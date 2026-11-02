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
from typing import Final, Sequence

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

    @property
    def bug_evidence(self) -> bool:
        """Does this shape assert that the previous behaviour was wrong?

        Tightening a visibility modifier, or adding an authorisation check,
        changes the contract's surface. Neither says the old behaviour was a
        defect, so neither is bug evidence on its own.
        """
        return self.value not in ("visibility_tightened", "access_control_added")

    @property
    def security_relevant(self) -> bool:
        """And that being wrong had a security consequence?"""
        return self.value not in ("comparison_tightened", "visibility_tightened")

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

    if prod:
        return fired
    # A change confined to tests did not alter what ships. The shapes are still
    # reported; they just stop counting as security evidence.
    return [s for s in fired if not s.security_relevant]


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
