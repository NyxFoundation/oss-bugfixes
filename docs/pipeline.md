# The pipeline

Five stages. Each writes to its own place, each is separately re-runnable, each
records per-repository completion so an interrupted run resumes instead of
starting over.

```
domains/<d>/repos.yml
        │
    ① mirror    →  work/<d>/mirrors/<slug>.git          bare, blobless
        │
    ② walk      →  work/<d>/commits/<slug>.parquet      one row per commit
        │
    ③ signals   →  work/<d>/candidates/<slug>.parquet   + deterministic columns
        │
    ④ diffs     →  work/<d>/diffs/<slug>/<ab>/<sha>.diff.gz
        │
    ⑤ classify  →  domains/<d>/data/bugfixes.csv
```

`work/` is gitignored and entirely reproducible from the registry. Losing it
costs time, never data.

---

## ① mirror

One bare, blobless clone per repository.

Git transport, not the REST API. The forge caps REST at 5,000 requests/hour and
serves secondary-rate-limit 403s well before that, which is the single thing
that makes a whole-history crawl of a few hundred repositories impractical.
`git clone` and `git fetch` are subject to neither.

`--filter=blob:none` gives the full commit and tree graph immediately and leaves
file contents on the server. That is what lets a 181-repo domain mirror in
minutes instead of filling a disk.

```bash
oss-bugfixes mirror --domain wallet --tier 1 --workers 8
oss-bugfixes mirror --domain wallet --refresh      # fetch new commits
oss-bugfixes mirror --domain wallet --hydrate      # also pull every blob
```

**When to `--hydrate`.** A blobless mirror fetches each missing blob on first
access — one network round trip per object. Anything that reads file *contents*
over a whole history therefore crawls. Measured on `P-H-C/phc-winner-argon2`
(496 commits), fresh clone each time:

| | |
|---|---:|
| blobless clone | 1.6 s, 0.3 MB |
| …then diff the whole history lazily | 214.7 s |
| blobless clone + hydrate | 0.6 s + 0.9 s, 6.4 MB |
| …then diff the whole history | 2.0 s |

Hydrate before `--line-counts`, before backport detection, and before diffing a
whole history. Do not hydrate a registry you only intend to enumerate.

Failure modes: a half-written clone from a killed run is deleted rather than
reused (git refuses to clone into it and every later stage reads an empty
graph); a failed `fetch` leaves the existing mirror intact, so the walk proceeds
on slightly stale history rather than losing the repository.

---

## ② walk

Every commit in every branch, with no keyword filter.

```bash
oss-bugfixes walk --domain wallet --tier 1
oss-bugfixes walk --domain crypto --limit 500          # smoke run
oss-bugfixes walk --domain crypto --line-counts        # needs a hydrated mirror
```

Only structural filters apply — see the table in the README. `--max-files 0`
disables the cap; `--keep-non-source` keeps documentation-only commits.

**Why `--name-only` by default.** Line counts force git to diff file contents,
which on a blobless mirror is a lazy fetch per commit (214.7 s vs under 0.1 s on
argon2). File paths come from trees, which a blobless clone already has. Ask for
`--line-counts` only against a hydrated mirror.

**Backport detection.** `git patch-id` hashes the diff, so the same change
cherry-picked to a maintenance branch keeps one patch-id under two shas.
Backporting is extra work a team only does when users on the old release cannot
wait — the developers' own statement of urgency, made by action rather than in
words, which is why it survives where a keyword does not.

It is additive metadata and never decides whether a repository gets enumerated:

- skipped above 6,000 commits (the render never finishes inside the budget, so
  attempting it costs the timeout to learn nothing);
- skipped on a blobless mirror, with that said in the result, rather than
  spending 90 s failing;
- on failure, the column is `<NA>` and the commits are unaffected.

`is_backport` is **never** written as `False` when detection did not run. That
confusion once reported "0 backported" for two repositories that had in fact
timed out.

Performance notes that are not negotiable: one `git log` for the whole history
(a per-commit `git show` spends all its time on process spawn), parsed as a
stream (a 600k-commit monorepo does not fit in memory as one string), and the
`git log -p | git patch-id` pipeline owns its process group so a timeout
actually kills it — an orphaned `patch-id` once burned a core for half an hour
past its deadline.

---

## ③ signals

Deterministic annotations: advisory ids, fix verbs, impact classes, a weighted
keyword score, and the structural signals from the walk.

```bash
oss-bugfixes signals --domain wallet
oss-bugfixes signals --domain wallet --min-signals 1    # scope a later budget
```

**These are signals, not verdicts.** Nothing here may decide that a commit is or
is not a bug fix — that is the whole point of walking every commit. A row firing
no signal is not dropped; `prefilter` records what a triage policy *would* do,
and `classify --min-signals 0` ignores it.

Two rules learned the expensive way:

- a fix verb counts only alongside an impact class, because `fix typo` matches
  every fix-verb regex ever written;
- the keyword score is computed over *unique* tokens. A commit saying "attacker"
  nine times makes one claim about an attacker. Counting occurrences is how
  release-note boilerplate scored itself to 1.0 and produced 95 bogus Critical
  findings in the predecessor corpus.

---

## ④ diffs

Materialises the patches the classifier will read, gzipped and content-addressed
under `work/<d>/diffs/`.

```bash
oss-bugfixes diffs --domain wallet --min-signals 1
oss-bugfixes diffs --domain wallet --limit-per-repo 500
```

Scope it the same way you will scope `classify`. The stage picks its own
transport: below ~200 uncached patches it fetches them lazily, above that it
hydrates the mirror first so every `git show` is local. That crossover comes
from the measurement above — a lazy diff cost ~0.43 s on argon2 while hydrating
the whole repository cost 0.9 s.

A diff that cannot be produced is recorded as `None`, not as an empty string. A
blobless mirror whose upstream deleted the branch genuinely cannot produce the
blobs, and "" would read as "this commit changed nothing".

---

## ⑤ classify

The only stage that decides what a commit means.

```bash
oss-bugfixes classify --domain wallet --engine heuristic
oss-bugfixes classify --domain wallet --engine llm:openai \
    --model qwen3-coder --base-url https://ollama.com/v1 --workers 8
oss-bugfixes classify --domain wallet --reclassify --engine llm:claude
```

Rows already carrying a verdict are skipped unless `--reclassify`. Replies are
cached by `(model, prompt)` under `work/<d>/llm_cache.json`, so a rerun after a
crash re-asks nothing. Changing the prompt changes the key, which is correct —
a different question deserves a different answer — and does mean a prompt
change discards the cache.

**Three things happen before the model is asked anything**, and each is there
because of a measured failure or a measured cost:

| step | effect |
|---|---|
| comments stripped from the diff | **22% smaller prompts**; a model that cannot see a comment cannot be fooled by one |
| provable refutations settled in-process (`--no-prefilter` to disable) | **14.6%** of commits never reach an endpoint |
| positives overruled by a provable refutation (`--no-veto` to disable) | a fact about the diff outranks a guess about it |

The middle one is what the deterministic engine was built for. A commit whose
diff changes no code token needs no model: the refutation was right 67 times
out of 67 where a model disagreed, and asking anyway is time spent being told
something already known.

The prompt follows LLM4VFD (arXiv:2501.14983): code-change intention, plus the
development artifacts, plus light structural context, answered as one JSON
object. Chain-of-thought is requested *inside* a `rationale` field rather than
as prose before the JSON, so a truncated reply still parses.

The deterministic signals are passed in explicitly, labelled "hints, not
conclusions", and their absence is labelled as normal rather than as evidence —
"(none fired — this is common for silent fixes)".

**Failures are undecided, never negative.** A timeout, a refusal or unparseable
output produces `is_bugfix=<NA>` with the error recorded. A corpus where
failures silently became `false` is a corpus whose negatives cannot be trusted.

### Choosing a model: prefer one that answers over one that thinks

Measured on the same commits, same prompts, one model at a time:

| | per commit | decided |
|---|---:|---:|
| `gpt-oss:120b` (max-tokens 2,000) | **2.6 s** | 3/3 |
| `glm-5.3-flash` (max-tokens 4,000) | 38.6 s | 2/3 |

A reasoning model spends its budget before it emits anything, which makes it
slow on exactly the commits worth reading — the ones with a real diff to work
through — and makes running out of budget *silent*. In a whole-corpus sweep
that compounds: the easy commits finish early, and what is left is the subset
the model thinks hardest about. Throughput on the remainder fell to 2.4
commits/minute with the reasoning model and rose to 67.9 with the other. The
same six workers.

Two warnings, both learned here:

* **Benchmark one model at a time.** Six models measured in parallel produced
  a ranking that was really a measure of contention, and it pointed at the
  wrong model.
* **Benchmark on the commits you still have to do.** Measuring on the head of
  the corpus measures the easy ones. The gap above only appears on the rows
  that had already failed once.

---

## Resuming, and what to do when something breaks

```bash
oss-bugfixes status --domain wallet
```

prints per-stage completion, which repositories failed, mirror disk usage and
the current CSV counts. To retry just the failures, delete their entries from
`work/<d>/state/<stage>.json` — it is a small, hand-editable JSON file precisely
so that a human debugging a skipped repository can do this — or re-run the stage
with `--force`.

### The classify stage, specifically

| symptom | cause | fix |
|---|---|---|
| every call returns `403 error code: 1010` | **not rate limiting.** Cloudflare blocks urllib's default `Python-urllib/3.x` announcement. Measured against the same endpoint, key and moment: `curl/8.0` → 200, no UA → 200, `Python-urllib/3.12` → **403** | a `User-Agent` is now always sent; override with `OSS_BUGFIXES_USER_AGENT` |
| a third of rows come back undecided, blank | a reasoning model spends its token budget before emitting anything, so running out is silent — empty `content`, or JSON cut mid-object | `--max-tokens` (default 4,000) |
| a rerun never retries the failures | a row carrying a classifier but no verdict was counted as done | fixed: a row is done when it has a verdict, or when a human set it. A failure is work still owed |
| more workers make it slower | real, and not the above. Measured on this endpoint: **6 workers → 14.5 commits/min, 16 workers → 10.0** — four of sixteen threads sat in backoff | 6 is the measured plateau here. Measure yours; do not assume |

That third row is the expensive one. 2,375 commits — 42% of a domain — were
recorded as permanently unclassifiable, and the corpus gave no sign of it: the
rows existed, carried a classifier name, and held `<NA>`, which is
indistinguishable from an engine that looked and declined. The distinction the
code lacked was *declined* versus *never answered*.

**A caution about measuring any of this.** Every latency figure taken through
this client before the User-Agent fix was wrong. Blocked-and-retried calls
read as a slow endpoint, which produced a "model benchmark" (11.8 s alone,
35.8 s under load) and a confident conclusion that throughput was
concurrency-limited. Both were artefacts of one missing header. When a sweep
looks slow, check what the endpoint says to a single `curl` before theorising
about load.

### General

| symptom | cause | fix |
|---|---|---|
| walk says `not attempted (blobless mirror)` | backport detection needs file contents | `mirror --hydrate` |
| walk is slow, `--line-counts` is on | lazy blob fetch per commit | `mirror --hydrate`, or drop the flag |
| `diffs` crawls | hundreds of lazy fetches | it hydrates itself above ~200; below that, scope with `--min-signals` |
| `no mirror — run mirror first` | the walk found no clone | `mirror --domain <d> --repo <slug>` |
| clone fails with auth prompt | private or renamed repository | `GIT_TERMINAL_PROMPT=0` is already set; fix the registry entry |
| classify records errors on every row | endpoint or model name wrong | `--engine heuristic` first to prove the rest of the chain |
