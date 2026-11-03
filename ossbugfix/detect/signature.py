"""signature.py — the structural edit shapes that constitute evidence.

A detector answers one question about a hunk, from tokens alone, with no model
and no prose. The design rule: a detector may only fire on something an author
DID to the code, never on something they SAID about it. The premise of this
corpus is that the commit message is unreliable, and a detector keyed on
wording would reintroduce exactly the blind spot the pipeline removes.

Each signature carries two gradings, kept separate because most shapes are bug
evidence and only some are security evidence — tightening a visibility
modifier is a real change to the contract's surface and is not by itself a
defect fix.
"""
from __future__ import annotations

import enum
from typing import Callable, Final, Sequence

from .diff import Diff, Hunk
from .tokens import (Tok, introduced, mentions, mentions_any, removed_toks,
                     tokens, tokens_of)

__all__ = ["Signature", "detect", "signatures", "distinct_signatures",
           "semantically_trivial", "only_docs", "hunk_trivial"]


class Signature(enum.Enum):
    """A structural edit shape. The value is the name used in output."""

    #: A `require` / `assert` / `revert` / error return appeared where there was
    #: none. The author decided an input they previously accepted must be
    #: rejected.
    GUARD_ADDED = "guard_added"
    #: A comparison operator changed strictness (`<` ↔ `<=`, `==` ↔ `!=`).
    COMPARISON_TIGHTENED = "comparison_tightened"
    #: A zero-address / null check appeared.
    ZERO_CHECK_ADDED = "zero_check_added"
    #: A call's success flag started being checked.
    RETURN_VALUE_CHECKED = "return_value_checked"
    #: An index or bound gained or lost a `± 1`, or a literal bound changed.
    BOUNDS_ADJUSTED = "bounds_adjusted"
    #: `unchecked` removed, a checked-arithmetic helper introduced, or an
    #: integer type widened.
    OVERFLOW_GUARDED = "overflow_guarded"
    #: The same statements, reordered around an external call — the shape of a
    #: check-effects-interaction fix.
    ORDERING_CHANGED = "ordering_changed"
    #: `public` became `internal` / `private` / `external`.
    VISIBILITY_TIGHTENED = "visibility_tightened"
    #: An authorisation modifier or an `msg.sender` check appeared.
    ACCESS_CONTROL_ADDED = "access_control_added"
    #: A reentrancy guard appeared.
    REENTRANCY_GUARD_ADDED = "reentrancy_guard_added"

    # ---- Solana: the account model, not the call stack --------------------
    # A Solana program is handed accounts by the caller and must prove each one
    # is what it claims. Nothing in the language enforces that, so the whole
    # class of "forgot to check" has no EVM analogue.

    #: A `Signer<'_>` / `is_signer` requirement appeared where the account was
    #: previously taken unauthenticated.
    SIGNER_CHECK_ADDED = "signer_check_added"
    #: An owner check appeared — the account must belong to the expected
    #: program. Without it a caller substitutes an account they control.
    OWNER_CHECK_ADDED = "owner_check_added"
    #: An Anchor `#[account(...)]` constraint appeared: `has_one`, `constraint`,
    #: `seeds`, `address`.
    ACCOUNT_CONSTRAINT_ADDED = "account_constraint_added"
    #: A PDA derivation gained its canonical bump, or moved off
    #: `create_program_address` onto `find_program_address`. A non-canonical
    #: bump lets a caller derive a second valid address for the same seeds.
    PDA_BUMP_CANONICALISED = "pda_bump_canonicalised"
    #: The target of a CPI started being checked against an expected program
    #: id, closing arbitrary-CPI.
    CPI_TARGET_CHECKED = "cpi_target_checked"
    #: Account closing was hardened — lamports drained AND data zeroed or the
    #: discriminator poisoned, so the account cannot be revived in the same
    #: transaction.
    ACCOUNT_CLOSE_HARDENED = "account_close_hardened"
    #: A discriminator or account-type check appeared, closing type cosplay.
    DISCRIMINATOR_CHECKED = "discriminator_checked"
    #: Two account parameters started being compared, closing the
    #: duplicate-mutable-account aliasing bug.
    DUPLICATE_ACCOUNT_CHECKED = "duplicate_account_checked"

    # ---- Move: abilities and object ownership -----------------------------
    # Move makes several EVM bug classes unrepresentable and introduces its
    # own, which live in the type system rather than in control flow.

    #: A struct lost an ability (`store`, `copy`, `drop`, `key`). Granting
    #: `store` is how a resource becomes freely transferable by anyone holding
    #: it, so removing one is a containment fix, not a refactor.
    ABILITY_RESTRICTED = "ability_restricted"
    #: A capability or one-time-witness requirement appeared.
    CAPABILITY_CHECK_ADDED = "capability_check_added"
    #: An object stopped being freely transferable or shared —
    #: `public_transfer` to `transfer`, or a `share_object` withdrawn.
    OBJECT_OWNERSHIP_TIGHTENED = "object_ownership_tightened"
    #: A shared object gained a version guard, so a stale reference can no
    #: longer be used against the current state.
    SHARED_OBJECT_VERSIONED = "shared_object_versioned"

    # ---- MEV: value extractable by whoever orders the transaction ---------
    # Not a memory-safety class and not an access-control class. The code is
    # correct for a single caller and wrong once someone else chooses when it
    # runs, which is why none of the above detects it.

    #: A minimum-output or slippage bound appeared. Without one a swap can be
    #: sandwiched for the whole of its price impact.
    SLIPPAGE_BOUND_ADDED = "slippage_bound_added"
    #: A deadline or expiry appeared. Without one a signed order can be held
    #: and replayed at the moment it is most profitable to the holder.
    DEADLINE_ADDED = "deadline_added"
    #: An oracle reading started being checked for staleness or confidence.
    ORACLE_STALENESS_CHECKED = "oracle_staleness_checked"
    #: A price source moved off the spot reading — TWAP, observation window,
    #: cumulative price — or gained a deviation bound.
    PRICE_MANIPULATION_GUARDED = "price_manipulation_guarded"
    #: Ordering dependence was removed or bounded: a commit-reveal, a nonce, a
    #: sequence number, a per-slot guard.
    ORDERING_DEPENDENCE_FIXED = "ordering_dependence_fixed"

    @property
    def bug_evidence(self) -> bool:
        """Does this shape assert that the previous behaviour was wrong?

        Tightening a visibility modifier, or adding an authorisation check,
        changes the contract's surface. Neither says the old behaviour was a
        defect, so neither is bug evidence on its own.
        """
        return self.value not in (
            "visibility_tightened", "access_control_added",
            # An ability or ownership restriction tightens what the type system
            # permits. That is a real change to the contract's surface; it does
            # not by itself say the old behaviour was a defect.
            "ability_restricted", "object_ownership_tightened")

    @property
    def security_relevant(self) -> bool:
        """And that being wrong had a security consequence?"""
        return self.value not in (
            "comparison_tightened", "visibility_tightened")

# --- vocabularies ----------------------------------------------------------
# Identifier names, not natural language. `require` is a Solidity builtin, not
# a word someone chose to describe their work.

GUARD_WORDS: Final[frozenset[str]] = frozenset({
    "require", "assert", "revert", "_require", "ensure", "require_", "expect",
    "checkArgument", "Require", "panicking"})
ZERO_WORDS: Final[frozenset[str]] = frozenset({
    "ZeroAddress", "AddressZero", "ZERO_ADDRESS", "isZero", "NullAddress",
    "ZeroAmount", "nullptr", "NULL"})
SUCCESS_WORDS: Final[frozenset[str]] = frozenset({
    "success", "ok", "succeeded", "callStatus", "returnData"})
CHECKED_ARITH: Final[frozenset[str]] = frozenset({
    "SafeMath", "checked_add", "checked_sub", "checked_mul", "checked_div",
    "saturating_add", "saturating_sub", "mulDiv", "tryAdd", "trySub",
    "safe_add", "safe_sub", "SafeCast", "toUint128", "toUint64", "toInt256"})
AUTH_WORDS: Final[frozenset[str]] = frozenset({
    "onlyOwner", "onlyRole", "onlyAdmin", "onlyGovernor", "onlyAuthorized",
    "requireAuth", "auth", "_checkOwner", "_checkRole", "accessControl",
    "onlySelf", "onlyManager"})
REENTRANCY_WORDS: Final[frozenset[str]] = frozenset({
    "nonReentrant", "ReentrancyGuard", "_nonReentrantBefore", "noReentrancy",
    "lock", "_lock"})
EXTERNAL_CALL_WORDS: Final[frozenset[str]] = frozenset({
    "call", "transfer", "transferFrom", "send", "delegatecall",
    "safeTransfer", "safeTransferFrom"})

# ---- Solana: proving an account is what the caller says it is -------------
SIGNER_WORDS: Final[frozenset[str]] = frozenset({
    "is_signer", "Signer", "signer", "require_signer", "assert_signer",
    "SignerAccount"})
OWNER_CHECK_WORDS: Final[frozenset[str]] = frozenset({
    "assert_owner", "check_owner", "owner", "program_id", "assert_owned_by",
    "owned_by", "AccountOwner"})
ANCHOR_CONSTRAINT_WORDS: Final[frozenset[str]] = frozenset({
    "has_one", "constraint", "seeds", "address", "token", "mint", "authority",
    "realloc", "init_if_needed"})
PDA_WORDS: Final[frozenset[str]] = frozenset({
    "find_program_address", "create_program_address", "bump", "canonical_bump",
    "try_find_program_address", "get_bump"})
CPI_WORDS: Final[frozenset[str]] = frozenset({
    "invoke", "invoke_signed", "CpiContext", "cpi", "program_id",
    "check_program_account", "assert_program"})
CLOSE_WORDS: Final[frozenset[str]] = frozenset({
    "close", "close_account", "lamports", "zeroed", "CLOSED_ACCOUNT_DISCRIMINATOR",
    "realloc", "assign"})
DISCRIMINATOR_WORDS: Final[frozenset[str]] = frozenset({
    "discriminator", "DISCRIMINATOR", "account_type", "AccountType", "try_deserialize",
    "anchor_lang"})

# ---- Move: abilities and object ownership ---------------------------------
ABILITY_WORDS: Final[frozenset[str]] = frozenset({"store", "copy", "drop", "key"})
CAPABILITY_WORDS: Final[frozenset[str]] = frozenset({
    "Cap", "AdminCap", "TreasuryCap", "OwnerCap", "UpgradeCap", "witness",
    "Witness", "one_time_witness", "assert_admin", "MintCap", "BurnCap",
    "SignerCapability", "signer_capability"})
OWNERSHIP_WORDS: Final[frozenset[str]] = frozenset({
    "public_transfer", "transfer", "share_object", "public_share_object",
    "freeze_object", "public_freeze_object"})
VERSION_WORDS: Final[frozenset[str]] = frozenset({
    "version", "VERSION", "assert_version", "migrate", "upgrade_cap"})

# ---- MEV: value extractable by whoever chooses when this runs -------------
SLIPPAGE_WORDS: Final[frozenset[str]] = frozenset({
    "min_amount_out", "minimum_amount_out", "amountOutMin", "amountOutMinimum",
    "min_out", "minOut", "slippage", "min_received", "sqrt_price_limit",
    "sqrtPriceLimitX96", "price_limit", "minAmountOut", "max_amount_in",
    "amountInMaximum", "min_lp_amount", "threshold_amount"})
DEADLINE_WORDS: Final[frozenset[str]] = frozenset({
    "deadline", "expiry", "expires_at", "valid_until", "validUntil",
    "expiration", "expiry_timestamp", "not_after", "timeout"})
STALENESS_WORDS: Final[frozenset[str]] = frozenset({
    "publish_time", "staleness", "stale", "last_updated", "updated_at",
    "max_age", "maxAge", "age", "confidence", "conf", "heartbeat",
    "updatedAt", "roundId", "answeredInRound", "max_staleness"})
TWAP_WORDS: Final[frozenset[str]] = frozenset({
    "twap", "TWAP", "observation", "observe", "cumulative", "time_weighted",
    "ema", "EMA", "moving_average", "deviation", "max_deviation",
    "price_impact", "oracle_price"})
ORDERING_WORDS: Final[frozenset[str]] = frozenset({
    "commit", "reveal", "nonce", "sequence", "seq", "last_slot", "slot",
    "same_block", "block_number", "commitment", "salt"})

_STRICT_OPS: Final[frozenset[str]] = frozenset({"<", ">"})
_LOOSE_OPS: Final[frozenset[str]] = frozenset({"<=", ">=", "==", "!="})
_COMPARISONS: Final[frozenset[str]] = _STRICT_OPS | _LOOSE_OPS
_INT_TYPES: Final[frozenset[str]] = frozenset({
    "uint8", "uint16", "uint32", "uint64", "uint96", "uint128", "uint256",
    "int8", "int16", "int32", "int64", "int128", "int256",
    "u8", "u16", "u32", "u64", "u128", "i8", "i16", "i32", "i64", "i128"})

#: Lines that carry no runtime behaviour, so a literal changing inside one is
#: never a bound being fixed. `pragma solidity ^0.8.0 -> ^0.8.13` was the
#: single worst false positive the first version produced.
_NON_BEHAVIOURAL_HEADS: Final[frozenset[str]] = frozenset({
    "pragma", "import", "use", "using", "from", "require_relative",
    "include", "package", "version", "SPDX"})

#: Paths holding tests. A fix inside a test is still evidence that something
#: was wrong, so these are not excluded from the walk — but a change confined
#: to them did not alter what ships, so it may not carry a SECURITY verdict.
_TEST_SUFFIXES: Final[tuple[str, ...]] = (
    ".t.sol", "_test.go", "_test.py", ".test.ts", ".test.js", ".spec.ts",
    ".spec.js", "_test.rs", "Test.java", "Tests.cs")
_TEST_SEGMENTS: Final[tuple[str, ...]] = (
    "test/", "tests/", "spec/", "mock/", "mocks/", "fixtures/", "__tests__/",
    "testdata/")
_DOC_SUFFIXES: Final[tuple[str, ...]] = (
    ".md", ".txt", ".rst", ".adoc", ".json", ".lock", ".yml", ".yaml",
    ".toml", ".cfg", ".ini", ".svg", ".png")

#: Above this the hunk is a rewrite, and a rewrite has no single edit.
_MAX_PAIRED = 40
#: A guard arriving inside a large block of new code is part of that new code.
_SURGICAL_LINES = 12


def _test_like(path: str) -> bool:
    return path.endswith(_TEST_SUFFIXES) or any(s in path for s in _TEST_SEGMENTS)


def _doc_like(path: str) -> bool:
    return path.endswith(_DOC_SUFFIXES)


def _non_behavioural(ts: Sequence[Tok]) -> bool:
    return not ts or ts[0] in _NON_BEHAVIOURAL_HEADS


def _is_numeric(t: Tok) -> bool:
    return bool(t) and t[0].isdigit()


#: Which failure surface a file belongs to. Taken from the extension rather
#: than from the registry, because one commit can span ecosystems — Wormhole's
#: history touches Solidity, Rust and Move in the same repository.
def ecosystem_of(path: str) -> str:
    low = path.lower()
    if low.endswith((".sol", ".vy")):
        return "evm"
    if low.endswith(".rs"):
        return "solana"
    if low.endswith(".move"):
        return "move"
    if low.endswith(".cairo"):
        return "cairo"
    return ""


def _one_point(before: Sequence[Tok], after: Sequence[Tok]
               ) -> tuple[Sequence[Tok], Tok, Tok] | None:
    """Same length, differing at exactly one position.

    This is what carries precision. Counting operator occurrences across a hunk
    reached a precision of 0.01 against a model reference, because any refactor
    changes operator counts. A removed line and an added line identical except
    at one token is an author changing one thing on purpose, and the token they
    changed says what they meant.
    """
    if len(before) != len(after) or _non_behavioural(after):
        return None
    diffs = [(o, n) for o, n in zip(before, after) if o != n]
    if len(diffs) != 1:
        return None
    return after, diffs[0][0], diffs[0][1]


def _one_point_edits(h: Hunk) -> list[tuple[Sequence[Tok], Tok, Tok]]:
    if len(h.removed) > _MAX_PAIRED or len(h.added) > _MAX_PAIRED:
        return []
    out = []
    added_toks = [tokens(a) for a in h.added]
    for r in h.removed:
        rt = tokens(r)
        for at in added_toks:
            e = _one_point(rt, at)
            if e is not None:
                out.append(e)
    return out


def detect(h: Hunk) -> list[Signature]:
    """Every shape this hunk shows, de-duplicated, order preserved."""
    before = tokens_of(h.removed)
    after = tokens_of(h.added)
    new_t = introduced(before, after)
    gone_t = removed_toks(before, after)
    edits = h.edits_existing_code
    surgical = len(h.added) <= _SURGICAL_LINES or len(h.added) * 2 <= len(h.context)
    prod = not _test_like(h.file)
    pairs = _one_point_edits(h)

    fired: list[Signature] = []

    def fire(cond: bool, sig: Signature) -> None:
        if cond and sig not in fired:
            fired.append(sig)

    fire(edits and surgical and mentions_any(new_t, GUARD_WORDS)
         and not mentions_any(before, GUARD_WORDS), Signature.GUARD_ADDED)
    fire(edits and any(o in _COMPARISONS and n in _COMPARISONS for _, o, n in pairs),
         Signature.COMPARISON_TIGHTENED)
    fire(edits and surgical and (
            mentions_any(new_t, ZERO_WORDS)
            or (mentions_any(after, GUARD_WORDS) and mentions(after, "address")
                and mentions(new_t, "0"))),
         Signature.ZERO_CHECK_ADDED)
    fire(edits and surgical and mentions_any(new_t, SUCCESS_WORDS)
         and mentions_any(after, GUARD_WORDS), Signature.RETURN_VALUE_CHECKED)
    fire(edits and any(_is_numeric(o) and _is_numeric(n)
                       and (any(t in _COMPARISONS for t in line) or "[" in line)
                       for line, o, n in pairs), Signature.BOUNDS_ADJUSTED)
    fire(edits and surgical and (
            mentions_any(new_t, CHECKED_ARITH)
            or (mentions(gone_t, "unchecked") and not mentions(new_t, "unchecked"))
            or (mentions_any(gone_t, _INT_TYPES) and mentions_any(new_t, _INT_TYPES))),
         Signature.OVERFLOW_GUARDED)
    # Reordering only means check-effects-interaction if there IS an
    # interaction. Without this it fires on reordered function parameters.
    fire(bool(h.removed) and bool(h.added) and len(before) == len(after)
         and before != after and not new_t and not gone_t
         and mentions_any(after, EXTERNAL_CALL_WORDS), Signature.ORDERING_CHANGED)
    fire(edits and mentions(gone_t, "public")
         and mentions_any(new_t, ("internal", "private", "external")),
         Signature.VISIBILITY_TIGHTENED)
    fire(edits and surgical and (
            mentions_any(new_t, AUTH_WORDS)
            or (mentions_any(after, GUARD_WORDS) and mentions(new_t, "sender")
                and not mentions(before, "sender"))),
         Signature.ACCESS_CONTROL_ADDED)
    fire(edits and surgical and mentions_any(new_t, REENTRANCY_WORDS),
         Signature.REENTRANCY_GUARD_ADDED)

    eco = ecosystem_of(h.file)
    if eco == "solana":
        _detect_solana(fire, h, before, after, new_t, gone_t, edits, surgical)
    elif eco == "move":
        _detect_move(fire, h, before, after, new_t, gone_t, edits, surgical)
    # MEV is not an ecosystem. The same mistake — leaving the caller's price or
    # timing unbounded — is made in Solidity, in Rust and in Move, and none of
    # the shapes above notices it, because the code is correct for one caller
    # and wrong once somebody else chooses when it runs.
    _detect_mev(fire, h, before, after, new_t, gone_t, edits, surgical)

    if prod:
        return fired
    # A change confined to tests did not alter what ships. The shapes are still
    # reported; they just stop counting as security evidence.
    return [s for s in fired if not s.security_relevant]


FireFn = Callable[[bool, Signature], None]


def _detect_solana(fire: FireFn, h: Hunk, before: Sequence[Tok],
                   after: Sequence[Tok], new_t: Sequence[Tok],
                   gone_t: Sequence[Tok], edits: bool, surgical: bool) -> None:
    """A Solana program is handed accounts by its caller and must prove each
    one is what it claims to be. Nothing in the language does that for it, so
    the entire class of "forgot to check" has no EVM analogue and none of the
    EVM shapes sees it."""
    fire(edits and mentions_any(new_t, SIGNER_WORDS)
         and not mentions_any(before, SIGNER_WORDS), Signature.SIGNER_CHECK_ADDED)
    fire(edits and mentions_any(new_t, OWNER_CHECK_WORDS)
         and (mentions_any(after, GUARD_WORDS) or mentions_any(after, _COMPARISONS)),
         Signature.OWNER_CHECK_ADDED)
    # An Anchor constraint is an attribute, so it arrives with its own syntax
    # rather than as a statement; requiring `account` keeps ordinary uses of
    # the word `mint` or `authority` from firing.
    fire(edits and mentions(after, "account")
         and mentions_any(new_t, ANCHOR_CONSTRAINT_WORDS),
         Signature.ACCOUNT_CONSTRAINT_ADDED)
    fire(edits and (mentions_any(new_t, PDA_WORDS)
                    or (mentions(gone_t, "create_program_address")
                        and mentions(new_t, "find_program_address"))),
         Signature.PDA_BUMP_CANONICALISED)
    fire(edits and mentions_any(after, CPI_WORDS)
         and mentions(new_t, "program_id")
         and (mentions_any(after, GUARD_WORDS) or mentions_any(after, _COMPARISONS)),
         Signature.CPI_TARGET_CHECKED)
    # Draining lamports without clearing the data leaves an account that can be
    # topped back up inside the same transaction and reused. The close itself
    # is usually already there — what arrives is the zeroing — so the close has
    # to be looked for in the CONTEXT as well as in what was added.
    around = list(after) + tokens_of(h.context)
    fire(edits and mentions_any(around, CLOSE_WORDS)
         and mentions_any(new_t, ("zeroed", "CLOSED_ACCOUNT_DISCRIMINATOR",
                                  "realloc", "assign", "fill")),
         Signature.ACCOUNT_CLOSE_HARDENED)
    fire(edits and mentions_any(new_t, DISCRIMINATOR_WORDS),
         Signature.DISCRIMINATOR_CHECKED)
    # `a.key() != b.key()` between two account parameters: the aliasing bug
    # where one account is passed twice and written through both handles.
    fire(edits and mentions(after, "key")
         and (mentions(new_t, "!=") or mentions(new_t, "ne"))
         and mentions_any(after, GUARD_WORDS),
         Signature.DUPLICATE_ACCOUNT_CHECKED)


def _detect_move(fire: FireFn, h: Hunk, before: Sequence[Tok],
                 after: Sequence[Tok], new_t: Sequence[Tok],
                 gone_t: Sequence[Tok], edits: bool, surgical: bool) -> None:
    """Move puts several of its failure modes in the TYPE system rather than in
    control flow, so a shape that looks for a check will never see them. The
    sharpest is an ability: granting `store` is how a resource becomes freely
    transferable by anyone who holds it."""
    fire(edits and mentions_any(gone_t, ABILITY_WORDS)
         and mentions(before, "has") and not mentions_any(new_t, ABILITY_WORDS),
         Signature.ABILITY_RESTRICTED)
    fire(edits and surgical and mentions_any(new_t, CAPABILITY_WORDS),
         Signature.CAPABILITY_CHECK_ADDED)
    fire(edits and ((mentions(gone_t, "public_transfer") and mentions(new_t, "transfer"))
                    or (mentions(gone_t, "public_share_object")
                        and not mentions(new_t, "public_share_object"))
                    or (mentions(gone_t, "share_object")
                        and mentions_any(new_t, ("transfer", "freeze_object")))),
         Signature.OBJECT_OWNERSHIP_TIGHTENED)
    fire(edits and mentions_any(new_t, VERSION_WORDS)
         and mentions_any(after, GUARD_WORDS), Signature.SHARED_OBJECT_VERSIONED)


def _detect_mev(fire: FireFn, h: Hunk, before: Sequence[Tok],
                after: Sequence[Tok], new_t: Sequence[Tok],
                gone_t: Sequence[Tok], edits: bool, surgical: bool) -> None:
    """Value extractable by whoever decides when the transaction runs.

    Not memory safety and not access control: the code is correct for a single
    caller and wrong the moment someone else chooses the ordering. A swap with
    no minimum output is sandwichable for the whole of its price impact; a
    signed order with no deadline can be held until it pays; a spot price read
    inside the same transaction that moves it is not a price at all.
    """
    fire(edits and mentions_any(new_t, SLIPPAGE_WORDS),
         Signature.SLIPPAGE_BOUND_ADDED)
    fire(edits and mentions_any(new_t, DEADLINE_WORDS)
         and (mentions_any(after, GUARD_WORDS) or mentions_any(after, _COMPARISONS)),
         Signature.DEADLINE_ADDED)
    fire(edits and mentions_any(new_t, STALENESS_WORDS)
         and (mentions_any(after, GUARD_WORDS) or mentions_any(after, _COMPARISONS)),
         Signature.ORACLE_STALENESS_CHECKED)
    fire(edits and (mentions_any(new_t, TWAP_WORDS)
                    or (mentions(gone_t, "slot0") and mentions_any(new_t, TWAP_WORDS))),
         Signature.PRICE_MANIPULATION_GUARDED)
    fire(edits and surgical and mentions_any(new_t, ORDERING_WORDS)
         and mentions_any(after, GUARD_WORDS), Signature.ORDERING_DEPENDENCE_FIXED)


def signatures(d: Diff) -> list[Signature]:
    """Every signature the diff produces, in hunk order."""
    out: list[Signature] = []
    for h in d.hunks:
        out.extend(detect(h))
    return out


def distinct_signatures(d: Diff) -> list[Signature]:
    seen: list[Signature] = []
    for s in signatures(d):
        if s not in seen:
            seen.append(s)
    return seen


def hunk_trivial(h: Hunk) -> bool:
    """Did the tokens change at all? If not, the edit was comments, whitespace
    or formatting — provably not a behaviour change."""
    return tokens_of(h.removed) == tokens_of(h.added)


def semantically_trivial(d: Diff) -> bool:
    return all(hunk_trivial(h) for h in d.hunks)


def only_docs(d: Diff) -> bool:
    return bool(d.hunks) and all(_doc_like(h.file) for h in d.hunks)
