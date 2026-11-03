#!/usr/bin/env python3
"""evaluate_structural.py — how much of a model's judgement does the free
engine reproduce?

The deterministic classifier costs nothing and answers in milliseconds. The
question that decides how it should be used is not "is it accurate" but "where
is it accurate", and that has three separate answers:

  as a REFUTER   it says "no behaviour changed" from a fact about the diff.
                 This is its strong half and should be measured on its own.
  as a DETECTOR  it points at commits carrying a structural shape. High recall,
                 low precision — a ranking, not a verdict.
  as an ABSTAINER it declines on most of the corpus, which is the honest
                 answer and must not be scored as a wrong one.

Averaging those into one number hides all three. So this prints them apart.

    uv run python scripts/evaluate_structural.py --domain smartcontract
"""
from __future__ import annotations

import argparse
import gzip
import sys
from pathlib import Path

import pandas as pd

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from ossbugfix import paths, registry                      # noqa: E402
from ossbugfix.classify.base import is_failure             # noqa: E402
from ossbugfix.detect import classify, parse               # noqa: E402
from ossbugfix.sink import HUMAN_CLASSIFIER, Sink          # noqa: E402

PROVABLE = {"empty_diff", "no_token_change", "docs_only"}


def load_reference(domain: str) -> pd.DataFrame:
    """Rows a model actually decided. Failures are not a reference — a row
    whose endpoint never answered says nothing about anything."""
    df = Sink(domain).read()
    model = df["classifier"].fillna("").str.startswith("llm") | (
        df["classifier"].fillna("") == HUMAN_CLASSIFIER)
    decided = df["is_bugfix"].notna()
    failed = df["rationale"].map(is_failure)
    return df[model & decided & ~failed].copy()


def run(domain: str, limit: int = 0) -> int:
    ref = load_reference(domain)
    if limit:
        ref = ref.head(limit)
    if ref.empty:
        print(f"{domain}: no model-decided rows to evaluate against", file=sys.stderr)
        return 1

    reg = registry.load(domain)
    rows = []
    for _, r in ref.iterrows():
        slug, sha = str(r["repo_slug"]), str(r["sha"])
        p = paths.diff_cache_dir(domain) / slug / sha[:2] / f"{sha}.diff.gz"
        if not p.exists():
            continue
        with gzip.open(p, "rt", encoding="utf-8", errors="replace") as fh:
            v = classify(parse(fh.read()))
        rows.append({
            "sha": sha, "subject": r["subject"],
            "ref_bug": r["is_bugfix"], "ref_sec": r["is_security_fix"],
            "det_bug": v.is_bugfix, "det_sec": v.is_security_fix,
            "ground": v.ground.value,
            "sigs": " ".join(s.value for s in v.distinct),
        })
    m = pd.DataFrame(rows)
    print(f"{domain}: {len(ref):,} model-decided rows, {len(m):,} with a cached diff")
    print(f"  reference positives: bugfix {int((m.ref_bug == True).sum()):,} · "        # noqa: E712
          f"security {int((m.ref_sec == True).sum()):,}")                               # noqa: E712

    _detector(m)
    _refuter(m)
    _abstention(m)
    return 0


def _detector(m: pd.DataFrame) -> None:
    print("\n── as a detector (on the rows where it ventures a verdict) ──")
    for col, label in (("bug", "is_bugfix"), ("sec", "is_security_fix")):
        a = m[f"ref_{col}"].astype("boolean")
        b = m[f"det_{col}"].astype("boolean")
        d = b.notna()
        aa, bb = a[d], b[d]
        tp = int(((aa == True) & (bb == True)).sum())        # noqa: E712
        tn = int(((aa == False) & (bb == False)).sum())      # noqa: E712
        fp = int(((aa == False) & (bb == True)).sum())       # noqa: E712
        fn = int(((aa == True) & (bb == False)).sum())       # noqa: E712
        prec = tp / (tp + fp) if tp + fp else float("nan")
        rec = tp / (tp + fn) if tp + fn else float("nan")
        print(f"  {label:<16} TP={tp:<5} TN={tn:<5} FP={fp:<5} FN={fn:<4} "
              f"precision={prec:.2f} recall={rec:.2f}")


def _refuter(m: pd.DataFrame) -> None:
    """The strong half: a refutation is a fact about the diff, not an opinion."""
    ref = m[m["ground"].isin(PROVABLE)]
    agreed = int((ref.ref_bug == False).sum())               # noqa: E712
    print(f"\n── as a refuter (its one entitled negative) ──")
    print(f"  provable refutations in this sample: {len(ref):,}")
    print(f"  model agreed:                        {agreed:,}")
    if len(ref) < 20:
        print("    Small because `classify` now PREFILTERS: a commit the engine"
              "\n    refutes is settled in-process and never reaches a model, so"
              "\n    no comparison is collected for it. That saving and this"
              "\n    measurement are in tension — the earlier 67/67 was taken"
              "\n    before the prefilter existed. To re-measure, run a sample"
              "\n    with `--no-prefilter` and compare on those rows.")
    disagreed = ref[ref.ref_bug == True]                     # noqa: E712
    print(f"  model disagreed:      {len(disagreed):,}")
    for _, r in disagreed.head(5).iterrows():
        print(f"      · {str(r['subject'])[:64]}  [{r['ground']}]")
    if len(disagreed):
        print("    Check these by hand before believing either side: the engine"
              "\n    strips comments, and a model reading a diff as text has"
              "\n    been fooled by a comment about a constant before.")


def _abstention(m: pd.DataFrame) -> None:
    n = len(m)
    abstained = int(m["det_bug"].isna().sum())
    print(f"\n── as an abstainer ──")
    print(f"  declined to judge: {abstained:,}/{n:,} ({100 * abstained / max(1, n):.0f}%)"
          f" — this is the honest answer, not a wrong one")


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--domain", required=True)
    ap.add_argument("--limit", type=int, default=0)
    a = ap.parse_args()
    return run(a.domain, a.limit)


if __name__ == "__main__":
    raise SystemExit(main())
