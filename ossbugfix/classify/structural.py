"""structural.py — the deterministic classifier as a pipeline engine.

In-process: no subprocess, no framing protocol, no second toolchain. The
decision core is `ossbugfix.detect`, which is `mypy --strict` clean and whose
verdict rules are discharged over their entire finite input space by
`ossbugfix.detect.verify` (Z3, plus exhaustive execution of the real function,
plus a point-by-point check that the two agree).

What it is for: a FILTER. It settles the cases a structural fact settles —
chiefly "no token changed, so no behaviour changed" — abstains on the rest,
and attaches a checkable reason to every answer it gives. It does not replace
a model; `docs/deterministic-classification.md` has the measurements saying
why not.
"""
from __future__ import annotations

from typing import Sequence

from ..detect import Ground, Signature, classify, parse
from .base import Classifier, CommitView, Verdict

__all__ = ["StructuralClassifier", "SIGNATURE_BLURB", "GROUND_BLURB"]

SIGNATURE_BLURB: dict[Signature, str] = {
    Signature.GUARD_ADDED: "a require/assert/revert was inserted into existing code",
    Signature.COMPARISON_TIGHTENED: "one comparison operator was swapped in one line",
    Signature.ZERO_CHECK_ADDED: "a zero-address or null check appeared",
    Signature.RETURN_VALUE_CHECKED: "a call's success flag started being checked",
    Signature.BOUNDS_ADJUSTED: "a numeric bound changed in a comparison or index",
    Signature.OVERFLOW_GUARDED: "unchecked arithmetic was removed or made checked",
    Signature.ORDERING_CHANGED: "statements around an external call were reordered",
    Signature.VISIBILITY_TIGHTENED: "public became internal/private/external",
    Signature.ACCESS_CONTROL_ADDED: "an authorisation check appeared",
    Signature.REENTRANCY_GUARD_ADDED: "a reentrancy guard appeared",
}

GROUND_BLURB: dict[Ground, str] = {
    Ground.EMPTY_DIFF: "the diff is empty",
    Ground.NO_TOKEN_CHANGE: "no token changed — comments, whitespace or formatting only",
    Ground.DOCS_ONLY: "only non-code files changed",
    Ground.STRUCTURAL: "structural edit shapes in the diff",
    Ground.NO_SIGNATURE: "no structural shape matched; the diff was read, not judged",
}


class StructuralClassifier(Classifier):
    name = "structural"
    model = ""

    def __init__(self, **_: object) -> None:
        self.model = ""

    @property
    def engine_label(self) -> str:
        return "structural"

    def classify(self, view: CommitView) -> Verdict:
        if not view.diff:
            # No diff is not "no change". Saying so is the difference between
            # a row somebody re-runs and a row somebody trusts.
            return Verdict(classifier=self.engine_label,
                           error="no diff available to read")
        v = classify(parse(view.diff))
        parts = [GROUND_BLURB.get(v.ground, v.ground.value)]
        parts += [SIGNATURE_BLURB.get(s, s.value) for s in v.distinct]
        return Verdict(
            is_bugfix=v.is_bugfix,
            is_security_fix=v.is_security_fix,
            severity="",
            cwe="",
            stride="",
            # Not a probability: the count of distinct shapes, scaled. It
            # exists to order rows for review.
            confidence=min(0.9, 0.3 + 0.15 * v.weight) if v.sigs else 0.0,
            rationale="; ".join(p for p in parts if p)[:1000],
            classifier=self.engine_label,
            model="",
        )

    def classify_many(self, views: Sequence[CommitView]) -> list[Verdict]:
        return [self.classify(v) for v in views]
