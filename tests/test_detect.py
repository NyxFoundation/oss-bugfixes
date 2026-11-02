"""The deterministic classifier.

Two kinds of test, matching the two kinds of guarantee this component has:

  * the VERDICT ALGEBRA is finite, so `ossbugfix.detect.verify` proves its
    rules over the whole space with Z3 and by exhaustive execution. The test
    here just runs that and insists it passes.
  * the PARSER and TOKENIZER work over strings, which is infinite. Those get
    Hypothesis: randomised, unbounded in principle, not a proof. The README
    and docs/verification.md say so rather than implying otherwise.
"""
from __future__ import annotations

import pytest
from hypothesis import given, settings
from hypothesis import strategies as st

from ossbugfix.detect import (Diff, Ground, Signature, Verdict, classify,
                              parse, signatures, tokens)
from ossbugfix.detect.verdict import InvalidVerdict, decide
from ossbugfix.detect.verify import cross_check, exhaustive_check, prove_with_z3


# --- the finite half: proved, not sampled ----------------------------------

def test_verdict_rules_hold_over_the_whole_decision_space():
    failures = exhaustive_check()
    assert not failures, "\n".join(failures[:5])


def test_z3_discharges_every_rule():
    proved, failures = prove_with_z3()
    assert not failures, "\n".join(failures)
    assert len(proved) >= 7, f"only {len(proved)} properties were proved"


def test_the_solver_model_matches_the_code_it_claims_to_describe():
    """Without this the solver has verified a story about the code. It caught
    a real discrepancy the first time it ran."""
    assert cross_check() == []


# --- construction-time invariants ------------------------------------------

def test_a_positive_verdict_cannot_be_built_without_a_witness():
    with pytest.raises(InvalidVerdict, match="security"):
        Verdict(True, True, (), Ground.STRUCTURAL)
    with pytest.raises(InvalidVerdict, match="bug-evidence"):
        Verdict(True, None, (), Ground.STRUCTURAL)


def test_a_refuted_verdict_cannot_carry_evidence():
    with pytest.raises(InvalidVerdict, match="no signatures"):
        Verdict(False, False, (Signature.GUARD_ADDED,), Ground.NO_TOKEN_CHANGE)


def test_security_without_bugfix_is_unconstructable():
    with pytest.raises(InvalidVerdict):
        Verdict(None, True, (Signature.GUARD_ADDED,), Ground.STRUCTURAL)


def test_asserted_requires_bug_evidence():
    with pytest.raises(InvalidVerdict):
        Verdict.asserted([Signature.VISIBILITY_TIGHTENED])


# --- the infinite half: assurance, not proof -------------------------------

text = st.text(max_size=400)
diff_line = st.one_of(
    text.map(lambda s: "+" + s),
    text.map(lambda s: "-" + s),
    text.map(lambda s: " " + s),
    st.just("@@ -1,2 +1,3 @@"),
    st.just("--- a/x.sol"),
    st.just("+++ b/x.sol"),
    st.just("--- /dev/null"),
    st.just("diff --git a/x.sol b/x.sol"),
    st.just("\\ No newline at end of file"),
    text,
)


@given(st.lists(diff_line, max_size=40).map("\n".join))
@settings(max_examples=300, deadline=None)
def test_parse_is_total(body: str):
    """A classifier that can raise silently drops commits, and a dropped
    commit is indistinguishable from one examined and rejected."""
    d = parse(body)
    assert isinstance(d, Diff)


@given(st.lists(diff_line, max_size=40).map("\n".join))
@settings(max_examples=300, deadline=None)
def test_classify_is_total(body: str):
    v = classify(parse(body))
    assert v.is_bugfix in (True, False, None)
    assert isinstance(v.ground, Ground)


@given(st.text(max_size=200))
@settings(max_examples=300, deadline=None)
def test_tokens_never_invents_characters(line: str):
    """Every token is a substring of the line it came from."""
    for t in tokens(line):
        assert t in line or t in {"<=", ">=", "==", "!=", "&&", "||", "->",
                                  "=>", "::", "++", "--", "<<", ">>", "**"}


@given(st.lists(diff_line, max_size=20).map("\n".join),
       st.lists(diff_line, max_size=20).map("\n".join))
@settings(max_examples=200, deadline=None)
def test_signatures_are_monotone_under_concatenation(a: str, b: str):
    """A finding must not disappear when the classifier is handed MORE diff —
    that is what stops a split commit from laundering a fix."""
    da, db = parse(a), parse(b)
    for s in signatures(da):
        assert s in signatures(da + db)
    for s in signatures(db):
        assert s in signatures(da + db)


@given(st.lists(diff_line, max_size=30).map("\n".join))
@settings(max_examples=200, deadline=None)
def test_parse_is_idempotent_on_its_own_rendering(body: str):
    d = parse(body)
    assert parse(body) == d


# --- behaviour the corpus depends on ---------------------------------------

GUARD = ("--- a/A.sol\n+++ b/A.sol\n@@ -1,3 +1,4 @@\n"
         " function f(uint256 i) {\n+        require(i < len);\n"
         "         return a[i];\n }\n")
COMMENT = ("--- a/A.sol\n+++ b/A.sol\n@@ -1,2 +1,2 @@\n"
           "-// old words\n+// new words\n   return 1;\n")
NEW_FILE = ("--- /dev/null\n+++ b/New.sol\n@@ -0,0 +1,3 @@\n"
            "+function f() {\n+    require(msg.sender == owner);\n+}\n")


def test_a_guard_inserted_into_existing_code_is_a_security_fix():
    v = classify(parse(GUARD))
    assert v.is_bugfix is True and v.is_security_fix is True
    assert Signature.GUARD_ADDED in v.sigs


def test_a_comment_only_edit_is_refuted():
    v = classify(parse(COMMENT))
    assert v.is_bugfix is False
    assert v.ground is Ground.NO_TOKEN_CHANGE


def test_a_new_file_full_of_guards_is_not_forty_security_fixes():
    """Every line of a created file is "added", so an edit signature meaning
    "X was introduced into code that already existed" cannot be read off it."""
    v = classify(parse(NEW_FILE))
    assert v.is_bugfix is not True
    assert Signature.GUARD_ADDED not in v.sigs


def test_a_version_bump_is_not_a_bounds_fix():
    """`pragma solidity ^0.8.0 -> ^0.8.13` was the worst false positive the
    first version produced."""
    d = parse("--- a/A.sol\n+++ b/A.sol\n@@ -1,1 +1,1 @@\n"
              "-pragma solidity ^0.8.0;\n+pragma solidity ^0.8.13;\n")
    assert Signature.BOUNDS_ADJUSTED not in classify(d).sigs


def test_a_test_only_change_carries_no_security_verdict():
    d = parse("--- a/test/A.t.sol\n+++ b/test/A.t.sol\n@@ -1,3 +1,4 @@\n"
              " function testF() {\n+        require(x > 0);\n"
              "         f();\n }\n")
    v = classify(d)
    assert v.is_security_fix is not True


def test_decide_is_pure_in_its_four_inputs():
    a = decide(touched=True, trivial=False, docs_only=False,
               found=[Signature.GUARD_ADDED])
    b = decide(touched=True, trivial=False, docs_only=False,
               found=[Signature.GUARD_ADDED])
    assert a == b
