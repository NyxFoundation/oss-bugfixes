# The migrated corpora

`domains/<domain>/data/vulns.parquet` holds the predecessor corpora this
repository supersedes. They are kept in **their own schema**, separate from the
`bugfixes.csv` this framework produces.

| domain | rows | from | what a row is |
|---|---:|---|---|
| `smartcontract` | 4,470 | [hf:NyxFoundation/vulnerability-reports](https://huggingface.co/datasets/NyxFoundation/vulnerability-reports) (`defi`) | one expert-judged audit finding |
| `blockchain` | 2,225 | [ethereum-vuln-dataset](https://github.com/NyxFoundation/ethereum-vuln-dataset) | one curated security fix across the 11 Ethereum clients |
| `wallet` | 34,526 | [wallet-vuln-dataset](https://github.com/NyxFoundation/wallet-vuln-dataset) | one curated security fix across 174 wallet repositories |

Reproduce with [`scripts/import_legacy.py`](../scripts/import_legacy.py).

## Why they are not folded into `bugfixes.csv`

Two reasons, and they are both about not lying.

**They carry fields this framework does not produce.** Expert-assigned severity,
STRIDE and CWE labels, audit-contest provenance, silent-fix probabilities,
authority tiers. Squeezing those into the commit schema would throw them away.

**`bugfixes.csv` means "this framework walked this commit and classified it".**
A row that came from an audit-findings crawl did not. Labelling it as though it
had would make the provenance of the corpus unreadable, and would quietly claim
the pipeline had examined rows it never saw.

So a domain can hold both, and they answer different questions: a finding says
what an auditor **caught**, a commit says what **shipped**.

## What was dropped, and why that is safe

`pre_fix_code` and `post_fix_code` — 66–92% of the bytes in both commit-derived
corpora (108 MB and 96 MB of a 310 MB wallet table). They are recoverable from
`fix_commit` through this framework's own `diffs` stage, so carrying them in git
buys nothing and costs every clone.

**AWS presigned-URL query strings** are stripped from every text column — 1 cell
in `smartcontract`, 2 in `wallet`. Audit findings quote GitHub image
attachments, and GitHub serves those as presigned S3 URLs whose query string
carries an `X-Amz-Credential=AKIA…`. Those are not leaked keys: they are
request signatures with `X-Amz-Expires=300`, dead five minutes after they were
issued in 2024. But they trip every secret scanner — GitHub push protection
blocked this repository's first push over exactly one of them — and a dead
signature carries no information. The URL is kept, the query string becomes
`?[presigned-query-stripped]`, and the import prints the count rather than
quietly altering its source.

Everything else is preserved verbatim.

## Joining back to the registries

`source_platform` is the repository slug in the `blockchain` and `wallet`
corpora, and every value matches a registry slug — 12/12 and 174/174 — so the
corpora join cleanly to `repos.yml`:

```python
import pandas as pd, yaml
vulns = pd.read_parquet("domains/wallet/data/vulns.parquet")
repos = yaml.safe_load(open("domains/wallet/repos.yml"))["repos"]
vulns["repo"] = vulns.source_platform.map(lambda s: repos.get(s, {}).get("repo"))
```

**The `smartcontract` corpus is different, and this is not a defect.** There,
`source_platform` is the audit *platform* — `code4rena` (3,799), `sherlock`
(508), `codehawks` (163) — because those rows are contest findings, not
repository commits. They are not keyed to a repository at all, which is exactly
why that domain's registry and its corpus cover different ground: the registry
lists code to walk from now on, the corpus records what auditors already found.
`import_legacy.py` prints a warning about this on every import rather than
silently coercing it.

## Severity is sparse, and the sparsity is the finding

In the `blockchain` corpus, 88.2% of rows carry **neither** an advisory id nor a
rated severity. That is not a collection failure — it is the measurement. Most
Ethereum-client security fixes ship without a CVE, a GHSA, or a severity, which
is the entire argument for a pipeline that does not start from one.
