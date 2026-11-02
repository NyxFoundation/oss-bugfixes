# domains/wallet

**Scope rule.** A repository belongs here when a defect in it can lead to loss of
user **funds, key material, or signing authority**.

That is deliberately wider than "a wallet app". It covers the hardware firmware
that holds the seed, the smart-contract account that holds the balance, the
MPC/TSS library that shards the key, and the signing and transport libraries
every wallet UI is built on. Historically those libraries are where the severe,
cross-wallet bugs live: one defect in `bitcoinjs-lib` or `noble-curves` is
simultaneously a defect in a hundred wallets.

## Coverage — 181 repositories

| category | repos | | category | repos |
|---|---:|---|---|---:|
| wallet SDKs & signing libraries | 50 | | mobile | 24 |
| browser extensions / web | 26 | | smart accounts | 22 |
| MPC / threshold signatures | 19 | | desktop | 17 |
| hardware firmware | 10 | | full-node wallets | 7 |
| supporting infrastructure | 6 | | | |

By tier: 46 mass-market (1), 98 significant (2), 37 niche (3). 19 archived —
kept because the history is still evidence even when no new fixes will land.

Each entry also carries a `custody` model (`self` 65, `lib` 55, `smart` 27,
`mpc` 19, `hw` 15), which is this domain's own metadata and is passed through
untouched by the framework.

```bash
oss-bugfixes repos --domain wallet --tier 1
oss-bugfixes repos --domain wallet --category hardware_firmware
```

## `data/vulns.parquet` — 34,526 curated fixes across 174 repositories

The corpus migrated from
[`NyxFoundation/wallet-vuln-dataset`](https://github.com/NyxFoundation/wallet-vuln-dataset).
Browse [`vulns.preview.csv`](data/vulns.preview.csv); see
[`../../docs/corpora.md`](../../docs/corpora.md) for the schema and what was
dropped.

| | |
|---|---|
| severity | Unrated 21,754 · Info 11,648 · Medium 846 · High 228 · Low 49 · Critical 1 |
| confidence | medium 18,905 · high 9,657 · low 5,964 |
| authority tier | B_corroborated 21,463 · C_candidate 11,014 · A_authoritative 1,421 · A_dependency 628 |
| silent-fix probability ≥ 0.70 | **10,890 of 32,846 scored** |

That last row is the headline. A third of the corpus looks like a security fix
to a model reading the diff while carrying no advisory, no severity, and no
security wording — which is the measurement this whole project is built around.

Mechanism labels (this domain's own taxonomy) rank the ones that are classified:
`input-bounds-parsing` 1,076 · `signed-differs-from-shown` 1,028 ·
`authorization-check` 927 · `key-derivation-storage` 732 ·
`encoding-canonicalization` 579 · `signature-verification-gap` 470.

## Read this before relying on the data

[`docs/limitations.md`](docs/limitations.md). The short version: **absence here
is not evidence of security.** Closed-source wallets — Phantom, Exodus, OKX,
Bitget, SafePal, Coinomi, Atomic, Guarda, the exchange custodians — publish no
commit history, so no silent fix of theirs is observable by any method. An open
history is a precondition for appearing at all, so the corpus over-represents
projects that develop in public, and several products with large real-world
losses cannot be described here at all.

Also: [`docs/silent_fix_detection.md`](docs/silent_fix_detection.md).
