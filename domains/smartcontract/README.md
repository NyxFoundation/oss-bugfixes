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
