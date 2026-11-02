"""Incremental collection: a second run must cost nothing for work already done,
and must never quietly mix rows produced under different rules."""
from __future__ import annotations

import pandas as pd
import pytest

from ossbugfix import paths, pipeline, schema, walk
from ossbugfix.state import Stage, StageState


def frame(domain, slug):
    return pd.read_parquet(paths.commits_dir(domain) / f"{slug}.parquet")


def test_second_walk_collects_only_what_landed_since(mirrored, commit_to_source):
    d = mirrored.domain
    first = pipeline.run_walk(d, [mirrored], workers=1, quiet=True)
    before = frame(d, mirrored.slug)
    assert first.rows == len(before)

    commit_to_source("fix out-of-bounds read in parser")
    second = pipeline.run_walk(d, [mirrored], workers=1, quiet=True)

    after = frame(d, mirrored.slug)
    assert second.rows == 1, "only the new commit should be collected"
    assert len(after) == len(before) + 1
    # every previously collected row survives, unchanged
    assert set(before["sha"]) < set(after["sha"])
    assert "fix out-of-bounds read in parser" in set(after["subject"])


def test_third_walk_with_nothing_new_collects_nothing(mirrored, commit_to_source):
    d = mirrored.domain
    pipeline.run_walk(d, [mirrored], workers=1, quiet=True)
    commit_to_source("another fix")
    pipeline.run_walk(d, [mirrored], workers=1, quiet=True)
    n = len(frame(d, mirrored.slug))

    again = pipeline.run_walk(d, [mirrored], workers=1, quiet=True)
    assert again.rows == 0
    assert again.ok == 1, "a no-op walk still succeeds; it is not an error"
    assert len(frame(d, mirrored.slug)) == n


def test_tips_are_recorded_for_the_next_run(mirrored):
    d = mirrored.domain
    pipeline.run_walk(d, [mirrored], workers=1, quiet=True)
    entry = StageState(d, Stage.WALK).get(mirrored.slug)
    assert entry["tips"], "without tips the next walk cannot be incremental"
    assert all(len(t) == 40 for t in entry["tips"])
    assert entry["schema_version"] == schema.SCHEMA_VERSION
    assert entry["incremental"] is False, "the first walk is not incremental"


def test_changing_the_walk_shape_forces_a_full_rewalk(mirrored, commit_to_source):
    """Rows made with --line-counts and rows made without are not comparable,
    so appending the second kind to the first would corrupt the frame."""
    d = mirrored.domain
    pipeline.run_walk(d, [mirrored], workers=1, quiet=True)
    commit_to_source("fix overflow")

    rep = pipeline.run_walk(d, [mirrored], workers=1, quiet=True,
                            cfg=walk.WalkConfig(line_counts=True))
    assert "full re-walk" in rep.notes[0] if rep.notes else True
    df = frame(d, mirrored.slug)
    # a full re-walk under line_counts means EVERY row now has counts
    assert (df["insertions"].fillna(0) > 0).all()
    assert StageState(d, Stage.WALK).get(mirrored.slug)["incremental"] is False


def test_schema_bump_forces_a_full_rewalk(mirrored, monkeypatch):
    d = mirrored.domain
    pipeline.run_walk(d, [mirrored], workers=1, quiet=True)
    monkeypatch.setattr(schema, "SCHEMA_VERSION", "999")
    rep = pipeline.run_walk(d, [mirrored], workers=1, quiet=True)
    assert rep.rows == len(frame(d, mirrored.slug)), \
        "a schema bump must re-collect, not append across contracts"


def test_a_rewritten_history_does_not_abort_the_walk(mirrored):
    """`git log --not <missing sha>` is fatal. A force-push upstream must cost
    the incremental shortcut, never the repository."""
    d = mirrored.domain
    pipeline.run_walk(d, [mirrored], workers=1, quiet=True)
    st = StageState(d, Stage.WALK)
    entry = st.get(mirrored.slug)
    entry["tips"] = ["f" * 40]                   # a sha the mirror has never seen
    st.entries[mirrored.slug] = entry
    st.save()

    rep = pipeline.run_walk(d, [mirrored], workers=1, quiet=True)
    assert rep.failed == 0
    assert len(frame(d, mirrored.slug)) >= 3


def test_live_tips_filters_to_what_the_mirror_still_has(mirrored):
    from ossbugfix import mirror as mirror_mod
    path = mirror_mod.mirror_path(mirrored)
    real = walk.current_tips(path)
    assert real
    assert walk.live_tips(path, [*real, "f" * 40]) == real


def test_signals_rerun_after_an_incremental_walk(mirrored, commit_to_source):
    d = mirrored.domain
    pipeline.run_walk(d, [mirrored], workers=1, quiet=True)
    pipeline.run_signals(d, [mirrored], workers=1, quiet=True)
    commit_to_source("fix panic on empty input")
    pipeline.run_walk(d, [mirrored], workers=1, quiet=True)

    rep = pipeline.run_signals(d, [mirrored], workers=1, quiet=True)
    assert rep.skipped == 0, "new commits must not be left un-annotated"
    cand = pd.read_parquet(paths.candidates_dir(d) / f"{mirrored.slug}.parquet")
    row = cand[cand["subject"] == "fix panic on empty input"].iloc[0]
    assert "crash" in row["impact_class"]


def test_signals_are_skipped_when_nothing_moved(mirrored):
    d = mirrored.domain
    pipeline.run_walk(d, [mirrored], workers=1, quiet=True)
    pipeline.run_signals(d, [mirrored], workers=1, quiet=True)
    rep = pipeline.run_signals(d, [mirrored], workers=1, quiet=True)
    assert rep.skipped == 1 and rep.ok == 0


def test_classify_does_not_redo_already_classified_commits(mirrored, commit_to_source):
    from ossbugfix import classify
    from ossbugfix.sink import Sink

    d = mirrored.domain
    pipeline.run_walk(d, [mirrored], workers=1, quiet=True)
    pipeline.run_signals(d, [mirrored], workers=1, quiet=True)
    first = pipeline.run_classify(d, [mirrored], classify.get("heuristic"), quiet=True)
    assert first.rows > 0

    commit_to_source("fix integer overflow in length check")
    pipeline.run_walk(d, [mirrored], workers=1, quiet=True)
    pipeline.run_signals(d, [mirrored], workers=1, quiet=True)
    second = pipeline.run_classify(d, [mirrored], classify.get("heuristic"), quiet=True)

    assert second.rows == 1, "only the new commit should reach the classifier"
    df = Sink(d).read()
    assert len(df) == first.rows + 1


def test_a_failed_classification_is_retried_but_a_declined_one_is_not(mirrored):
    """Both record is_bugfix=None, and the difference decides whether a rerun
    owes them work. 2,375 rows that died on a token limit became permanently
    unclassifiable because every rerun skipped them: something had once tried.
    """
    from ossbugfix.classify.base import Verdict
    from ossbugfix.sink import Sink

    d = mirrored.domain
    pipeline.run_walk(d, [mirrored], workers=1, quiet=True)
    pipeline.run_signals(d, [mirrored], workers=1, quiet=True)

    sink = Sink(d)
    frame = pd.read_parquet(paths.candidates_dir(d) / f"{mirrored.slug}.parquet")
    rows = schema.conform(frame, schema.BUGFIX_COLUMNS)
    # One row the engine declined, one the endpoint never answered for.
    rows.loc[rows.index[0], ["classifier", "rationale"]] = \
        ["llm:openai", "no deterministic signal fired"]
    rows.loc[rows.index[1:], ["classifier", "rationale"]] = \
        ["llm:openai", Verdict(error="HTTP 403: error code: 1010").as_row()["rationale"]]
    sink.merge(rows)

    from ossbugfix import classify as classify_mod
    rep = pipeline.run_classify(d, [mirrored], classify_mod.get("heuristic"),
                                quiet=True)
    assert rep.rows == len(rows) - 1, \
        "the declined row is a result; every failed row is still owed"
