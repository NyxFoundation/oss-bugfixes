from __future__ import annotations

from ossbugfix import diffs, walk


def test_diff_is_served_from_the_mirror_and_cached(mirrored):
    res = walk.walk(mirrored, walk.WalkConfig(detect_backports=False))
    sha = res.frame[res.frame["subject"].str.startswith("fix null")].iloc[0]["sha"]
    cache = diffs.DiffCache(mirrored.domain)

    text = diffs.get_diff(mirrored, sha, cache=cache)
    assert text and "lib.c" in text and "+" in text
    assert cache.has(mirrored.slug, sha)
    assert cache.get(mirrored.slug, sha) == text


def test_unknown_sha_is_none_not_empty_string(mirrored):
    assert diffs.get_diff(mirrored, "0" * 40) is None, \
        "None means 'could not be produced'; '' would read as 'empty diff'"


def test_truncate_says_how_much_it_dropped():
    out = diffs.truncate("line\n" * 1000, cap=50)
    assert "diff truncated" in out and "characters not shown" in out
    assert len(out) < 300


def test_truncate_is_a_noop_under_the_cap():
    assert diffs.truncate("short", cap=100) == "short"
    assert diffs.truncate("short", cap=None) == "short"


def test_corrupt_cache_entry_is_discarded_not_served(mirrored, tmp_path):
    cache = diffs.DiffCache(mirrored.domain)
    p = cache.path_for(mirrored.slug, "c" * 40)
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_bytes(b"not gzip at all")
    assert cache.get(mirrored.slug, "c" * 40) is None
    assert not p.exists()
