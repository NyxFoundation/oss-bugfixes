# Schema

`domains/<domain>/data/bugfixes.csv` — one row per commit, identified by
`(domain, repo_slug, sha)`. Re-running a classifier over a commit updates its
row in place; it never adds a second one.

The column list lives in [`ossbugfix/schema.py`](../ossbugfix/schema.py) and
nowhere else. That is what makes domains interchangeable: having learned to read
`domains/wallet/data/bugfixes.csv`, you can read the `crypto` one without
looking.

## Identity

| column | meaning |
|---|---|
| `id` | `<domain>/<repo_slug>@<sha[:12]>` — readable on purpose; it is what a human pastes into a bug report |
| `url` | the commit on the forge |
| `domain` `repo_slug` `sha` | the key. `repo_slug` is the registry key, stable across upstream renames |

## Git facts (stage ②)

| column | notes |
|---|---|
| `parents` | space-separated; `""` for a root commit |
| `author_name` `author_email` | |
| `authored_at` `committed_at` | ISO-8601. They differ for a cherry-pick, which is why both are kept |
| `subject` `body` | first line, and the rest |
| `files_changed` | **a JSON array in one cell.** Uglier than a side table, but it keeps one row = one commit, which is what every consumer of these corpora has actually wanted. Decode with `ossbugfix.signals.files_of` |
| `n_files` | |
| `insertions` `deletions` | **0 unless the walk ran with `--line-counts`.** See [pipeline.md](pipeline.md#-walk) |
| `touches_source` | at least one path has a recognised source extension |
| `is_revert` | conventional revert commit |
| `languages` | space-separated, inferred from extensions |

## Deterministic signals (stage ③)

| column | notes |
|---|---|
| `advisory_ids` | space-separated CVE / GHSA / RUSTSEC / GO ids found in the message |
| `has_advisory_id` | |
| `fix_verb` | the matched verb, `""` if none. Weak on its own |
| `impact_class` | space-separated: `memory` `crash` `overflow` `auth` `crypto` `injection` `dos` `leak` `consensus` `funds` |
| `keyword_score` | 0.0–1.0, weighted, over **unique** tokens |
| `is_backport` | the same patch-id appears under more than one sha. `<NA>` when detection was skipped |
| `n_patch_siblings` | how many shas share this patch-id |
| `n_signals` | how many independent signals fired |
| `prefilter` | `keep` or `drop:<reason>`. Only a triage policy reads it; it is not a verdict |

## Verdict (stage ⑤)

| column | notes |
|---|---|
| `is_bugfix` | tri-state |
| `is_security_fix` | tri-state |
| `severity` | `Critical` `High` `Medium` `Low` `Info` or `""` |
| `cwe` | e.g. `CWE-125` |
| `stride` | `Spoofing` `Tampering` `Repudiation` `InformationDisclosure` `DenialOfService` `ElevationOfPrivilege` |
| `confidence` | 0.0–1.0, the engine's own calibration |
| `rationale` | one or two sentences citing what decided it |
| `classifier` | engine label, e.g. `llm:openai`, `heuristic`, `human` |
| `model` | concrete model id, `""` for deterministic engines |
| `classified_at` | ISO-8601 |

## `<NA>` is a value, and it is load-bearing

Every boolean column here is **tri-state**, and the distinction is the single
easiest thing to lose:

| value | means |
|---|---|
| `True` | the check ran and fired |
| `False` | the check ran and did not fire |
| `<NA>` | **the check did not run**, or the engine declined |

Reporting `False` for a check that was skipped once produced "0 backported" for
two repositories that had in fact timed out — indistinguishable from
repositories that genuinely backport nothing, and it cost an analysis its
conclusion. The dtypes are nullable (`boolean`, `Int64`, `Float64`) end to end,
including through the CSV round trip, specifically to keep that distinction
alive.

When you filter, say which you mean:

```python
df[df.is_security_fix == True]        # decided yes
df[df.is_security_fix == False]       # decided no
df[df.is_security_fix.isna()]         # never decided — NOT the same as "no"
```

## Reading it

```python
import pandas as pd
df = pd.read_csv("domains/wallet/data/bugfixes.csv")

df[df.is_security_fix == True]                       # the security slice
df[(df.n_signals == 0) & (df.is_security_fix == True)]  # silent fixes: what a
                                                     # keyword crawl would miss
df[df.classifier == "human"]                         # hand-checked rows
```

That second query is the one this project exists for.

## The other table

`domains/<domain>/data/vulns.parquet` is the **migrated predecessor corpus** and
keeps its own, different schema. See [corpora.md](corpora.md).
