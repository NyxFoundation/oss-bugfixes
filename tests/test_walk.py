"""The walk is where a mistake is most expensive: a commit dropped here is
invisible to every later stage, and nothing downstream can notice it is gone."""
from __future__ import annotations

import json

from ossbugfix import walk


def test_walks_whole_history_and_drops_only_structurally(mirrored):
    res = walk.walk(mirrored, walk.WalkConfig(detect_backports=False))
    assert res.ok, res.error
    subjects = res.frame["subject"].tolist()

    # The fix and the two source-touching commits are kept...
    assert any(s.startswith("fix null pointer") for s in subjects)
    assert "style: reformat" in subjects, \
        "a reformat is not a structural drop — only the classifier may reject it"
    # ...and the docs-only commit is dropped, because it cannot hold a defect.
    assert "docs: describe f" not in subjects
    assert res.dropped.get("no_source_file") == 1


def test_records_git_facts(mirrored):
    res = walk.walk(mirrored, walk.WalkConfig(detect_backports=False))
    row = res.frame[res.frame["subject"].str.startswith("fix null")].iloc[0]
    assert len(row["sha"]) == 40
    assert row["repo"] == "example/sample"
    assert row["domain"] == "testdom"
    assert json.loads(row["files_changed"]) == ["lib.c"]
    assert row["n_files"] == 1
    assert bool(row["touches_source"]) is True
    assert row["languages"] == "c"
    assert row["authored_at"].startswith("2024-01-01")
    assert "CVE-2024-12345" in row["body"]


def test_line_counts_are_zero_unless_asked_for(mirrored):
    """Default is `--name-only`: paths come from trees, which a blobless mirror
    already has, while line counts would force a blob fetch per commit."""
    res = walk.walk(mirrored, walk.WalkConfig(detect_backports=False))
    assert (res.frame["insertions"].fillna(0) == 0).all()

    counted = walk.walk(mirrored, walk.WalkConfig(detect_backports=False,
                                                  line_counts=True))
    row = counted.frame[counted.frame["subject"].str.startswith("fix null")].iloc[0]
    assert row["insertions"] >= 1 and row["deletions"] >= 1
    # Both shapes must agree on the files themselves.
    assert sorted(counted.frame["subject"]) == sorted(res.frame["subject"])


def test_body_with_newlines_does_not_break_the_parse(mirrored):
    res = walk.walk(mirrored, walk.WalkConfig(detect_backports=False))
    row = res.frame[res.frame["subject"].str.startswith("fix null")].iloc[0]
    assert "attacker could crash" in row["body"]
    assert row["subject"] == "fix null pointer dereference in f"


def test_max_files_cap_is_structural(mirrored):
    res = walk.walk(mirrored, walk.WalkConfig(max_files=0, detect_backports=False))
    # max_files=0 disables the cap rather than dropping everything.
    assert len(res.frame) == 3


def test_backport_column_is_na_when_detection_is_skipped(mirrored):
    res = walk.walk(mirrored, walk.WalkConfig(detect_backports=False))
    assert res.frame["is_backport"].isna().all(), \
        "skipped detection must never be recorded as False"
    assert res.backport_state == "not attempted"


def test_backport_detection_runs_when_asked(mirrored):
    res = walk.walk(mirrored, walk.WalkConfig(detect_backports=True))
    assert res.ok
    # One branch, no cherry-picks: every commit's patch-id is unique.
    assert (res.frame["n_patch_siblings"].fillna(1) == 1).all()
    assert not res.frame["is_backport"].fillna(False).any()


def test_limit_caps_rows(mirrored):
    res = walk.walk(mirrored, walk.WalkConfig(limit=1, detect_backports=False))
    assert len(res.frame) == 1


def test_missing_mirror_is_an_error_not_an_empty_frame(mirrored):
    from ossbugfix import mirror as mirror_mod
    import shutil
    shutil.rmtree(mirror_mod.mirror_path(mirrored))
    res = walk.walk(mirrored)
    assert not res.ok and "mirror" in res.error
