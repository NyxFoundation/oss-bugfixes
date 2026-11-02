"""manifest.py — `domains/<domain>/data/manifest.json`, so a table on disk can
say what produced it.

A corpus that is appended to for months by code that keeps changing needs to
carry its own provenance, or nobody can tell whether two halves of it mean the
same thing. The manifest records the schema version, the tool version, when it
was last written, how many rows there are and how they were classified.

It is also what makes migration checkable rather than hopeful: `oss-bugfixes
migrate` compares what is on disk against the current contract and says what it
would change before it changes anything.
"""
from __future__ import annotations

import json
import os
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import pandas as pd

from . import __version__, paths, schema

__all__ = ["Manifest", "describe", "write", "read"]

FILENAME = "manifest.json"


@dataclass(frozen=True)
class Manifest:
    domain: str
    schema_version: str
    tool_version: str
    updated_at: str
    rows: int
    classified: int
    bugfixes: int
    security_fixes: int
    by_classifier: dict[str, int]
    repos: int
    columns: list[str]
    unknown_columns: list[str]

    def to_dict(self) -> dict[str, Any]:
        return {
            "domain": self.domain,
            "schema_version": self.schema_version,
            "tool_version": self.tool_version,
            "updated_at": self.updated_at,
            "rows": self.rows,
            "classified": self.classified,
            "bugfixes": self.bugfixes,
            "security_fixes": self.security_fixes,
            "by_classifier": self.by_classifier,
            "repos": self.repos,
            "columns": self.columns,
            "unknown_columns": self.unknown_columns,
        }


def path_for(domain: str) -> Path:
    return paths.domain_data_dir(domain) / FILENAME


def describe(domain: str, df: pd.DataFrame) -> Manifest:
    classifier = df["classifier"].fillna("") if "classifier" in df else pd.Series(dtype="string")
    return Manifest(
        domain=domain,
        schema_version=schema.SCHEMA_VERSION,
        tool_version=__version__,
        updated_at=time.strftime("%Y-%m-%dT%H:%M:%S%z"),
        rows=len(df),
        classified=int((classifier != "").sum()),
        bugfixes=int(df["is_bugfix"].fillna(False).sum()) if "is_bugfix" in df else 0,
        security_fixes=int(df["is_security_fix"].fillna(False).sum()) if "is_security_fix" in df else 0,
        by_classifier={k: int(v) for k, v in classifier[classifier != ""].value_counts().items()},
        repos=int(df["repo_slug"].nunique()) if "repo_slug" in df else 0,
        columns=list(df.columns),
        unknown_columns=[c for c in df.columns if c not in schema.BUGFIX_COLUMNS],
    )


def write(domain: str, df: pd.DataFrame) -> Path:
    m = describe(domain, df)
    p = path_for(domain)
    p.parent.mkdir(parents=True, exist_ok=True)
    tmp = p.with_suffix(".tmp")
    tmp.write_text(json.dumps(m.to_dict(), indent=1, ensure_ascii=False) + "\n",
                   encoding="utf-8")
    os.replace(tmp, p)
    return p


def read(domain: str) -> dict[str, Any] | None:
    p = path_for(domain)
    try:
        return json.loads(p.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None


def plan_migration(domain: str, df: pd.DataFrame) -> list[str]:
    """What would change if this table were rewritten under the current schema.

    Returns human-readable lines, empty when the table is already current. The
    point is to be able to look before leaping: a migration that silently
    rewrote 34,000 rows would be indistinguishable from a bug.
    """
    notes: list[str] = []
    prior = read(domain)
    if prior is None:
        notes.append("no manifest — this table predates provenance tracking")
    else:
        if prior.get("schema_version") != schema.SCHEMA_VERSION:
            notes.append(f"schema {prior.get('schema_version')} -> {schema.SCHEMA_VERSION}")
        if prior.get("tool_version") != __version__:
            notes.append(f"written by oss-bugfixes {prior.get('tool_version')}, "
                         f"now {__version__}")

    missing = [c for c in schema.BUGFIX_COLUMNS if c not in df.columns]
    if missing:
        notes.append(f"{len(missing)} column(s) will be added empty: "
                     + ", ".join(missing[:8]) + ("…" if len(missing) > 8 else ""))
    extra = [c for c in df.columns if c not in schema.BUGFIX_COLUMNS]
    if extra:
        notes.append(f"{len(extra)} column(s) not in the schema will be KEPT: "
                     + ", ".join(extra[:8]) + ("…" if len(extra) > 8 else ""))
    wrong = [c for c in schema.BUGFIX_COLUMNS
             if c in df.columns and str(df[c].dtype) != schema._dtype_for(c)]
    if wrong:
        notes.append(f"{len(wrong)} column(s) will be re-typed: "
                     + ", ".join(wrong[:8]) + ("…" if len(wrong) > 8 else ""))
    return notes
