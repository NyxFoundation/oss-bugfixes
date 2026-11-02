"""state.py — what has already been done, so a killed run resumes.

A whole-history sweep over a few hundred repositories takes hours and WILL be
interrupted. Every stage therefore records per-repository completion, and the
CLI skips what is already done unless told `--force`.

One small JSON file per (domain, stage). Not a database: the file has to be
readable by a human trying to work out why a repo was skipped, and deletable by
the same human to make it run again.
"""
from __future__ import annotations

import json
import os
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from . import paths

__all__ = ["Stage", "StageState"]


class Stage:
    MIRROR = "mirror"
    WALK = "walk"
    SIGNALS = "signals"
    DIFFS = "diffs"
    CLASSIFY = "classify"


@dataclass
class StageState:
    domain: str
    stage: str
    path: Path = field(init=False)
    entries: dict[str, dict[str, Any]] = field(init=False, default_factory=dict)

    def __post_init__(self) -> None:
        self.path = paths.state_dir(self.domain) / f"{self.stage}.json"
        self.entries = self._read()

    def _read(self) -> dict[str, dict[str, Any]]:
        try:
            raw = json.loads(self.path.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            return {}
        return raw.get("entries", {}) if isinstance(raw, dict) else {}

    def done(self, slug: str) -> bool:
        return bool(self.entries.get(slug, {}).get("ok"))

    def get(self, slug: str) -> dict[str, Any]:
        return dict(self.entries.get(slug, {}))

    def record(self, slug: str, *, ok: bool, **detail: Any) -> None:
        self.entries[slug] = {"ok": bool(ok), "at": _now(), **detail}

    def forget(self, slug: str) -> None:
        self.entries.pop(slug, None)

    def save(self) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        tmp = self.path.with_suffix(".tmp")
        payload = {"domain": self.domain, "stage": self.stage,
                   "updated_at": _now(), "entries": self.entries}
        tmp.write_text(json.dumps(payload, indent=1, sort_keys=True,
                                  ensure_ascii=False), encoding="utf-8")
        os.replace(tmp, self.path)

    def summary(self) -> tuple[int, int]:
        ok = sum(1 for e in self.entries.values() if e.get("ok"))
        return ok, len(self.entries)


def _now() -> str:
    return time.strftime("%Y-%m-%dT%H:%M:%S%z")
