"""lean.py — the deterministic classifier, as a pipeline engine.

The decision core lives in `lean/` and is written in Lean 4, compiled to a
native binary. This module is the adapter: it frames commits for that binary,
reads its NDJSON back, and turns it into `Verdict`s.

Why a separate language for one stage. The honesty rules this project keeps —
a skipped check is never `False`, a verdict always has a witness, more diff
never hides a finding — are enforced everywhere else by review and by tests
over the inputs somebody thought of. In `lean/OssBugfix/Props.lean` three of
them are theorems, checked by the compiler over every input there is. That is
the whole reason this component is not just more Python.

What it is for, and what it is not. Measured on 2,418 smart-contract commits
it runs at ~43 commits/second against ~0.25/second for a hosted model, and it
*abstains* on about three quarters of them. So it is a FILTER: it settles the
cases a structural fact settles, and hands the rest on. It does not replace a
model, and `docs/deterministic-classification.md` says why not.

Framing: records are `\\x1e<id>\\x1f<diff>`. Both are C0 control characters git
never emits, so a diff needs no escaping on the way in — the same trick the
commit walker uses.
"""
from __future__ import annotations

import json
import os
import shutil
import subprocess
from pathlib import Path
from typing import Sequence

from .. import paths
from .base import Classifier, ClassifyError, CommitView, Verdict

__all__ = ["LeanClassifier", "binary_path", "build"]

REC = "\x1e"
FLD = "\x1f"
DEFAULT_TIMEOUT = int(os.environ.get("OSS_BUGFIXES_LEAN_TIMEOUT", "900"))

#: Which signatures the Lean core reports, and whether each is security
#: evidence. Mirrored here only for the human-readable rationale; the binary is
#: the authority and carries the same grading in `Signature.securityRelevant`.
SIGNATURE_BLURB = {
    "guard_added": "a require/assert/revert was inserted into existing code",
    "comparison_tightened": "one comparison operator was swapped in one line",
    "zero_check_added": "a zero-address or null check appeared",
    "return_value_checked": "a call's success flag started being checked",
    "bounds_adjusted": "a numeric bound changed in a comparison or index",
    "overflow_guarded": "unchecked arithmetic was removed or made checked",
    "ordering_changed": "statements around an external call were reordered",
    "visibility_tightened": "public became internal/private/external",
    "access_control_added": "an authorisation check appeared",
    "reentrancy_guard_added": "a reentrancy guard appeared",
}

GROUND_BLURB = {
    "empty_diff": "the diff is empty",
    "no_token_change": "no token changed — comments, whitespace or formatting only",
    "docs_only": "only non-code files changed",
    "structural_signature": "structural edit shapes in the diff",
    "no_structural_signature": "no structural shape matched; the diff was read, "
                               "not judged",
}


def binary_path() -> Path:
    return paths.repo_root() / "lean" / ".lake" / "build" / "bin" / "ossbugfix-classify"


def build(quiet: bool = True) -> Path:
    """Compile the Lean core. Needs `lake` on PATH; no package index, no
    mathlib, so this is seconds from a bare toolchain."""
    root = paths.repo_root() / "lean"
    if not shutil.which("lake"):
        raise ClassifyError(
            "the lean engine needs `lake` on PATH — install the Lean toolchain "
            "(https://lean-lang.org/install) or use --engine heuristic")
    r = subprocess.run(["lake", "build"], cwd=root, capture_output=True, text=True,
                       timeout=1800)
    if r.returncode != 0:
        raise ClassifyError(f"lake build failed:\n{r.stderr[-800:]}")
    if not quiet:
        print(r.stdout[-400:])
    return binary_path()


class LeanClassifier(Classifier):
    name = "lean"
    model = ""

    def __init__(self, *, binary: str | Path | None = None,
                 timeout: int = DEFAULT_TIMEOUT, auto_build: bool = True,
                 **_: object) -> None:
        self.binary = Path(binary) if binary else binary_path()
        self.timeout = timeout
        if not self.binary.exists():
            if not auto_build:
                raise ClassifyError(f"no Lean binary at {self.binary}; run `lake build` in lean/")
            self.binary = build()
        self._version = self._probe()

    @property
    def engine_label(self) -> str:
        return "lean"

    def _probe(self) -> str:
        # `input=""` closes stdin: the binary reads stdin in its normal mode,
        # and inheriting a terminal here would hang the probe.
        r = subprocess.run([str(self.binary), "--version"], input="",
                           capture_output=True, text=True, timeout=60)
        return (r.stdout or "").strip() or "ossbugfix-classify"

    def selftest(self) -> bool:
        """The binary checks two fixed patches on every run: a guard inserted
        into existing code must come back a security fix, and a comment-only
        edit must come back refuted. If those stop holding, every downstream
        number is wrong, so it is worth a second before a sweep."""
        r = subprocess.run([str(self.binary), "--selftest"], capture_output=True,
                           text=True, timeout=120)
        return r.returncode == 0

    def classify(self, view: CommitView) -> Verdict:
        return self.classify_many([view])[0]

    def classify_many(self, views: Sequence[CommitView]) -> list[Verdict]:
        if not views:
            return []
        payload = "".join(f"{REC}{v.sha}{FLD}{v.diff or ''}" for v in views)
        try:
            r = subprocess.run([str(self.binary)], input=payload, capture_output=True,
                               text=True, timeout=self.timeout)
        except subprocess.TimeoutExpired:
            return [self._failed(f"lean engine timed out after {self.timeout}s")
                    for _ in views]
        if r.returncode != 0:
            return [self._failed(f"lean engine exited {r.returncode}: {r.stderr[-200:]}")
                    for _ in views]

        by_sha: dict[str, dict] = {}
        for line in r.stdout.splitlines():
            if not line.strip():
                continue
            try:
                obj = json.loads(line)
            except json.JSONDecodeError:
                continue
            by_sha[str(obj.get("id", ""))] = obj

        out = []
        for v in views:
            obj = by_sha.get(v.sha)
            if obj is None:
                # Silence is not a negative. The binary did not answer for this
                # commit, and that is a different claim from "not a bug fix".
                out.append(self._failed("lean engine returned no record"))
            else:
                out.append(self._verdict(obj))
        return out

    def _verdict(self, obj: dict) -> Verdict:
        sigs = [str(s) for s in (obj.get("signatures") or [])]
        ground = str(obj.get("ground") or "")
        parts = [GROUND_BLURB.get(ground, ground)]
        parts += [SIGNATURE_BLURB.get(s, s) for s in sigs]
        return Verdict(
            is_bugfix=_tri(obj.get("is_bugfix")),
            is_security_fix=_tri(obj.get("is_security_fix")),
            severity="",
            cwe="",
            stride="",
            # Not a probability. It is the number of distinct shapes, scaled,
            # and it exists to order rows for review.
            confidence=min(0.9, 0.3 + 0.15 * int(obj.get("weight") or 0)) if sigs else 0.0,
            rationale="; ".join(p for p in parts if p)[:1000],
            classifier=self.engine_label,
            model=self._version,
        )

    def _failed(self, msg: str) -> Verdict:
        return Verdict(classifier=self.engine_label, model=self._version, error=msg)


def _tri(v) -> bool | None:
    return v if isinstance(v, bool) else None
