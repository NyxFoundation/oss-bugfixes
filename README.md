# oss-bugfixes

A uniform harness for mining **bug-fix commits** out of open-source repositories,
across security domains — smart contracts, blockchain nodes, wallets, operating
systems and cryptography.

One framework, five registries, one output schema:

```
domains/<domain>/repos.yml   →  mirror  →  walk  →  signals  →  diffs  →  classify  →  CSV
   which repositories            git      every     deterministic   patches     a model
   (the only place a             clone    commit    annotations     from the    reads the
    repo is ever named)                   in every  (no model)      mirror      diff
                                          branch
```

Everything up to `classify` is deterministic, offline after the clone, resumable,
and free. `classify` is the only stage that needs a model.

## Why walk every commit instead of searching for "security"

Because the commit worth reading usually does not say so.

A keyword crawl decides what to look at from the words the author happened to
choose, which is exactly the assumption a **silent fix** breaks. The commit that
says `cleanup`, `refactor validation`, or `prevent unwanted wrapping` is the one
that quietly closed an integer overflow. Teams fix security bugs quietly all the
time — before a disclosure window, to avoid handing out an exploit, or because
nobody realised the bug was exploitable.

So this harness reads **all** of it and lets the classifier be the only judge of
meaning. The only things filtered before the model are structural facts about the
commit, never a guess at what it means:

| dropped | why |
|---|---|
| merge commits | carry no diff of their own |
| commits touching no source file | cannot contain a code defect |
| commits above `--max-files` (60) | vendored drops, generated output, formatting sweeps — not one defect, and the diff does not fit a prompt |

A refactor is **not** dropped. Neither is a commit whose message says nothing.
Those are the population this project exists to search.

## Install

```bash
uv venv && uv pip install -e .
oss-bugfixes validate            # every registry parses
oss-bugfixes domains             # what is covered
```

## Run it

```bash
# the deterministic half — no model, no API key, no cost
oss-bugfixes mirror  --domain crypto --tier 1 --workers 8
oss-bugfixes walk    --domain crypto --tier 1
oss-bugfixes signals --domain crypto
oss-bugfixes diffs   --domain crypto --min-signals 1

# the model half
oss-bugfixes classify --domain crypto --engine llm:openai --model qwen3-coder \
                      --base-url https://ollama.com/v1

# or all of it, with the no-model baseline
oss-bugfixes run --domain crypto --repo argon2 --engine heuristic
oss-bugfixes status --domain crypto
```

Every stage is separately re-runnable and skips what the state file says is
already done, because a 180-repo sweep takes hours and **will** be interrupted.
One repository failing never takes down the run; `status` tells you what to retry.

## Coverage

| domain | repos | scope rule | past-vulnerability corpus |
|---|---:|---|---|
| [`smartcontract`](domains/smartcontract) | 40 | deployed on-chain code and the contract libraries it inherits | 4,470 audit findings |
| [`blockchain`](domains/blockchain) | 47 | node software, where a defect can halt or split a chain | 2,225 client fixes |
| [`wallet`](domains/wallet) | 181 | anything whose defect costs funds, keys or signing authority | 34,526 wallet fixes |
| [`os`](domains/os) | 35 | code running at a privilege the applications above cannot revoke | — |
| [`crypto`](domains/crypto) | 42 | implementations where a defect breaks a guarantee, not just a process | — |

345 repositories. `domains/<d>/repos.yml` **is** the coverage claim — no crawler,
script or notebook in this repository hard-codes a repository name, so adding one
is a registry edit and nothing else.

## What a domain folder holds

```
domains/wallet/
  repos.yml      the registry — the only place a repository is named
  data/
    vulns.parquet         the past-vulnerability corpus (see docs/corpora.md)
    vulns.preview.csv     a slice GitHub renders as a table
    bugfixes.csv          what this framework produced (created on first run)
  docs/          scope notes, limitations, analysis
```

No code lives in a domain folder. No repository name lives outside one.

## The two tables, and why they are separate

`vulns.parquet` is the **predecessor corpora**, migrated in their own schema:
expert-judged audit findings and curated client/wallet fixes, carrying severity
ratings, STRIDE/CWE labels and contest provenance this framework does not
produce.

`bugfixes.csv` is **what this framework walked and classified**, in the schema in
[`docs/schema.md`](docs/schema.md).

They are kept apart on purpose. A finding says what an auditor caught; a commit
says what shipped. Folding one into the other would make the provenance of the
corpus unreadable, and would quietly claim this pipeline had examined rows it
never saw.

## Two measurements that shape the defaults

**Blobless mirrors make diffs expensive, and one fetch makes them cheap again.**
A `--filter=blob:none` clone leaves file contents on the server and fetches them
one at a time on first access — a network round trip per commit. On
`P-H-C/phc-winner-argon2` (496 commits), fresh clone each time:

| | |
|---|---:|
| blobless, diff the whole history lazily | **214.7 s** |
| blobless + `--hydrate`, then diff | **0.9 s + 2.0 s** |

A hundredfold, bought with 0.9 s and 6 MB. So `walk` defaults to `--name-only`
(file paths come from trees, which a blobless clone already has), line counts and
backport detection are opt-in behind `--hydrate`, and the `diffs` stage hydrates
a mirror by itself once it needs more than a couple of hundred patches.

**A fix verb alone is not a signal.** `fix typo` matches every fix-verb regex
ever written. The signal pass only counts a fix verb when it co-occurs with a
recognisable impact class, and scores keywords over *unique* tokens rather than
occurrences — counting repetitions is how release-note boilerplate once scored
itself to 1.0 and produced 95 bogus Critical findings.

## Signals are not verdicts

The deterministic pass annotates; it does not gate. A row that fires no signal is
still handed to the classifier. The signals exist to **order** a classification
budget, to **tell the model what is already known**, and to **measure** how much
a keyword crawl would have missed — the disagreement between the baseline and the
model is the most informative number this project produces.

`--min-signals N` scopes a run without pretending the rest were examined and
rejected: skipped rows are marked `drop:below_min_signals`, not labelled negative.

## Honesty rules the code keeps

These are not style preferences; each one is a bug this lineage already shipped.

- **A skipped check is `<NA>`, never `False`.** Backport detection that timed out
  once reported "0 backported", indistinguishable from a repo that backports
  nothing. Every tri-state column stays tri-state end to end.
- **A failed classification is undecided, not negative.** A timeout or an
  unparseable reply records the error; it never becomes `is_bugfix=false`.
- **A truncated diff says so, in the prompt.** A model handed a silently cut
  patch reasons confidently about code it never saw.
- **An optional column never costs a repository.** Backport detection failing
  loses the column and nothing else.
- **Human labels win.** A row whose `classifier` is `human` is not overwritten by
  a model unless you type `--overwrite-human`.

## Classifier engines

| engine | needs | for |
|---|---|---|
| `heuristic` | nothing | CI, baselines, and measuring what the model adds |
| `llm:claude` | the `claude` CLI | interactive work |
| `llm:openai` | any OpenAI-compatible endpoint | whole-corpus sweeps (Ollama Cloud, vLLM, LM Studio) |
| `llm:ollama` | a local ollama | offline work |

Replies are cached by `(model, prompt)`, so a rerun after a crash costs nothing
for the part that finished. Adding an engine is one `register()` call.

## Documentation

- [`docs/pipeline.md`](docs/pipeline.md) — each stage, its failure modes, what to do about them
- [`docs/schema.md`](docs/schema.md) — every column, and what `<NA>` means in it
- [`docs/adding-a-domain.md`](docs/adding-a-domain.md) — adding a domain or a repository
- [`docs/corpora.md`](docs/corpora.md) — where the migrated corpora came from

## Lineage

This supersedes three separate efforts, whose `collection/` directories were two
near-copies of the same pipeline:

- [`NyxFoundation/ethereum-vuln-dataset`](https://github.com/NyxFoundation/ethereum-vuln-dataset) → `domains/blockchain`
- [`NyxFoundation/wallet-vuln-dataset`](https://github.com/NyxFoundation/wallet-vuln-dataset) → `domains/wallet`
- [`NyxFoundation/vulnerability-reports`](https://huggingface.co/datasets/NyxFoundation/vulnerability-reports) → `domains/smartcontract`

The framework is the **wallet** lineage generalised. That corpus walks whole
histories and lets the model judge; the earlier Ethereum one started from keyword
search and therefore could not see a silent fix by construction. Where the two
disagreed, the wallet design won.

## Licence

MIT. The registries, the framework and the migrated corpora are all open.
