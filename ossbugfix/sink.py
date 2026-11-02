"""sink.py — the published table, appended to and never silently rewritten.

`domains/<domain>/data/bugfixes.csv` is the artifact this repository exists to
produce. Rules it keeps:

  * **append, don't replace.** A rerun that classified 500 new commits must not
    discard the 40,000 already there, including the ones a human corrected.
  * **one row per commit.** Identity is `(domain, repo_slug, sha)`. Re-running
    a classifier over a commit updates its verdict in place rather than adding
    a second row.
  * **human edits win by default.** A row whose `classifier` is `human` is not
    overwritten by a model; `--overwrite-human` exists and is deliberately
    awkward to type.
  * **atomic.** Written to a temp file and renamed, so an interrupted run
    leaves the previous table intact rather than a half-written one.
"""
from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path

import pandas as pd

from . import paths, schema

__all__ = ["Sink", "MergeStats"]

HUMAN_CLASSIFIER = "human"


@dataclass(frozen=True)
class MergeStats:
    added: int = 0
    updated: int = 0
    unchanged: int = 0
    protected: int = 0      # human-labelled rows a model was not allowed to touch
    total: int = 0

    def __str__(self) -> str:
        bits = [f"+{self.added} new"]
        if self.updated:
            bits.append(f"{self.updated} updated")
        if self.unchanged:
            bits.append(f"{self.unchanged} unchanged")
        if self.protected:
            bits.append(f"{self.protected} human-labelled kept")
        return f"{', '.join(bits)} → {self.total} rows"


@dataclass
class Sink:
    domain: str
    path: Path | None = None

    def __post_init__(self) -> None:
        self.path = Path(self.path) if self.path else (
            paths.domain_data_dir(self.domain) / "bugfixes.csv")

    def read(self) -> pd.DataFrame:
        if not self.path.exists():
            return schema.empty(schema.BUGFIX_COLUMNS)
        df = pd.read_csv(self.path, dtype=str, keep_default_na=False,
                         na_values=[""], low_memory=False)
        return schema.conform(df, schema.BUGFIX_COLUMNS)

    def merge(self, incoming: pd.DataFrame, *,
              overwrite_human: bool = False) -> MergeStats:
        """Merge `incoming` into the table on disk and write it back."""
        incoming = self._prepare(incoming)
        if not len(incoming):
            existing = self.read()
            return MergeStats(unchanged=len(existing), total=len(existing))

        existing = self.read()
        if not len(existing):
            self._write(incoming)
            return MergeStats(added=len(incoming), total=len(incoming))

        key = list(schema.KEY_COLUMNS)
        existing_keys = existing.set_index(key, drop=False)
        incoming_keys = incoming.set_index(key, drop=False)

        protected_idx = []
        if not overwrite_human:
            human = existing_keys["classifier"].fillna("") == HUMAN_CLASSIFIER
            protected_idx = existing_keys.index[human]
            incoming_keys = incoming_keys.drop(index=protected_idx, errors="ignore")

        is_update = incoming_keys.index.isin(existing_keys.index)
        n_update = int(is_update.sum())
        n_add = int(len(incoming_keys) - n_update)

        kept = existing_keys.drop(index=incoming_keys.index, errors="ignore")
        merged = pd.concat([kept, incoming_keys], ignore_index=True)
        merged = merged.sort_values(["domain", "repo_slug", "authored_at", "sha"],
                                    na_position="last")
        self._write(merged)
        return MergeStats(added=n_add, updated=n_update,
                          unchanged=len(kept), protected=len(protected_idx),
                          total=len(merged))

    def _prepare(self, df: pd.DataFrame) -> pd.DataFrame:
        out = schema.conform(df, schema.BUGFIX_COLUMNS)
        if not len(out):
            return out
        need_id = out["id"].isna() | (out["id"].fillna("") == "")
        if need_id.any():
            out.loc[need_id, "id"] = [
                schema.commit_id(r.domain, r.repo_slug, r.sha)
                for r in out.loc[need_id].itertuples()
            ]
        need_url = out["url"].isna() | (out["url"].fillna("") == "")
        if need_url.any():
            out.loc[need_url, "url"] = [
                schema.commit_url(r.repo, r.sha) for r in out.loc[need_url].itertuples()
            ]
        # Two rows for the same commit inside one batch is a caller bug; keep
        # the last, which is the most recently classified.
        return out.drop_duplicates(subset=list(schema.KEY_COLUMNS), keep="last")

    def _write(self, df: pd.DataFrame) -> None:
        out = schema.conform(df, schema.BUGFIX_COLUMNS)
        self.path.parent.mkdir(parents=True, exist_ok=True)
        tmp = self.path.with_suffix(".tmp")
        out.to_csv(tmp, index=False)
        os.replace(tmp, self.path)

    def write_preview(self, limit: int = 2000) -> Path | None:
        """A small CSV GitHub will render as a table. The full file is too wide
        and too long to browse, and a corpus nobody can look at does not get
        checked."""
        df = self.read()
        if not len(df):
            return None
        cols = ["id", "repo", "sha", "authored_at", "subject", "n_files",
                "impact_class", "advisory_ids", "n_signals",
                "is_bugfix", "is_security_fix", "severity", "confidence"]
        cols = [c for c in cols if c in df.columns]
        view = df.sort_values("n_signals", ascending=False, na_position="last").head(limit)
        p = self.path.with_name(self.path.stem + ".preview.csv")
        view[cols].to_csv(p, index=False)
        return p
