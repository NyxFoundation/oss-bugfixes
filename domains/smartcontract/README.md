# domains/smartcontract

**Scope rule.** A repository belongs here when its code is **deployed to a chain**
and a defect in it puts value held by that deployment at risk. Node software
belongs in [`../blockchain`](../blockchain); key custody in
[`../wallet`](../wallet).

## Coverage — 40 repositories

Two kinds of entry, mixed on purpose:

- the widely-forked **libraries** — OpenZeppelin, Solady, Solmate, Permit2,
  solana-program-library, Anchor. A defect in one of these is a defect in every
  protocol that inherited it, which is what makes their fix history
  disproportionately valuable.
- the large protocol **deployments** — Uniswap v3/v4, Aave v3, Compound Comet,
  Morpho Blue, Lido, EigenLayer, Safe, Seaport, Wormhole, Chainlink. These carry
  the fixes that happened *after* an audit, which is exactly the population an
  audit tool should be scored against.

By category: library 10 · dex 8 · lending 6 · bridge 5 · staking 4 · oracle 3 ·
account 2 · marketplace 1 · framework 1. By tier: 17 / 13 / 10.

## `data/bugfixes.csv` — 5,607 commits walked and classified

Five repositories — `openzeppelin-contracts` (3,189 commits), `morpho-blue`
(1,111), `safe-contracts` (705), `solmate` (338), `permit2` (264) — walked in
full, every diff read, every commit classified.

| | |
|---|---:|
| commits | **5,607** |
| bug fixes | **1,182** |
| security fixes | **672** |
| severity | High 416 · Medium 195 · Low 40 · Critical 20 · Info 1 |
| classified by | `llm:openai` 5,074 · `structural` 533 |
| unclassified | **0** |

### 95% of the security fixes are silent

This is the measurement the whole repository is built to take:

| of 672 security fixes | |
|---|---:|
| carrying a CVE / GHSA / RUSTSEC id | **8 (1.2%)** |
| firing **no** deterministic signal at all | **637 (95%)** |

A keyword crawl would have found about one in twenty. What the other
nineteen look like:

```
feat: independent collateral                    High    CWE-840
fix: lastUpdate issue                           High    CWE-682
fix: minor issues                               Medium  CWE-193
fix: Math.min                                   High    CWE-682
fix: transfer direction, removing int casting   High    CWE-191
feat: 0 amount liquidation and check bucket     Medium  CWE-129
```

Three of those are labelled `feat`. None mentions security. All were read as
diffs, not as sentences.

### The deterministic engine on this corpus

Measured against the 5,062 model-decided rows
([`scripts/evaluate_structural.py`](../../scripts/evaluate_structural.py)):

| | precision | recall |
|---|---:|---:|
| `is_bugfix` | 0.46 | **1.00** |
| `is_security_fix` | 0.34 | **0.97** |

…while declining to judge 89% of them. High recall, low precision, mostly
silent: a filter and a ranking, not a classifier. See
[`../../docs/deterministic-classification.md`](../../docs/deterministic-classification.md).

## `data/vulns.parquet` — 4,470 expert-judged audit findings

Migrated from
[`hf:NyxFoundation/vulnerability-reports`](https://huggingface.co/datasets/NyxFoundation/vulnerability-reports)
(`defi` config). Browse [`vulns.preview.csv`](data/vulns.preview.csv).

| | |
|---|---|
| severity | Medium 2,720 · High 1,750 |
| platform | code4rena 3,799 · sherlock 508 · codehawks 163 |

**This corpus is shaped differently from the other two, and that is not a
defect.** Its rows are *contest findings*, so `source_platform` is the audit
platform rather than a repository slug, and the rows do not join to
`repos.yml` at all. The registry and the corpus deliberately cover different
ground here:

> the corpus records what auditors already **found**;
> the registry lists code to **walk** from now on.

Comparing the two is the interesting experiment in this domain — a finding says
what an auditor caught, a commit says what shipped.

## A note on Sherlock

508 of these findings come from Sherlock contests. Sherlock ran only nine public
contests in 2026, the last starting 2026-07-20, and has shifted toward its
AI-driven Audit Engine; its continuously-running bug bounty programs remain
active. Treat the contest-sourced slice as a closed population rather than a
growing one.
