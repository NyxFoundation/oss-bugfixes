"""Migration resilience: a table written months ago by older code must still
load, still merge, and never lose a column nobody told the schema about."""
from __future__ import annotations

import json

import pandas as pd
import pytest

from ossbugfix import manifest, paths, schema
from ossbugfix.sink import Sink


def legacy_csv(path, **extra):
    """A table as an older version would have left it: a subset of today's
    columns, plus whatever somebody added by hand."""
    row = {"id": "d/s@" + "a" * 12, "domain": "d", "repo_slug": "s",
           "repo": "o/r", "sha": "a" * 40, "subject": "fix oob read",
           "is_bugfix": "True", "classifier": "heuristic"}
    row.update(extra)
    pd.DataFrame([row]).to_csv(path, index=False)
    return path


def test_a_table_missing_todays_columns_still_loads(tmp_path):
    sink = Sink("d", legacy_csv(tmp_path / "bugfixes.csv"))
    df = sink.read()
    assert len(df) == 1
    for col in schema.BUGFIX_COLUMNS:
        assert col in df.columns
    assert df.iloc[0]["is_bugfix"] is True or df.iloc[0]["is_bugfix"] == True  # noqa: E712
    assert pd.isna(df.iloc[0]["cwe"]) or df.iloc[0]["cwe"] == ""


def test_columns_the_schema_does_not_know_are_kept(tmp_path):
    """A column somebody added by hand is data. Dropping it on the next merge
    would be the pipeline quietly destroying work it did not understand."""
    p = legacy_csv(tmp_path / "bugfixes.csv", reviewer_note="checked by hand",
                   ticket="SEC-412")
    sink = Sink("d", p)
    df = sink.read()
    assert df.iloc[0]["reviewer_note"] == "checked by hand"

    sink.merge(df)                      # a round trip must not lose them
    again = sink.read()
    assert again.iloc[0]["reviewer_note"] == "checked by hand"
    assert again.iloc[0]["ticket"] == "SEC-412"


def test_merging_new_rows_into_a_legacy_table_preserves_both(tmp_path):
    p = legacy_csv(tmp_path / "bugfixes.csv", reviewer_note="keep me")
    sink = Sink("d", p)
    new = schema.conform(pd.DataFrame([{
        "domain": "d", "repo_slug": "s", "repo": "o/r", "sha": "b" * 40,
        "subject": "fix overflow", "classifier": "heuristic", "is_bugfix": True,
    }]), schema.BUGFIX_COLUMNS)

    stats = sink.merge(new)
    assert stats.added == 1
    out = sink.read()
    assert len(out) == 2
    old = out[out.sha == "a" * 40].iloc[0]
    assert old["reviewer_note"] == "keep me"
    assert pd.isna(out[out.sha == "b" * 40].iloc[0]["reviewer_note"])


def test_human_rows_survive_a_schema_era_change(tmp_path):
    p = legacy_csv(tmp_path / "bugfixes.csv", classifier="human",
                   rationale="confirmed exploitable")
    sink = Sink("d", p)
    sink.merge(schema.conform(pd.DataFrame([{
        "domain": "d", "repo_slug": "s", "repo": "o/r", "sha": "a" * 40,
        "subject": "fix oob read", "classifier": "llm:claude",
        "is_bugfix": False, "rationale": "model disagrees",
    }]), schema.BUGFIX_COLUMNS))
    kept = sink.read().iloc[0]
    assert kept["classifier"] == "human"
    assert kept["rationale"] == "confirmed exploitable"


def test_conform_is_idempotent(tmp_path):
    df = Sink("d", legacy_csv(tmp_path / "bugfixes.csv")).read()
    once = schema.conform(df, schema.BUGFIX_COLUMNS, keep_extra=True)
    twice = schema.conform(once, schema.BUGFIX_COLUMNS, keep_extra=True)
    pd.testing.assert_frame_equal(once, twice)


def test_garbage_in_a_typed_column_becomes_na_not_an_exception(tmp_path):
    p = legacy_csv(tmp_path / "bugfixes.csv", confidence="very high",
                   n_files="lots", is_security_fix="maybe")
    df = Sink("d", p).read()
    assert pd.isna(df.iloc[0]["confidence"])
    assert pd.isna(df.iloc[0]["n_files"])
    assert pd.isna(df.iloc[0]["is_security_fix"]), \
        "an unparseable boolean is undecided, never False"


def test_plan_migration_is_quiet_on_a_current_table(tmp_path, monkeypatch):
    root = tmp_path / "root"
    (root / "domains" / "d" / "data").mkdir(parents=True)
    monkeypatch.setenv("OSS_BUGFIXES_ROOT", str(root))
    sink = Sink("d")
    sink.merge(schema.conform(pd.DataFrame([{
        "domain": "d", "repo_slug": "s", "repo": "o/r", "sha": "a" * 40,
        "classifier": "heuristic"}]), schema.BUGFIX_COLUMNS))
    manifest.write("d", sink.read())
    assert manifest.plan_migration("d", sink.read()) == []


def test_plan_migration_names_what_would_change(tmp_path, monkeypatch):
    root = tmp_path / "root"
    (root / "domains" / "d" / "data").mkdir(parents=True)
    monkeypatch.setenv("OSS_BUGFIXES_ROOT", str(root))
    legacy_csv(root / "domains" / "d" / "data" / "bugfixes.csv", note="x")
    notes = " ".join(manifest.plan_migration("d", Sink("d").read()))
    assert "no manifest" in notes
    assert "will be KEPT" in notes, "an unknown column must be reported, not dropped"


def test_manifest_records_provenance(tmp_path, monkeypatch):
    root = tmp_path / "root"
    (root / "domains" / "d" / "data").mkdir(parents=True)
    monkeypatch.setenv("OSS_BUGFIXES_ROOT", str(root))
    sink = Sink("d")
    sink.merge(schema.conform(pd.DataFrame([
        {"domain": "d", "repo_slug": "s", "repo": "o/r", "sha": "a" * 40,
         "classifier": "llm:claude", "is_bugfix": True, "is_security_fix": True},
        {"domain": "d", "repo_slug": "s", "repo": "o/r", "sha": "b" * 40,
         "classifier": "human", "is_bugfix": True, "is_security_fix": False},
    ]), schema.BUGFIX_COLUMNS))
    manifest.write("d", sink.read())
    m = manifest.read("d")
    assert m["rows"] == 2 and m["classified"] == 2
    assert m["bugfixes"] == 2 and m["security_fixes"] == 1
    assert m["by_classifier"] == {"llm:claude": 1, "human": 1}
    assert m["schema_version"] == schema.SCHEMA_VERSION
