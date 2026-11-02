"""base.py — what a classifier is, and what it is allowed to say."""
from __future__ import annotations

import time
from dataclasses import dataclass, field, asdict
from typing import Any, Protocol, Sequence

__all__ = ["Verdict", "Classifier", "ClassifyError", "CommitView",
           "FAILURE_PREFIX", "is_failure"]

#: Marks a row whose classifier never got an answer, as opposed to one whose
#: classifier looked and declined. Both record `is_bugfix=None`, and the
#: difference decides whether a rerun should try again: a declined row is a
#: result, a failed row is work still owed.
FAILURE_PREFIX = "[not classified]"


def is_failure(rationale: object) -> bool:
    return isinstance(rationale, str) and rationale.startswith(FAILURE_PREFIX)


class ClassifyError(RuntimeError):
    pass


@dataclass
class CommitView:
    """Everything a classifier is given about one commit. No pandas, so an
    engine can be tested without building a frame."""

    id: str
    repo: str
    sha: str
    url: str
    subject: str
    body: str
    files: Sequence[str]
    insertions: int
    deletions: int
    languages: str = ""
    diff: str = ""
    # deterministic signals, passed through so the model is told what is already
    # known rather than having to re-derive it from the text
    advisory_ids: str = ""
    impact_class: str = ""
    is_backport: bool | None = None
    is_revert: bool | None = None
    keyword_score: float = 0.0


@dataclass
class Verdict:
    """A classifier's answer. Every field is optional except the two booleans,
    and those are tri-state: None means "the engine declined", which is a
    different thing from False and must not be recorded as one."""

    is_bugfix: bool | None = None
    is_security_fix: bool | None = None
    severity: str = ""
    cwe: str = ""
    stride: str = ""
    confidence: float = 0.0
    rationale: str = ""
    classifier: str = ""
    model: str = ""
    classified_at: str = field(default_factory=lambda: time.strftime("%Y-%m-%dT%H:%M:%S%z"))
    error: str = ""

    def as_row(self) -> dict[str, Any]:
        """The verdict as columns.

        A failure surfaces in `rationale`. Recording the row as undecided and
        saying nothing else is only half the honesty rule: the first sweep
        that did this left 125 rows blank with no way to tell a model that
        declined from an endpoint that timed out, which is the difference
        between "re-run it" and "look at the commit".
        """
        d = asdict(self)
        err = d.pop("error", "") or ""
        if err and not (d.get("rationale") or "").strip():
            d["rationale"] = f"{FAILURE_PREFIX} {err}"[:1000]
        return d


class Classifier(Protocol):
    name: str
    model: str

    def classify(self, view: CommitView) -> Verdict: ...

    def classify_many(self, views: Sequence[CommitView]) -> list[Verdict]: ...
