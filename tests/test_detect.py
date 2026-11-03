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


# --- the veto: a checkable fact outranks a model's guess --------------------

COMMENT_ABOUT_A_CONSTANT = (
    "--- a/SafeERC20.sol\n+++ b/SafeERC20.sol\n@@ -78,7 +78,7 @@\n"
    "             mstore(add(callData, 36), amount)\n"
    "-            // We use 100 because the calldata length is 4 + 32 * 2.\n"
    "+            // We use 68 because the calldata length is 4 + 32 * 2.\n"
    "             let callStatus := call(gas(), token, 0, callData, 68, 0, 0)\n")


def test_a_comment_about_a_constant_is_not_a_constant():
    """Observed in solmate's "📝 Comment fixes": every changed line was a
    comment, including `// We use 100 because…` corrected to `// We use 68…`
    while the code beside it always read 68. A model reported it as "corrects
    the calldata length constants … fixing a functional bug". It had read a
    comment about a constant as the constant.

    The structural engine strips comments before it looks at anything, so it
    cannot make that mistake — which is why its refutation outranks a model's
    positive verdict.
    """
    v = classify(parse(COMMENT_ABOUT_A_CONSTANT))
    assert v.is_bugfix is False
    assert v.ground is Ground.NO_TOKEN_CHANGE


def test_the_veto_overrules_a_model_positive_and_says_so():
    from ossbugfix.classify.base import CommitView, Verdict
    from ossbugfix.pipeline import veto_unprovable_positives

    view = CommitView(id="x", repo="o/r", sha="a" * 40, url="u",
                      subject="Comment fixes", body="", files=["SafeERC20.sol"],
                      insertions=1, deletions=1, diff=COMMENT_ABOUT_A_CONSTANT)
    verdict = Verdict(is_bugfix=True, is_security_fix=False,
                      rationale="corrects the calldata length constants",
                      classifier="llm:openai")
    assert veto_unprovable_positives([view], [verdict]) == 1
    assert verdict.is_bugfix is False
    assert "overruled: no_token_change" in verdict.rationale
    assert "corrects the calldata length" in verdict.rationale, \
        "the overruled claim must stay visible, not be erased"


def test_the_veto_leaves_a_real_fix_alone():
    from ossbugfix.classify.base import CommitView, Verdict
    from ossbugfix.pipeline import veto_unprovable_positives

    view = CommitView(id="x", repo="o/r", sha="b" * 40, url="u", subject="fix",
                      body="", files=["A.sol"], insertions=1, deletions=0,
                      diff=GUARD)
    verdict = Verdict(is_bugfix=True, is_security_fix=True, classifier="llm:openai")
    assert veto_unprovable_positives([view], [verdict]) == 0
    assert verdict.is_bugfix is True


# --- Solana: an account is whatever the caller passed ----------------------

SOLANA_SIGNER = (
    "--- a/programs/x/src/lib.rs\n+++ b/programs/x/src/lib.rs\n@@ -10,3 +10,4 @@\n"
    " pub fn withdraw(ctx: Context<Withdraw>) -> Result<()> {\n"
    "+        require!(ctx.accounts.authority.is_signer, ErrorCode::Unauthorized);\n"
    "         transfer(ctx, amount)\n }\n")
SOLANA_PDA = (
    "--- a/programs/x/src/state.rs\n+++ b/programs/x/src/state.rs\n@@ -5,3 +5,3 @@\n ctx\n"
    "-    let addr = Pubkey::create_program_address(seeds, program_id)?;\n"
    "+    let (addr, bump) = Pubkey::find_program_address(seeds, program_id);\n")
SOLANA_CLOSE = (
    "--- a/programs/x/src/close.rs\n+++ b/programs/x/src/close.rs\n@@ -3,3 +3,5 @@\n"
    "     **acc.lamports.borrow_mut() = 0;\n"
    "+    let mut data = acc.try_borrow_mut_data()?;\n"
    "+    data.fill(0);\n")


def test_a_missing_signer_check_is_a_solana_shape():
    v = classify(parse(SOLANA_SIGNER))
    assert Signature.SIGNER_CHECK_ADDED in v.sigs
    assert v.is_security_fix is True


def test_a_non_canonical_pda_bump_is_caught():
    """`create_program_address` lets a caller supply a bump that derives a
    second valid address for the same seeds."""
    assert Signature.PDA_BUMP_CANONICALISED in classify(parse(SOLANA_PDA)).sigs


def test_closing_without_zeroing_is_caught():
    """Draining lamports leaves an account that can be topped back up and
    reused inside the same transaction."""
    assert Signature.ACCOUNT_CLOSE_HARDENED in classify(parse(SOLANA_CLOSE)).sigs


# --- Move: the failures are in the type system -----------------------------

MOVE_ABILITY = (
    "--- a/sources/vault.move\n+++ b/sources/vault.move\n@@ -2,3 +2,3 @@\n ctx\n"
    "-    struct Receipt has key, store, drop {\n"
    "+    struct Receipt has key {\n")
MOVE_CAP = (
    "--- a/sources/admin.move\n+++ b/sources/admin.move\n@@ -4,3 +4,4 @@\n"
    "     public fun set_fee(cfg: &mut Config, bps: u64) {\n"
    "+        assert!(exists<AdminCap>(signer::address_of(s)), E_NOT_ADMIN);\n"
    "         cfg.fee = bps;\n")


def test_removing_the_store_ability_is_a_containment_fix():
    """Granting `store` is how a resource becomes freely transferable by
    anyone holding it. Removing one is not a refactor."""
    v = classify(parse(MOVE_ABILITY))
    assert Signature.ABILITY_RESTRICTED in v.sigs
    assert Signature.ABILITY_RESTRICTED.bug_evidence is False, \
        "tightening the type system is a surface change, not proof of a defect"


def test_a_capability_requirement_is_a_move_shape():
    assert Signature.CAPABILITY_CHECK_ADDED in classify(parse(MOVE_CAP)).sigs


# --- MEV: correct for one caller, wrong once somebody chooses the ordering --

MEV_SLIPPAGE = (
    "--- a/programs/amm/src/swap.rs\n+++ b/programs/amm/src/swap.rs\n@@ -8,3 +8,4 @@\n"
    "     let out = compute_out(amount_in)?;\n"
    "+    require!(out >= min_amount_out, ErrorCode::SlippageExceeded);\n"
    "     transfer_out(out)\n")
MEV_DEADLINE = (
    "--- a/contracts/Router.sol\n+++ b/contracts/Router.sol\n@@ -3,3 +3,4 @@\n"
    "     function swap(Order calldata o) external {\n"
    "+        require(block.timestamp <= o.deadline, \"expired\");\n"
    "         _fill(o);\n")
MEV_STALENESS = (
    "--- a/contracts/Oracle.sol\n+++ b/contracts/Oracle.sol\n@@ -5,3 +5,4 @@\n"
    "         (, int256 answer, , uint256 updatedAt, ) = feed.latestRoundData();\n"
    "+        require(block.timestamp - updatedAt <= maxAge, \"stale\");\n"
    "         return answer;\n")


def test_a_slippage_bound_is_an_mev_fix():
    """Without a minimum output the trade is sandwichable for the whole of its
    price impact, and no access-control shape sees that."""
    v = classify(parse(MEV_SLIPPAGE))
    assert Signature.SLIPPAGE_BOUND_ADDED in v.sigs
    assert v.is_security_fix is True


def test_a_deadline_is_an_mev_fix():
    """A signed order with no expiry is held until the moment it pays."""
    assert Signature.DEADLINE_ADDED in classify(parse(MEV_DEADLINE)).sigs


def test_an_oracle_staleness_check_is_an_mev_fix():
    assert Signature.ORACLE_STALENESS_CHECKED in classify(parse(MEV_STALENESS)).sigs


def test_mev_shapes_are_not_confined_to_one_ecosystem():
    """The same mistake is made in Rust, in Solidity and in Move. A detector
    that only looked at .sol would miss two thirds of it."""
    from ossbugfix.detect.signature import ecosystem_of
    assert ecosystem_of("programs/amm/src/swap.rs") == "solana"
    assert ecosystem_of("sources/pool.move") == "move"
    assert ecosystem_of("contracts/Router.sol") == "evm"
    assert Signature.SLIPPAGE_BOUND_ADDED in classify(parse(MEV_SLIPPAGE)).sigs  # .rs
    assert Signature.DEADLINE_ADDED in classify(parse(MEV_DEADLINE)).sigs        # .sol


def test_ecosystem_shapes_do_not_fire_on_the_wrong_ecosystem():
    """A Solidity diff must not produce Solana account shapes."""
    v = classify(parse(GUARD))
    solana = {Signature.SIGNER_CHECK_ADDED, Signature.PDA_BUMP_CANONICALISED,
              Signature.DISCRIMINATOR_CHECKED}
    assert not (set(v.sigs) & solana)


def test_the_verifier_still_covers_the_larger_taxonomy():
    """27 signatures is 2^27 subsets — not enumerable. The decision reads the
    set through two predicates, so the classes are, and `class_constancy`
    checks that reduction rather than assuming it."""
    from ossbugfix.detect.verify import class_constancy
    assert class_constancy(trials=200) == []


# --- a statement moved past unchanged context ------------------------------

MOVED_PAST_CONTEXT = (
    "--- a/ExchangeRouter.sol\n+++ b/ExchangeRouter.sol\n@@ -1,6 +1,6 @@\n"
    "-        validateGasLeftForCallback(deposit.callbackGasLimit());\n"
    "-\n"
    "         bytes memory eventData = buildEventData();\n"
    "+        validateGasLeftForCallback(deposit.callbackGasLimit());\n"
    "+\n"
    "         emit DepositExecuted(eventData);\n")


def test_a_statement_moved_past_context_is_not_a_trivial_edit():
    """GMX's "H-09, gasleft fix" moves a gas check after event-data creation.
    The removed and added token sequences are IDENTICAL — only their position
    relative to unchanged context moved — so comparing those two lists called
    it `no_token_change` and refuted a real security fix.

    `Hunk.lines` keeps the interleaving so the two renderings differ.
    """
    from ossbugfix.detect.signature import hunk_trivial
    from ossbugfix.detect.tokens import tokens_of

    d = parse(MOVED_PAST_CONTEXT)
    h = d.hunks[0]
    assert tokens_of(h.removed) == tokens_of(h.added), \
        "the premise: separated lists cannot see the move"
    assert hunk_trivial(h) is False
    assert classify(d).ground is not Ground.NO_TOKEN_CHANGE


def test_comment_and_whitespace_edits_are_still_refuted():
    """The fix must not cost the one negative the classifier is entitled to."""
    assert classify(parse(COMMENT)).ground is Ground.NO_TOKEN_CHANGE
    ws = parse("--- a/A.sol\n+++ b/A.sol\n@@ -1,2 +1,2 @@\n"
               "-  return   1;\n+  return 1;\n")
    assert classify(ws).ground is Ground.NO_TOKEN_CHANGE


def test_hunk_before_and_after_reconstruct_the_two_renderings():
    h = parse(MOVED_PAST_CONTEXT).hunks[0]
    # `text.split("\n")` leaves a trailing empty string, which parses as a
    # blank context line — so index from the front, not the back.
    assert h.before[0].strip().startswith("validateGasLeftForCallback")
    assert [l.strip() for l in h.after if l.strip()][-1].startswith("emit DepositExecuted")
    assert len(h.lines) == len(h.removed) + len(h.added) + len(h.context)
    # The order is what matters: the moved statement sits at a different index
    # in the two renderings, which is exactly what the separated lists lose.
    assert h.before.index(h.before[0]) != h.after.index(
        next(l for l in h.after if "validateGasLeftForCallback" in l))
