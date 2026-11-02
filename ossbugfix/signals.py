"""signals.py — deterministic annotations on top of the commit frame.

These are SIGNALS, not a verdict. The whole point of walking every commit is
that a keyword cannot tell a silent fix from a refactor, so nothing here is
allowed to decide that a commit is or is not a bug fix. What they are for:

  * **triage order.** Classifying 600k commits with a model costs real money.
    Signals give a defensible order to spend that budget in, and
    `--min-signals` lets a run scope itself without pretending the rest were
    examined and rejected.
  * **evidence.** A commit that carries a CVE id is a fix on authority, and the
    classifier should be told so rather than having to infer it.
  * **calibration.** The signal pass and the model disagreeing is the most
    informative thing in the corpus; it is how `docs/` learns what keyword
    crawls were missing.

A row that fires no signal is NOT dropped by default. `prefilter` records what
a triage policy *would* do, and `oss-bugfixes classify --all` ignores it.
"""
from __future__ import annotations

import json
import re

import pandas as pd

from . import schema

__all__ = ["annotate", "ADVISORY_RE", "FIX_VERB_RE", "IMPACT_PATTERNS", "KEYWORD_WEIGHTS"]

#: CVE-2024-1234 / GHSA-xxxx-xxxx-xxxx / RUSTSEC-2024-0001 / GO-2024-1234
ADVISORY_RE = re.compile(
    r"\b(CVE-\d{4}-\d{4,7}"
    r"|GHSA-[0-9a-z]{4}-[0-9a-z]{4}-[0-9a-z]{4}"
    r"|RUSTSEC-\d{4}-\d{4}"
    r"|GO-\d{4}-\d{4,})\b",
    re.IGNORECASE,
)

#: A verb that claims something was wrong before this commit. On its own this is
#: weak — "fix typo" matches — which is why it only counts as a signal when it
#: co-occurs with an impact class below.
FIX_VERB_RE = re.compile(
    r"\b(fix(?:e[sd])?|fixing|patch(?:e[sd])?|correct(?:s|ed)?|resolve[sd]?"
    r"|repair(?:s|ed)?|prevent(?:s|ed)?|avoid(?:s|ed)?|guard(?:s|ed)?"
    r"|harden(?:s|ed)?|mitigat(?:e|es|ed)|sanitiz(?:e|es|ed)"
    r"|validat(?:e|es|ed)|reject(?:s|ed)?)\b",
    re.IGNORECASE,
)

#: Impact classes, keyed by the failure a reader would recognise. The value is
#: the pattern; the key is what lands in `impact_class`.
IMPACT_PATTERNS: dict[str, re.Pattern[str]] = {
    "memory": re.compile(
        r"\b(buffer overflow|heap overflow|stack overflow|out[- ]of[- ]bounds|oob"
        r"|use[- ]after[- ]free|double free|memory (?:leak|corruption)"
        r"|uninitialized (?:memory|read)|segfault|segmentation fault)\b", re.I),
    "crash": re.compile(
        r"\b(panic(?:s|ked|king)?|crash(?:es|ed)?|abort(?:s|ed)?|assertion failure"
        r"|unwrap on none|index out of range|nil pointer|null pointer"
        r"|divide by zero|division by zero)\b", re.I),
    "overflow": re.compile(
        r"\b(integer overflow|underflow|wrap(?:ping|around)|arithmetic overflow"
        r"|precision loss|rounding error|off[- ]by[- ]one)\b", re.I),
    "auth": re.compile(
        r"\b(authentication bypass|authorization bypass|access control"
        r"|privilege escalation|permission check|missing (?:auth|check)"
        r"|signature (?:verification|validation|malleability)|replay attack"
        r"|unauthorized)\b", re.I),
    "crypto": re.compile(
        r"\b(timing (?:attack|leak|side[- ]channel)|constant[- ]time|side[- ]channel"
        r"|nonce reuse|weak (?:random|entropy|prng)|predictable (?:seed|nonce|key)"
        r"|key (?:leak|exposure|recovery)|invalid curve|small subgroup"
        r"|padding oracle|fault injection)\b", re.I),
    "injection": re.compile(
        r"\b(xss|cross[- ]site scripting|csrf|sql injection|command injection"
        r"|code injection|path traversal|directory traversal|prototype pollution"
        r"|deserializ\w+ (?:flaw|vulnerab\w+)|ssrf|open redirect)\b", re.I),
    "dos": re.compile(
        r"\b(denial of service|dos attack|resource exhaustion|infinite loop"
        r"|unbounded (?:loop|allocation|growth)|memory exhaustion|oom"
        r"|rate limit|deadlock|livelock|gas (?:griefing|limit))\b", re.I),
    "leak": re.compile(
        r"\b(information (?:leak|disclosure|exposure)|data leak|secret (?:leak|exposure)"
        r"|credential (?:leak|exposure)|sensitive data|private key (?:in|exposed))\b", re.I),
    "consensus": re.compile(
        r"\b(consensus (?:bug|failure|split)|chain split|fork choice|reorg"
        r"|non[- ]determinism|state (?:divergence|mismatch)|invalid (?:block|state root))\b", re.I),
    "funds": re.compile(
        r"\b(loss of funds|fund(?:s)? (?:at risk|drain|theft)|steal(?:ing)? (?:funds|tokens)"
        r"|reentranc\w+|front[- ]runn\w+|price manipulation|oracle manipulation"
        r"|flash loan|slippage|double[- ]spend)\b", re.I),
}

#: Words that raise the security score on their own, with how much. Chosen so
#: that no single weak term can reach the 0.5 the triage policy uses.
KEYWORD_WEIGHTS: dict[str, float] = {
    "vulnerability": 0.5, "vulnerable": 0.45, "exploit": 0.5, "attack": 0.35,
    "attacker": 0.45, "malicious": 0.4, "security": 0.3, "cve": 0.5,
    "advisory": 0.35, "disclosure": 0.3, "bounty": 0.3, "audit": 0.2,
    "unsafe": 0.25, "unchecked": 0.25, "bypass": 0.4, "spoof": 0.4,
    "forge": 0.25, "tamper": 0.35, "hijack": 0.45, "poison": 0.35,
    "untrusted": 0.3, "sanitize": 0.25, "escalation": 0.4, "severity": 0.2,
}

_TOKEN_RE = re.compile(r"[a-z]+")


def annotate(df: pd.DataFrame, *, min_signals: int = 0) -> pd.DataFrame:
    """Add `SIGNAL_COLUMNS` to a commit frame. Pure, deterministic, offline.

    `min_signals=0` (the default) marks every row `keep`: the signal pass
    annotates, it does not gate. A higher value writes `drop:below_min_signals`
    into `prefilter`, which only the triage policy reads.
    """
    if not len(df):
        return schema.conform(df, schema.COMMIT_COLUMNS + schema.SIGNAL_COLUMNS)

    out = df.copy()
    text = (out["subject"].fillna("") + "\n" + out["body"].fillna("")).str.lower()

    ids = text.map(lambda t: " ".join(sorted({m.group(0).upper()
                                              for m in ADVISORY_RE.finditer(t)})))
    out["advisory_ids"] = ids
    out["has_advisory_id"] = (ids.str.len() > 0)

    out["fix_verb"] = text.map(lambda t: (m.group(0).lower()
                                          if (m := FIX_VERB_RE.search(t)) else ""))
    out["impact_class"] = text.map(_impact_class)
    out["keyword_score"] = text.map(_keyword_score)

    # `is_backport` / `n_patch_siblings` come from the walk; if this frame has
    # not been through it, treat them as unknown rather than inventing False.
    for col in ("is_backport", "n_patch_siblings"):
        if col not in out.columns:
            out[col] = pd.Series([pd.NA] * len(out), index=out.index,
                                 dtype="boolean" if col == "is_backport" else "Int64")

    out["n_signals"] = (
        out["has_advisory_id"].fillna(False).astype(int)
        + (out["keyword_score"] >= 0.5).fillna(False).astype(int)
        # A fix verb counts only together with a recognisable impact: "fix typo"
        # is a fix verb and is not a defect report.
        + ((out["fix_verb"].str.len() > 0) & (out["impact_class"].str.len() > 0))
          .fillna(False).astype(int)
        + out["is_backport"].fillna(False).astype(int)
        + out["is_revert"].fillna(False).astype(int)
    ).astype("Int64")

    out["prefilter"] = (
        "keep" if min_signals <= 0
        else out["n_signals"].fillna(0).map(
            lambda n: "keep" if n >= min_signals else "drop:below_min_signals")
    )
    return schema.conform(out, schema.COMMIT_COLUMNS + schema.SIGNAL_COLUMNS)


def _impact_class(text: str) -> str:
    hits = [name for name, pat in IMPACT_PATTERNS.items() if pat.search(text)]
    return " ".join(hits)


def _keyword_score(text: str) -> float:
    """Weighted vocabulary match, saturating at 1.0.

    Scored over unique tokens, not occurrences: a commit that says "attacker"
    nine times is one claim about an attacker, and counting it nine times is how
    release notes score themselves to 1.0.
    """
    tokens = set(_TOKEN_RE.findall(text))
    total = sum(w for term, w in KEYWORD_WEIGHTS.items() if term in tokens)
    return round(min(total, 1.0), 3)


def files_of(row: pd.Series | dict) -> list[str]:
    """Decode the `files_changed` cell. Tolerates an already-decoded list."""
    raw = row["files_changed"] if isinstance(row, dict) else row.get("files_changed")
    if isinstance(raw, list):
        return raw
    if not isinstance(raw, str) or not raw.strip():
        return []
    try:
        val = json.loads(raw)
    except json.JSONDecodeError:
        return []
    return val if isinstance(val, list) else []
