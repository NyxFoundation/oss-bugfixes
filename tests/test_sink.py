"""The sink holds the only artifact a human has edited by hand, so its
merge rules matter more than its performance."""
from __future__ import annotations

import pandas as pd

from ossbugfix import schema
from ossbugfix.sink import Sink


def row(sha="a" * 40, **over):
    base = {"domain": "d", "repo_slug": "s", "repo": "o/r", "sha": sha,
            "subject": "fix thing", "authored_at": "2024-01-01T00:00:00+00:00",
            "classifier": "heuristic", "is_bugfix": True}
    base.update(over)
    return schema.conform(pd.DataFrame([base]), schema.BUGFIX_COLUMNS)


def test_first_write_fills_id_and_url(tmp_path):
    sink = Sink("d", tmp_path / "bugfixes.csv")
    stats = sink.merge(row())
    assert stats.added == 1
    got = sink.read().iloc[0]
    assert got["id"] == "d/s@" + "a" * 12
    assert got["url"] == "https://github.com/o/r/commit/" + "a" * 40


def test_rerun_updates_in_place_rather_than_duplicating(tmp_path):
    sink = Sink("d", tmp_path / "bugfixes.csv")
    sink.merge(row(rationale="first"))
    stats = sink.merge(row(rationale="second"))
    assert (stats.added, stats.updated) == (0, 1)
    df = sink.read()
    assert len(df) == 1
    assert df.iloc[0]["rationale"] == "second"


def test_other_rows_survive_a_partial_rerun(tmp_path):
    sink = Sink("d", tmp_path / "bugfixes.csv")
    sink.merge(row(sha="a" * 40))
    sink.merge(row(sha="b" * 40))
    sink.merge(row(sha="a" * 40, rationale="redone"))
    assert len(sink.read()) == 2


def test_human_labels_are_not_overwritten_by_a_model(tmp_path):
    sink = Sink("d", tmp_path / "bugfixes.csv")
    sink.merge(row(classifier="human", is_security_fix=True, rationale="checked"))
    stats = sink.merge(row(classifier="llm:claude", is_security_fix=False,
                           rationale="model says no"))
    assert stats.protected == 1
    kept = sink.read().iloc[0]
    assert kept["classifier"] == "human"
    assert kept["rationale"] == "checked"


def test_overwrite_human_is_possible_when_asked(tmp_path):
    sink = Sink("d", tmp_path / "bugfixes.csv")
    sink.merge(row(classifier="human", rationale="checked"))
    sink.merge(row(classifier="llm:claude", rationale="model"), overwrite_human=True)
    assert sink.read().iloc[0]["classifier"] == "llm:claude"


def test_duplicate_keys_inside_one_batch_keep_the_last(tmp_path):
    sink = Sink("d", tmp_path / "bugfixes.csv")
    batch = pd.concat([row(rationale="old"), row(rationale="new")], ignore_index=True)
    sink.merge(schema.conform(batch, schema.BUGFIX_COLUMNS))
    df = sink.read()
    assert len(df) == 1 and df.iloc[0]["rationale"] == "new"


def test_round_trip_preserves_tri_state_booleans(tmp_path):
    sink = Sink("d", tmp_path / "bugfixes.csv")
    sink.merge(row(is_bugfix=None, is_security_fix=False))
    got = sink.read().iloc[0]
    assert pd.isna(got["is_bugfix"]), "a declined verdict must not become False"
    assert got["is_security_fix"] is False or got["is_security_fix"] == False  # noqa: E712


def test_concurrent_merges_do_not_lose_rows(tmp_path):
    """Two classifier runs over one domain must not clobber each other.

    Atomic rename is not enough on its own: each process reads, merges into
    what it read, and the second rename discards the first one's rows. This is
    the regression test for 178 model-labelled rows lost exactly that way.
    """
    import multiprocessing as mp

    path = tmp_path / "bugfixes.csv"
    Sink("d", path).merge(row(sha="0" * 40))        # create the table

    def writer(tag: str, start: int, n: int) -> None:
        s = Sink("d", path)
        for i in range(n):
            s.merge(row(sha=f"{tag}{i:039d}", classifier=tag))

    procs = [mp.Process(target=writer, args=("a", 0, 12)),
             mp.Process(target=writer, args=("b", 0, 12))]
    for p in procs:
        p.start()
    for p in procs:
        p.join(timeout=120)

    df = Sink("d", path).read()
    assert len(df) == 25, f"rows were lost to a concurrent write: {len(df)}"
    assert (df["classifier"] == "a").sum() == 12
    assert (df["classifier"] == "b").sum() == 12
