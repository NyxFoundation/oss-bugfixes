#!/usr/bin/env python3
"""import_legacy.py — bring the predecessor corpora into domains/<d>/data/.

This repository supersedes three separate efforts:

    NyxFoundation/ethereum-vuln-dataset              -> domains/blockchain
    NyxFoundation/wallet-vuln-dataset                -> domains/wallet
    hf:NyxFoundation/vulnerability-reports (defi)    -> domains/smartcontract

Their rows are kept in THEIR OWN schema, as `vulns.parquet`, rather than being
forced into this framework's `bugfixes.csv` shape. Two reasons:

  * those corpora carry fields this framework does not produce (expert-assigned
    severity, STRIDE/CWE labels, audit-contest provenance, silent-fix
    probabilities) and squeezing them into the commit schema would throw that
    away;
  * `bugfixes.csv` means "this framework walked this commit and classified it".
    A row that came from a 2026 audit-findings crawl did not, and labelling it
    as though it had would make the provenance of the corpus unreadable.

So a domain can hold both, and `docs/` in each domain says which is which.

What IS dropped: `pre_fix_code` and `post_fix_code`. They are 66-92% of the
bytes in both corpora and are reproducible from `fix_commit` through this
framework's own diff stage, so carrying them in git buys nothing.

Usage:
    uv run python scripts/import_legacy.py --all
    uv run python scripts/import_legacy.py --blockchain /path/to/ethereum_vulns.parquet
"""
from __future__ import annotations

import argparse
import re
import sys
from pathlib import Path

import pandas as pd

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from ossbugfix import paths, registry          # noqa: E402

#: Inline code blobs: the bulk of the bytes, and recoverable from fix_commit.
DROP_COLUMNS = ["pre_fix_code", "post_fix_code"]

#: Audit findings quote GitHub's image attachments, and GitHub serves those as
#: presigned S3 URLs whose query string carries an `X-Amz-Credential=AKIA…`.
#: They are not leaked keys — they are short-lived (`X-Amz-Expires=300`) request
#: signatures, long expired by the time anyone reads the corpus — but they trip
#: every secret scanner, and a dead signature is pure noise in a description.
#: Strip the query string and keep the URL.
PRESIGNED_RE = re.compile(
    r"(https?://[^\s)\]\'\"<>]*amazonaws\.com[^\s)\]\'\"<>]*?)\?[^\s)\]\'\"<>]*"
    r"X-Amz-(?:Credential|Signature|Security-Token)[^\s)\]\'\"<>]*",
    re.IGNORECASE)
REDACTION = r"\1?[presigned-query-stripped]"

#: Columns worth putting in the browsable preview, in order. Missing ones are
#: skipped, so one list serves all three corpora.
PREVIEW_COLUMNS = [
    "id", "source_platform", "title", "severity", "severity_estimated",
    "confidence", "authority_tier", "stride", "cwe_top25", "impact_type",
    "mechanism", "silent_fix_prob", "fix_commit", "source_url",
]

DEFAULTS = {
    "blockchain": "/home/gohan/workspace/ethereum-vuln-dataset/data/ethereum_vulns.parquet",
    "wallet": "/home/gohan/workspace/wallet-vuln-dataset/data/wallet_vulns.parquet",
}
HF_DEFI = "hf://datasets/NyxFoundation/vulnerability-reports/defi/train.parquet"


def import_one(domain: str, src: str, *, preview_rows: int = 1500) -> int:
    df = pd.read_parquet(src)
    before = len(df.columns)
    df = df.drop(columns=[c for c in DROP_COLUMNS if c in df.columns])
    redacted = _strip_presigned(df)

    out_dir = paths.domain_data_dir(domain)
    out_dir.mkdir(parents=True, exist_ok=True)
    parquet = out_dir / "vulns.parquet"
    df.to_parquet(parquet, index=False)

    cols = [c for c in PREVIEW_COLUMNS if c in df.columns] or list(df.columns)[:12]
    preview = df
    if "confidence" in df.columns:
        order = {"high": 0, "medium": 1, "low": 2}
        preview = df.assign(_o=df["confidence"].map(order).fillna(3)).sort_values("_o")
    preview.head(preview_rows)[cols].to_csv(out_dir / "vulns.preview.csv", index=False)

    unmatched = _check_slugs(domain, df)
    print(f"[{domain}] {len(df):,} rows, {before}->{len(df.columns)} cols "
          f"-> {parquet.relative_to(paths.repo_root())} "
          f"({parquet.stat().st_size / 1e6:.1f} MB)"
          + (f"; stripped {redacted} presigned S3 query strings" if redacted else ""))
    if unmatched:
        print(f"[{domain}] WARNING: {len(unmatched)} source_platform values are not "
              f"registry slugs: {sorted(unmatched)[:8]}", file=sys.stderr)
    return len(df)


def _strip_presigned(df: pd.DataFrame) -> int:
    """Remove AWS presigned-URL query strings from every text column, in place.

    Returns how many cells were touched, so the import says it out loud rather
    than quietly altering the source data.
    """
    n = 0
    for col in df.columns:
        # Not `dtype == object`: pandas reads parquet text as its own string
        # dtype, and that check silently skipped every column it should have
        # cleaned. Ask what the column holds, not how it is stored.
        if not (pd.api.types.is_string_dtype(df[col])
                or pd.api.types.is_object_dtype(df[col])):
            continue
        hit = df[col].astype("string").str.contains("X-Amz-", na=False)
        if not hit.any():
            continue
        n += int(hit.sum())
        df.loc[hit, col] = df.loc[hit, col].map(
            lambda v: PRESIGNED_RE.sub(REDACTION, v) if isinstance(v, str) else v)
    return n


def _check_slugs(domain: str, df: pd.DataFrame) -> set[str]:
    """A corpus whose repo identifiers do not line up with the registry is a
    corpus nobody can join back to a repository — worth saying out loud."""
    if "source_platform" not in df.columns:
        return set()
    try:
        known = {r.slug for r in registry.load(domain)}
    except registry.RegistryError:
        return set()
    seen = set(df["source_platform"].dropna().astype(str).unique())
    return seen - known


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--all", action="store_true")
    ap.add_argument("--blockchain", nargs="?", const=DEFAULTS["blockchain"])
    ap.add_argument("--wallet", nargs="?", const=DEFAULTS["wallet"])
    ap.add_argument("--smartcontract", nargs="?", const=HF_DEFI)
    a = ap.parse_args()

    jobs: list[tuple[str, str]] = []
    if a.all:
        jobs = [("blockchain", DEFAULTS["blockchain"]),
                ("wallet", DEFAULTS["wallet"]),
                ("smartcontract", HF_DEFI)]
    else:
        for d in ("blockchain", "wallet", "smartcontract"):
            if getattr(a, d):
                jobs.append((d, getattr(a, d)))
    if not jobs:
        ap.error("nothing to do; pass --all or one of --blockchain/--wallet/--smartcontract")

    failed = 0
    for domain, src in jobs:
        try:
            import_one(domain, src)
        except (OSError, ValueError) as exc:
            print(f"[{domain}] FAILED: {type(exc).__name__}: {exc}", file=sys.stderr)
            failed += 1
    return 1 if failed else 0


if __name__ == "__main__":
    raise SystemExit(main())
