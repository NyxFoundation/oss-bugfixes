from __future__ import annotations

import pandas as pd

from ossbugfix import schema, signals


def frame(**over):
    row = {"subject": "", "body": "", "is_revert": False,
           "is_backport": pd.NA, "n_patch_siblings": pd.NA}
    row.update(over)
    return schema.conform(pd.DataFrame([row]), schema.COMMIT_COLUMNS)


def test_advisory_id_is_detected_and_normalised():
    out = signals.annotate(frame(subject="fix cve-2024-1234 in parser"))
    assert out.loc[0, "advisory_ids"] == "CVE-2024-1234"
    assert bool(out.loc[0, "has_advisory_id"]) is True


def test_fix_verb_alone_is_not_a_signal():
    """"fix typo" is a fix verb and is not a defect report — the whole reason
    the verb only counts alongside an impact class."""
    out = signals.annotate(frame(subject="fix typo in comment"))
    assert out.loc[0, "fix_verb"] == "fix"
    assert out.loc[0, "impact_class"] == ""
    assert out.loc[0, "n_signals"] == 0


def test_fix_verb_with_impact_counts():
    out = signals.annotate(frame(subject="fix panic when header is empty"))
    assert "crash" in out.loc[0, "impact_class"]
    assert out.loc[0, "n_signals"] >= 1


def test_keyword_score_counts_unique_terms_not_occurrences():
    once = signals.annotate(frame(body="an attacker can do this"))
    many = signals.annotate(frame(body="attacker attacker attacker attacker"))
    assert once.loc[0, "keyword_score"] == many.loc[0, "keyword_score"], \
        "repetition is one claim; counting it N times is how release notes " \
        "scored themselves to 1.0"


def test_annotate_does_not_drop_rows_by_default():
    out = signals.annotate(frame(subject="refactor the validation helper"))
    assert out.loc[0, "prefilter"] == "keep", \
        "the signal pass annotates; only an explicit policy may gate"
    assert out.loc[0, "n_signals"] == 0


def test_min_signals_marks_but_still_returns_the_row():
    out = signals.annotate(frame(subject="refactor"), min_signals=1)
    assert len(out) == 1
    assert out.loc[0, "prefilter"] == "drop:below_min_signals"


def test_empty_frame_is_conformed_not_crashed():
    out = signals.annotate(schema.empty(schema.COMMIT_COLUMNS))
    assert list(out.columns) == schema.COMMIT_COLUMNS + schema.SIGNAL_COLUMNS


def test_files_of_handles_both_shapes():
    assert signals.files_of({"files_changed": '["a.c"]'}) == ["a.c"]
    assert signals.files_of({"files_changed": ["a.c"]}) == ["a.c"]
    assert signals.files_of({"files_changed": "not json"}) == []
