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
    """The fixture must be a real diff: the prompt builder strips comments
    first, and a 50,000-character blob with no +/- markers is not a diff at
    all — it reports as "no code changed", not as a truncated one."""
    body = "--- a/A.sol\n+++ b/A.sol\n@@ -1,9999 +1,9999 @@\n" + \
           "\n".join(f"+    uint256 x{i} = {i};" for i in range(2_000))
    p = build_prompt(view(diff=body), diff_chars=200)
    assert "[truncated]" in p


def test_prompt_hides_comments_from_the_model():
    """A model that cannot see a comment cannot be fooled by one — the
    solmate "Comment fixes" failure."""
    body = ("--- a/A.sol\n+++ b/A.sol\n@@ -1,3 +1,3 @@\n"
            "     mstore(add(callData, 36), amount)\n"
            "-    // We use 100 because the calldata length is 4 + 32 * 2.\n"
            "+    // We use 68 because the calldata length is 4 + 32 * 2.\n"
            "     let s := call(gas(), token, 0, callData, 68, 0, 0)\n")
    p = build_prompt(view(diff=body))
    assert "We use 100" not in p and "We use 68" not in p
    assert "no code changed" in p
    assert "comments removed" in p


def test_prompt_passes_signals_through():
    p = build_prompt(view(advisory_ids="CVE-2024-1", is_backport=True))
    assert "CVE-2024-1" in p
    assert "backported" in p
    assert "hints, not conclusions" in p


def test_prompt_says_signals_absent_is_normal():
    assert "common for silent fixes" in build_prompt(view())


def test_a_failed_verdict_says_why():
    """Undecided with a blank rationale cannot be triaged: an endpoint that
    timed out and a model that declined look identical."""
    from ossbugfix.classify.base import Verdict
    row = Verdict(classifier="llm:openai", error="URLError: timed out").as_row()
    assert row["is_bugfix"] is None
    assert "not classified" in row["rationale"] and "timed out" in row["rationale"]
    assert "error" not in row


def test_a_real_rationale_is_not_overwritten_by_an_error():
    from ossbugfix.classify.base import Verdict
    row = Verdict(rationale="model said no", error="late warning").as_row()
    assert row["rationale"] == "model said no"


def test_json_is_recovered_from_a_reasoning_channel():
    """A reasoning model can leave `content` empty and put the answer in its
    own channel. Throwing that call away cost a third of one sweep."""
    text = ("We need to decide. The diff adds a bounds check.\n"
            '{"is_bugfix": true, "is_security_fix": true, "confidence": 0.8}')
    obj = _extract_json(text)
    assert obj is not None and obj["is_bugfix"] is True
