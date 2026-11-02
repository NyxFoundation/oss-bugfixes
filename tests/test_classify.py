from __future__ import annotations

import pytest

from ossbugfix import classify
from ossbugfix.classify.base import CommitView
from ossbugfix.classify.llm import _extract_json, build_prompt


def view(**over):
    base = dict(id="x", repo="o/r", sha="a" * 40, url="u", subject="s", body="",
                files=["a.c"], insertions=1, deletions=1)
    base.update(over)
    return CommitView(**base)


def test_engine_lookup_and_colon_backend():
    assert "heuristic" in classify.available()
    with pytest.raises(classify.ClassifyError, match="unknown engine"):
        classify.get("nope")


def test_heuristic_declines_when_nothing_fires():
    v = classify.get("heuristic").classify(view(subject="refactor helper"))
    assert v.is_bugfix is None, "no signal is 'undecided', not 'not a bug fix'"
    assert "diff was not read" in v.rationale


def test_heuristic_calls_an_advisory_a_security_fix():
    v = classify.get("heuristic").classify(
        view(subject="fix oob read", advisory_ids="CVE-2024-1", impact_class="memory"))
    assert v.is_bugfix and v.is_security_fix
    assert v.confidence <= 0.8, "a message-only engine is never entitled to certainty"


def test_revert_alone_is_not_called_a_bug_fix():
    """A revert says a change had to come out, not that there was a defect."""
    v = classify.get("heuristic").classify(
        view(subject='Revert "Improve hmac test"', is_revert=True))
    assert v.is_bugfix is False
    assert "revert" in v.rationale


def test_extract_json_survives_prose_and_fences():
    assert _extract_json('{"is_bugfix": true}')["is_bugfix"] is True
    assert _extract_json('```json\n{"is_bugfix": false}\n```')["is_bugfix"] is False
    assert _extract_json('Sure!\n{"is_bugfix": true}\nHope that helps')["is_bugfix"] is True
    assert _extract_json("no json at all") is None


def test_prompt_states_when_the_diff_is_missing():
    p = build_prompt(view(diff=""))
    assert "diff unavailable" in p


def test_prompt_marks_truncation():
    p = build_prompt(view(diff="x" * 50_000), diff_chars=100)
    assert "[truncated]" in p


def test_prompt_passes_signals_through():
    p = build_prompt(view(advisory_ids="CVE-2024-1", is_backport=True))
    assert "CVE-2024-1" in p
    assert "backported" in p
    assert "hints, not conclusions" in p


def test_prompt_says_signals_absent_is_normal():
    assert "common for silent fixes" in build_prompt(view())
