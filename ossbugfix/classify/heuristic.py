"""heuristic.py — the no-model baseline.

It exists for three reasons, none of them "it is good enough":

  1. the pipeline must be runnable end to end with no API key, in CI and on a
     laptop, or nobody will run it;
  2. a model is only worth its cost against a baseline, and "what a keyword
     crawl would have said" is the honest one to beat;
  3. the disagreement between this and the model is the measurement that tells
     a domain's docs how much silent-fix recall the keyword era was losing.

It reads only the deterministic signals. It never sees the diff, and it says so
in `rationale` so nobody mistakes its output for a judgement about code.
"""
from __future__ import annotations

from typing import Sequence

from .base import Classifier, CommitView, Verdict

__all__ = ["HeuristicClassifier"]


class HeuristicClassifier(Classifier):
    name = "heuristic"
    model = ""

    def __init__(self, *, security_threshold: float = 0.5, **_: object) -> None:
        self.security_threshold = security_threshold

    def classify(self, view: CommitView) -> Verdict:
        reasons: list[str] = []
        if view.advisory_ids:
            reasons.append(f"advisory id ({view.advisory_ids})")
        if view.impact_class:
            reasons.append(f"impact class ({view.impact_class})")
        if view.keyword_score >= self.security_threshold:
            reasons.append(f"keyword score {view.keyword_score:.2f}")
        if view.is_backport:
            reasons.append("backported to another branch")
        if view.is_revert:
            reasons.append("revert")

        security = bool(view.advisory_ids) or (
            bool(view.impact_class) and view.keyword_score >= self.security_threshold)
        # A revert is worth triaging — somebody decided a change had to come out
        # — but it is not evidence of a DEFECT: `Revert "Improve hmac test"` is
        # a revert and is not a bug fix. It counts toward n_signals, which is a
        # question for the classifier, and not toward this verdict.
        bugfix = security or bool(view.impact_class)

        # Confidence tracks evidence count, and is capped well below 1.0: this
        # engine cannot see the code, so it is never entitled to certainty.
        confidence = min(0.2 + 0.2 * len(reasons), 0.8) if reasons else 0.1
        return Verdict(
            is_bugfix=bugfix if reasons else None,
            is_security_fix=security if reasons else None,
            severity="",
            confidence=round(confidence, 2),
            rationale=("message-only signals: " + "; ".join(reasons)) if reasons
                      else "no deterministic signal fired; the diff was not read",
            classifier=self.name,
            model="",
        )

    def classify_many(self, views: Sequence[CommitView]) -> list[Verdict]:
        return [self.classify(v) for v in views]
