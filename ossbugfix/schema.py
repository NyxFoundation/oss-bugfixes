"""schema.py — the three tables this pipeline produces, named once.

Keeping the column lists here (rather than letting each stage invent its own)
is what makes a domain interchangeable: a consumer who has learned to read
`domains/wallet/data/bugfixes.csv` can read the `crypto` one without looking.

    COMMIT_COLUMNS      one row per commit, pure git facts
    SIGNAL_COLUMNS      deterministic signals added on top (no model involved)
    VERDICT_COLUMNS     what a classifier adds
    BUGFIX_COLUMNS      the published table = commit + signals + verdict

`files_changed` is stored as a JSON array in a single cell. A CSV cell holding
JSON is uglier than a separate table, but it keeps one row = one commit, which
is what every downstream consumer of these corpora has actually wanted.
"""
from __future__ import annotations

from typing import Any

import pandas as pd

#: Bumped whenever the meaning or membership of the column lists changes.
#: Stored alongside every artifact so a later run can tell whether what is on
#: disk was produced by the same contract it is about to append to. An
#: incremental stage refuses to append across a bump and re-walks instead,
#: because a frame whose rows were made two different ways is worse than a
#: frame that took longer to build.
SCHEMA_VERSION = "1"

# --- one row per commit, straight from git ---------------------------------
COMMIT_COLUMNS: list[str] = [
    "domain",           # smartcontract | blockchain | wallet | os | crypto
    "repo_slug",        # registry key, stable across upstream renames
    "repo",             # owner/name on the forge
    "sha",              # full 40-hex commit id
    "parents",          # space-separated parent shas ("" for a root commit)
    "author_name",
    "author_email",
    "authored_at",      # ISO-8601, author timestamp
    "committed_at",     # ISO-8601, committer timestamp
    "subject",          # first line of the message
    "body",             # the rest of the message
    "files_changed",    # JSON array of paths
    "n_files",
    "insertions",
    "deletions",
    "touches_source",   # bool: at least one path looks like source code
    "is_revert",        # bool: conventional revert commit
    "languages",        # space-separated, inferred from file extensions
]

# --- deterministic signals, computed without a model -----------------------
SIGNAL_COLUMNS: list[str] = [
    "advisory_ids",     # space-separated CVE / GHSA / RUSTSEC ids found in text
    "has_advisory_id",
    "fix_verb",         # the matched fix verb, "" if none
    "impact_class",     # panic | overflow | oob | auth | leak | dos | ...
    "keyword_score",    # 0.0-1.0, weighted security vocabulary match
    "is_backport",      # <NA> when detection was skipped — never False
    "n_patch_siblings", # how many shas share this commit's patch-id
    "n_signals",        # how many independent signals fired
    "prefilter",        # keep | drop:<reason>
]

# --- what a classifier is allowed to write ---------------------------------
VERDICT_COLUMNS: list[str] = [
    "is_bugfix",        # bool | <NA>
    "is_security_fix",  # bool | <NA>
    "severity",         # Critical | High | Medium | Low | Info | ""
    "cwe",              # e.g. CWE-125
    "stride",           # Spoofing | Tampering | ... | ""
    "confidence",       # 0.0-1.0
    "rationale",        # one or two sentences from the classifier
    "classifier",       # engine name, e.g. "llm:claude" / "heuristic"
    "model",            # concrete model id, "" for deterministic classifiers
    "classified_at",    # ISO-8601
]

BUGFIX_COLUMNS: list[str] = (
    ["id", "url"] + COMMIT_COLUMNS + SIGNAL_COLUMNS + VERDICT_COLUMNS
)

BOOL_COLUMNS: frozenset[str] = frozenset(
    {"touches_source", "is_revert", "has_advisory_id", "is_backport",
     "is_bugfix", "is_security_fix"}
)
INT_COLUMNS: frozenset[str] = frozenset(
    {"n_files", "insertions", "deletions", "n_patch_siblings", "n_signals"}
)
FLOAT_COLUMNS: frozenset[str] = frozenset({"keyword_score", "confidence"})

#: identity of a row. Two rows with the same key are the same commit, even if
#: they were produced by different runs or different classifiers.
KEY_COLUMNS: tuple[str, str, str] = ("domain", "repo_slug", "sha")


def commit_id(domain: str, repo_slug: str, sha: str) -> str:
    """Stable row id. Readable on purpose — it is what a human pastes into a
    bug report, and it round-trips back to the commit URL."""
    return f"{domain}/{repo_slug}@{sha[:12]}"


def commit_url(repo: str, sha: str, forge: str = "github.com") -> str:
    return f"https://{forge}/{repo}/commit/{sha}"


def empty(columns: list[str]) -> pd.DataFrame:
    return pd.DataFrame({c: pd.Series(dtype=_dtype_for(c)) for c in columns})


def _dtype_for(col: str) -> str:
    if col in BOOL_COLUMNS:
        return "boolean"      # nullable: <NA> means "not decided yet"
    if col in INT_COLUMNS:
        return "Int64"
    if col in FLOAT_COLUMNS:
        return "Float64"
    return "string"


def conform(df: pd.DataFrame, columns: list[str], *,
            keep_extra: bool = False) -> pd.DataFrame:
    """Return `df` with `columns`, in order, with the declared dtypes.

    Missing columns are added empty rather than raising: a frame that has not
    been through the signals stage yet is still a valid commit frame, and the
    sink should be able to write it. That is what makes the schema additive —
    a table written by an older version still loads under a newer one.

    `keep_extra=True` appends columns the schema does not know about instead of
    dropping them. The published table uses it, because a column somebody added
    by hand is data, and dropping it on the next merge would be the pipeline
    quietly destroying work it did not understand.
    """
    out = df.copy()
    for col in columns:
        if col not in out.columns:
            out[col] = pd.Series([pd.NA] * len(out), index=out.index, dtype=_dtype_for(col))
    extra = [c for c in out.columns if c not in columns] if keep_extra else []
    out = out[columns + extra]
    for col in columns:
        want = _dtype_for(col)
        if str(out[col].dtype) == want:
            continue
        try:
            out[col] = out[col].astype(want)
        except (TypeError, ValueError):
            # a malformed cell must not take down a 600k-row frame; coerce
            # per-value and let the bad ones become <NA>.
            out[col] = _coerce(out[col], want)
    return out


def _coerce(s: pd.Series, want: str) -> pd.Series:
    if want in ("Int64", "Float64"):
        return pd.to_numeric(s, errors="coerce").astype(want)
    if want == "boolean":
        return s.map(_as_bool).astype("boolean")
    return s.astype("string")


def _as_bool(v: Any) -> Any:
    if isinstance(v, bool):
        return v
    if v is None or v is pd.NA:
        return pd.NA
    t = str(v).strip().lower()
    if t in ("true", "1", "yes", "y"):
        return True
    if t in ("false", "0", "no", "n"):
        return False
    return pd.NA
