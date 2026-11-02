# Adding a domain, or a repository

## A repository

Edit `domains/<domain>/repos.yml`. That is the whole change — no crawler, script
or notebook in this repository hard-codes a repository name.

```yaml
  my-slug:
    repo: owner/name          # required; owner/name on the forge
    category: library         # free-form, used by --category
    tier: 2                   # 1 mass-market, 2 significant, 3 niche
    language: rust
    tags: [evm, signing]
    archived: false
```

Shorthand for an entry with no metadata:

```yaml
  my-slug: owner/name
```

Then:

```bash
oss-bugfixes validate --domain <domain>
oss-bugfixes repos    --domain <domain> --repo my-slug
oss-bugfixes run      --domain <domain> --repo my-slug --limit 200 --engine heuristic
```

**Tiers nest.** `--tier 2` means tiers 1 and 2. They exist so a crawl can be
scoped without editing the registry, and so a domain's docs can state coverage
honestly.

**One repository, one slug.** The loader rejects a registry that lists the same
`owner/name` twice, case-insensitively.

**Unknown keys are preserved, not rejected.** A domain may carry its own
metadata — a wallet's `custody`, a client's `layer`, a library's `primitive` —
and the framework passes it through to `Repo.extra` without needing to learn
about it.

## A domain

```bash
mkdir -p domains/<name>/{data,docs}
```

Write `domains/<name>/repos.yml`:

```yaml
domain: <name>                # must match the directory name
description: >-
  One sentence saying what belongs here and what does not.

defaults:
  forge: github.com

repos:
  first-slug:
    repo: owner/name
    tier: 1
```

Then `oss-bugfixes validate` and `oss-bugfixes domains`.

### Write the scope rule down first

Every registry in this repository opens with a **scope rule** — one sentence
that decides membership, phrased as the consequence of a defect rather than as a
category of software:

> *wallet*: a defect can lead to loss of user funds, key material, or signing
> authority.
>
> *blockchain*: a defect can halt a chain, split it, or let an invalid state be
> accepted as valid.
>
> *crypto*: a defect breaks a security guarantee rather than merely crashing.

This matters more than it looks. Phrased as consequence, the rule puts
`bitcoinjs-lib` in `wallet` (it holds no keys, but a defect in it costs users
their funds) and keeps deployed contracts out of `blockchain`. Phrased as
category — "wallet apps" — it would do neither, and the boundary would be
argued about in every pull request instead of being read off the file.

Say what is excluded, and why, in the same place. The wallet registry notes that
closed-source wallets are absent **by construction**: they publish no commit
history, so no silent fix of theirs is observable by any method.

### Source extensions

`walk.SOURCE_EXT` decides what counts as a source file. If a domain's language
is missing, add it there — not in a per-domain crawler, because there are no
per-domain crawlers.

## A classifier engine

Implement `classify.base.Classifier` and register it:

```python
from ossbugfix import classify
classify.register("mine", MyClassifier)
```

`oss-bugfixes classify --engine mine:somebackend` passes `somebackend` through
as the `backend` keyword. Two obligations:

- return `is_bugfix=None` when you could not decide. A failure is undecided, not
  negative.
- put something in `rationale` that cites what decided it. A verdict nobody can
  check is a verdict nobody will trust.
