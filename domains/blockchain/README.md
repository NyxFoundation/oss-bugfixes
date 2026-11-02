# domains/blockchain

**Scope rule.** A repository belongs here when a defect in it can **halt a chain,
split it, or let an invalid state be accepted as valid**.

That is the node's threat model, not the application's. Contracts deployed *on* a
chain belong in [`../smartcontract`](../smartcontract); key-custody software
belongs in [`../wallet`](../wallet).

## Coverage — 47 repositories

By layer: full 23 · execution 14 · consensus 9 · infra 1. By tier: 23 / 14 / 10.

The eleven production Ethereum clients are tier 1 and are the most interesting
rows in this domain for a specific reason: **the same specification is
implemented eleven times**, so a defect in one is a natural experiment about
whether the other ten got it right.

| | |
|---|---|
| Ethereum execution | geth · nethermind · besu · erigon · reth |
| Ethereum consensus | lighthouse · lodestar · nimbus · prysm · teku · grandine |
| Ethereum specs & libs | consensus-specs · execution-specs · revm · evmone · mev-boost |
| Bitcoin | bitcoin-core · btcd · lnd · core-lightning · eclair · libbitcoin |
| other L1 | solana (agave, firedancer) · cosmos-sdk · cometbft · ibc-go · polkadot-sdk · avalanchego · nearcore · aptos · sui · monero · zcash · cardano · algorand · stellar · xrpl · ton |
| L2 | op-stack · arbitrum-nitro · zksync-era · scroll · polygon-cdk · starknet |

The spec repositories are in scope deliberately: a spec change that fixes a
defect is the authoritative record of that defect, and usually predates every
client's fix for it.

## `data/vulns.parquet` — 2,225 curated fixes across the 11 clients + specs

Migrated from
[`NyxFoundation/ethereum-vuln-dataset`](https://github.com/NyxFoundation/ethereum-vuln-dataset).
Browse [`vulns.preview.csv`](data/vulns.preview.csv).

| | |
|---|---|
| severity | Unrated 1,332 · Info 750 · High 63 · Medium 57 · Low 20 · Critical 3 |
| confidence | medium 1,445 · low 454 · high 326 |
| authority tier | B_corroborated 1,573 · C_candidate 417 · A_authoritative 235 |
| silent-fix probability ≥ 0.70 | 692 of 897 scored |

**88.2% of rows carry neither an advisory id nor a rated severity.** That is not
a collection failure; it is the measurement. Most Ethereum-client security fixes
ship without a CVE, a GHSA or a severity — which is the entire argument for a
pipeline that does not start from one.

## Caveat on this corpus specifically

It was built by the **keyword-first** predecessor pipeline: a search crawl
decided what to look at, and a model rescued silent fixes afterwards. The
framework in this repository inverts that, walking whole histories first. So
treat `vulns.parquet` here as the keyword-era baseline and `bugfixes.csv` as the
whole-history result; the gap between them is worth measuring rather than
assuming.

Further reading: [`docs/limitations.md`](docs/limitations.md) ·
[`docs/severity_labeling.md`](docs/severity_labeling.md) ·
[`docs/silent_fix_detection.md`](docs/silent_fix_detection.md) ·
[`docs/analysis.md`](docs/analysis.md) ·
[`docs/critical_high_findings.md`](docs/critical_high_findings.md)
