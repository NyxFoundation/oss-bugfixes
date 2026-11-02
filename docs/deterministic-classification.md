# Classifying without a model

An LLM pass over a whole history is the expensive part of this pipeline. On
2,418 smart-contract commits a hosted model ran at roughly **0.25 commits per
second**; the deterministic classifier in [`lean/`](../lean) runs at **43**.
This note records what that buys, what it does not, and what the literature
already knows — because the second half is the part that decides the design.

## What already exists

Identifying a bug-fixing (and specifically a *silent* vulnerability-fixing)
commit is a well-studied problem. Nothing below is a gap waiting to be filled;
it is a map of approaches with known costs.

**Graph neural networks over patch graphs** — the approach closest to "no LLM,
very fast":

| work | idea |
|---|---|
| [VFFinder (2309.01971)](https://arxiv.org/abs/2309.01971) | AST-level change graph of the patch, GNN over it, for silent vulnerability-fix identification |
| [Silent Vulnerability-fixing Commit Identification with GNNs (2309.08225)](https://arxiv.org/abs/2309.08225) | the same framing, attention over changed-code graphs |
| [Fixseeker (2503.20265)](https://arxiv.org/abs/2503.20265) | graph over *hunk correlations* — a fix is often several hunks that only make sense together |
| [VFDelta (2409.16606)](https://arxiv.org/abs/2409.16606) | learns the delta between pre- and post-fix embeddings rather than the patch text |
| [Repository-level graph representation (2412.08068)](https://arxiv.org/abs/2412.08068) | context beyond the patch: the rest of the repository |
| [BinGo (2312.07921)](https://arxiv.org/abs/2312.07921), [StriderSPD (2601.05772)](https://arxiv.org/abs/2601.05772) | the same problem on binaries, where no source diff exists |

**Code property graphs** — the structure those GNNs consume: [SemVul
(2609.06035)](https://arxiv.org/abs/2609.06035), [TaintRadar
(2607.16456)](https://arxiv.org/abs/2607.16456), and the CPG-guided LLM hybrids
([LLMxCPG 2507.16585](https://arxiv.org/abs/2507.16585), [2603.24837](https://arxiv.org/abs/2603.24837)).

**SZZ** solves the *inverse* problem — given a fixing commit, find the
inducing one ([developer-informed oracle](https://arxiv.org/abs/2102.03300),
[on the Linux kernel](https://arxiv.org/abs/2308.05060),
[WIA-SZZ](https://arxiv.org/abs/2411.12740)). It presupposes the label this
pipeline is trying to produce, so it is downstream of us, not a substitute.

The thing to take from this: **every method that works operates on a parsed
structure — an AST or a CPG — and learns.** None of them are keyword or token
heuristics. That is not an accident, as the measurements below show.

## What was built here

[`lean/OssBugfix`](../lean/OssBugfix) is a deterministic classifier in Lean 4:
a total parser from unified-diff text to a `Diff`, a tokenizer, ten structural
edit detectors, and a three-valued verdict. It compiles to a native binary with
**no dependencies beyond the bare toolchain** — no mathlib, no package index —
and is wired in as `--engine lean`.

The detectors answer questions about what an author *did*, never about what
they *said*: a `require` inserted into existing code, one comparison operator
swapped in one line, `unchecked` removed, a reentrancy guard added. Keying any
of them on commit wording would reintroduce exactly the blind spot this corpus
exists to remove.

### Why Lean, and not more Python

The README states five honesty rules. Everywhere else they are kept by review
and by tests over the inputs somebody thought of. In
[`Props.lean`](../lean/OssBugfix/Props.lean) three of them are theorems,
checked by the compiler over every input there is:

| theorem | what it rules out |
|---|---|
| `security_needs_witness` | calling something a security fix with no structural shape to point at |
| `bugfix_needs_witness` | the same for "this is a bug fix" |
| `refuted_is_silent` | showing evidence for a claim that was not made |
| `trivial_is_refuted` | an edit that changed no token being anything but "no behaviour change" |
| `signatures_monotone` | a finding disappearing when the classifier is handed *more* diff — i.e. a split commit laundering a fix |

All six depend only on `propext`, `Classical.choice` and `Quot.sound`; there is
no `sorry` in the package. Totality is checked rather than hoped for, which
matters more than it sounds: a classifier that can diverge on some input
silently drops commits, and a dropped commit is indistinguishable from one that
was examined and rejected.

## What it measures

2,418 commits from `solmate`, `permit2`, `morpho-blue` and `safe-contracts`,
diffs served from hydrated local mirrors.

| | |
|---|---:|
| throughput | **43 commits/sec** (2,418 in 56 s, single process) |
| hosted model, same corpus | ~0.25 commits/sec |
| provably refuted (no token changed) | **346 (14%)** |
| abstained (`null`, handed on) | **1,876 (78%)** |
| asserted a verdict | 196 (8%) |

The 14% is the honest win and it needs no model ever: those commits changed
comments, whitespace or formatting, and `trivial_is_refuted` says so as a
theorem rather than a heuristic.

### Precision: three rounds, and the answer is still no

Against the model's labels on the overlapping commits:

| round | what changed | positives (of 2,418) | false positives on the reference |
|---|---|---:|---:|
| 1 | token-count heuristics | 949 | 113 |
| 2 | one-token paired edits only | 443 | 34 |
| 3 | + surgical-insertion and test-path conditions | 196 | 15 |

Each tightening came from a named failure, not from tuning:

- **round 2** — `bounds_adjusted` was firing on `pragma solidity ^0.8.0 →
  ^0.8.13`, because *any* numeric literal changed. Replaced by: a removed line
  and an added line identical except at one token, where that token is numeric
  *and* the line performs a comparison or an index.
- **round 3** — `guard_added` was firing on `packed allowance and signature`
  and `add nonce invalidation`, which add features that contain guards. A guard
  that arrives inside a large block of new code is part of that new code.
  Likewise `ordering_changed` fired on reordered function parameters, so it now
  requires an external call to be present at all.

**The evaluation is nonetheless inconclusive, and in the honest direction.**
The reference set at the time of writing holds 1 positive in 384 labelled
commits. Precision and recall for a positive-detector cannot be estimated from
one positive, so the right statement is: *the deterministic core over-fires by
roughly an order of magnitude relative to the model's base rate, and the
measurement needed to say by how much is still running.*

## The conclusion, and why it matches the literature

Token-level heuristics cannot tell "a guard added to existing code" from "new
code that happens to contain a guard". Making that distinction needs scope —
which function, which block, does this check dominate that use — and scope is
exactly what an AST or a code property graph provides and a token stream does
not. That is why VFFinder, Fixseeker and GraphSPD all build graphs, and it is
why round 3 is where hand-written token rules stop improving.

So the Lean core is **a filter, not a replacement**:

```
2,418 commits
   ├─ 346 (14%)  refuted with a proof     → never reach a model
   ├─  196 (8%)  carry a structural shape → ranked first for the model
   └─ 1,876 (78%) abstained               → the model's actual work
```

It removes a seventh of the corpus for free, orders the rest, and attaches a
machine-checked reason to every answer it does give.

## Where a GNN fits

The fast, model-free classifier the literature describes is reachable, but the
missing ingredient is **labels, not architecture** — and this pipeline is
currently manufacturing them. The sequence that works:

1. the LLM pass labels a domain (in progress);
2. the Lean core emits the graph deterministically — it already parses the
   diff, tokenizes it and extracts typed edit shapes, which is most of a
   patch-level feature extractor;
3. a GNN trains on (graph, label) and replaces the model at inference time;
4. the Lean theorems still bound the *reported* verdict — a learned score may
   rank, but "security fix" still requires a witness.

Step 2 wants the Lean side to grow from token edits to a real AST diff. That is
the next piece of work, and it is the piece the literature says actually pays.

## Should the Python be rewritten in Lean?

No, and the reason is not taste.

| stage | dominated by | Lean would |
|---|---|---|
| mirror | `git clone` over the network | change nothing |
| walk | `git log` output, streamed | change nothing; would lose pandas/parquet |
| signals | a pandas pass over a frame | be slower to write, no faster to run |
| diffs | git object reads | change nothing |
| classify | **the decision rule** | **already done — this is `lean/`** |
| sink | CSV merge, schema migration | lose the ecosystem that makes migration cheap |

Four of six stages are I/O-bound on `git`, and the two that are not depend on
pandas, pyarrow and a parquet reader that Lean does not have. A rewrite would
trade a working pipeline for a worse one, in exchange for proofs about code
whose correctness is not where the risk lives.

The risk lives in the *decision rule* — in whether a verdict is entitled to be
asserted. That is the part now in Lean, and it is the part where a theorem is
worth more than a test.
