"""verdict.py — signatures to a verdict, with the honesty rules built in.

The verdict is THREE-VALUED and that is the whole design. `None` means the
classifier has no opinion, which is a different claim from `False`, and
conflating them is the mistake this project has already paid for twice — a
skipped backport check recorded as "0 backported", and a timed-out model call
recorded as "not a bug fix".

Two layers enforce the rules, and they are deliberately redundant:

  1. **Construction.** `__post_init__` rejects any `Verdict` that asserts
     something without a witness, so no such object can exist anywhere in the
     process — not just as a return value of `classify`. This is the Python
     stand-in for making the state unrepresentable.
  2. **Proof.** `verify.py` discharges the same rules over the *whole* finite
     space of signature subsets with Z3, which shows `classify` never even
     attempts an invalid construction. See `docs/verification.md`.

Layer 1 alone would turn a logic bug into a crash in production. Layer 2 alone
would leave hand-built verdicts unchecked. Together: it cannot be built wrong,
and the one function that builds them is proved never to try.
"""
from __future__ import annotations

import enum
from dataclasses import dataclass
from typing import Sequence

from .diff import Diff
from .signature import (Signature, distinct_signatures, only_docs,
                        semantically_trivial, signatures)

__all__ = ["Ground", "Verdict", "classify", "decide", "InvalidVerdict"]


class InvalidVerdict(ValueError):
    """A verdict that asserts more than its evidence supports."""


class Ground(enum.Enum):
    """Why the classifier said what it said. Carried into the output so a row
    is auditable without re-running anything."""

    EMPTY_DIFF = "empty_diff"
    NO_TOKEN_CHANGE = "no_token_change"
    DOCS_ONLY = "docs_only"
    STRUCTURAL = "structural_signature"
    NO_SIGNATURE = "no_structural_signature"


@dataclass(frozen=True, slots=True)
class Verdict:
    is_bugfix: bool | None
    is_security_fix: bool | None
    sigs: tuple[Signature, ...]
    ground: Ground

    def __post_init__(self) -> None:
        # Rule: a positive verdict names its witness.
        if self.is_security_fix is True and not any(s.security_relevant for s in self.sigs):
            raise InvalidVerdict(
                "is_security_fix=True with no security-relevant signature")
        if self.is_bugfix is True and not any(s.bug_evidence for s in self.sigs):
            raise InvalidVerdict("is_bugfix=True with no bug-evidence signature")
        # Rule: a negative verdict shows no evidence, so a reader is never
        # given support for a claim that was not made.
        if self.is_bugfix is False and self.sigs:
            raise InvalidVerdict("a refuted verdict must carry no signatures")
        # Rule: security implies bugfix. Something cannot have had a security
        # consequence without having been wrong.
        if self.is_security_fix is True and self.is_bugfix is not True:
            raise InvalidVerdict("is_security_fix=True without is_bugfix=True")

    # --- smart constructors ------------------------------------------------

    @classmethod
    def refuted(cls, ground: Ground) -> "Verdict":
        """Provably not a behaviour change."""
        return cls(False, False, (), ground)

    @classmethod
    def undecided(cls, ground: Ground, sigs: Sequence[Signature] = ()) -> "Verdict":
        """No opinion. Shapes may still be reported — "something changed" is
        not "something was wrong"."""
        return cls(None, None, tuple(sigs), ground)

    @classmethod
    def asserted(cls, witnesses: Sequence[Signature]) -> "Verdict":
        """A positive verdict. The witnesses are an ARGUMENT, not an
        afterthought: there is no way to assert without supplying them."""
        sigs = tuple(witnesses)
        if not any(s.bug_evidence for s in sigs):
            raise InvalidVerdict("asserted() needs at least one bug-evidence signature")
        return cls(True, any(s.security_relevant for s in sigs), sigs, Ground.STRUCTURAL)

    # --- views -------------------------------------------------------------

    @property
    def distinct(self) -> tuple[Signature, ...]:
        seen: list[Signature] = []
        for s in self.sigs:
            if s not in seen:
                seen.append(s)
        return tuple(seen)

    @property
    def weight(self) -> int:
        """Not a probability and not calibrated. It orders rows for review:
        more independent shapes, more worth a look."""
        return len(self.distinct)

    def to_dict(self) -> dict[str, object]:
        return {
            "is_bugfix": self.is_bugfix,
            "is_security_fix": self.is_security_fix,
            "ground": self.ground.value,
            "signatures": [s.value for s in self.distinct],
            "weight": self.weight,
        }


def decide(*, touched: bool, trivial: bool, docs_only: bool,
           found: Sequence[Signature]) -> Verdict:
    """The decision itself, over its four inputs and nothing else.

    Factored out so that the thing `verify.py` proves is the thing that runs.
    A verifier that checks a hand-written model of a decision proves something
    about the model; the gap between model and code is where the bug lives.
    Here the decision takes four values, its input space is finite, and the
    verifier executes THIS function over all of it.

    Order is from most certain to least:
      1. nothing changed                 → refuted
      2. no token changed                → refuted (comments, whitespace, format)
      3. only non-code files             → refuted
      4. at least one bug-evidence shape → asserted, with those shapes
      5. otherwise                       → undecided, explicitly
    """
    if not touched:
        return Verdict.refuted(Ground.EMPTY_DIFF)
    if trivial:
        return Verdict.refuted(Ground.NO_TOKEN_CHANGE)
    if docs_only:
        return Verdict.refuted(Ground.DOCS_ONLY)
    if any(s.bug_evidence for s in found):
        return Verdict.asserted(found)
    return Verdict.undecided(Ground.NO_SIGNATURE, found)


def classify(d: Diff) -> Verdict:
    """Read the four decision inputs off a diff, then decide."""
    return decide(
        touched=d.touched,
        trivial=semantically_trivial(d),
        docs_only=only_docs(d),
        found=signatures(d),
    )
