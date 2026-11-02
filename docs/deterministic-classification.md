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

Two implementations of the same classifier — a total parser from unified-diff
text, a tokenizer, ten structural edit detectors, and a three-valued verdict:

| engine | language | guarantee |
|---|---|---|
| `--engine structural` | typed Python, `mypy --strict` | verdict rules **proved** over their finite space (Z3 + exhaustive execution + a model/code link); parser properties tested with Hypothesis |
| `--engine lean` | Lean 4, no mathlib | the same rules plus parser totality as theorems over the **unbounded** space |

[`docs/verification.md`](verification.md) draws the line between those two
precisely.

The detectors answer questions about what an author *did*, never about what
they *said*: a `require` inserted into existing code, one comparison operator
swapped in one line, `unchecked` removed, a reentrancy guard added. Keying any
of them on commit wording would reintroduce exactly the blind spot this corpus
exists to remove.

### Lean first, then typed Python — and the measurement that settled it

Lean came first, because the README states five honesty rules that everywhere
else are kept by review and by tests over the inputs somebody thought of. In
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

**Then the same core was written in typed Python, and the comparison was not
close.** Both engines over the same 5,607 commits (97 MB of diffs), same host:

| | throughput | agreement |
|---|---:|---:|
| `structural` (typed Python, in-process) | **107 commits/sec** | — |
| `lean` (compiled binary, piped) | 56 commits/sec | **5,607 / 5,607 = 100.000%** |

The compiled language is the slower one, by roughly two. Not because Lean is
slow: the Python runs in-process while the Lean path pays for a 97 MB payload
through a pipe, and the Lean parser accumulates hunk lines with `++`, which is
quadratic where Python's list append is not. Both are fixable; neither was
worth fixing once the verdicts agreed exactly.

What that leaves is a maintenance question rather than a performance one. Two
copies of ten detectors in two languages will drift —
`tests/test_engine_parity.py` exists to notice — and the second copy buys one
property the first cannot have: proof of parser totality over an infinite
input space. For a component whose measured value is a 14% pre-filter, the
typed-Python core is the better trade, and `--engine structural` is the
recommendation. The Lean package stays as the stronger reference.

## What it measures

2,418 commits from `solmate`, `permit2`, `morpho-blue` and `safe-contracts`,
diffs served from hydrated local mirrors.

| | |
|---|---:|
| throughput | **107 commits/sec** (typed Python); 43–56 via the Lean binary |
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

### The evaluation, once there were positives to evaluate against

The first attempt at this was worthless: the reference set held 1 positive in
384 labelled commits, and precision cannot be estimated from one positive. The
cause turned out to be the model, not the corpus — `deepseek-v4-pro` returned
an empty `content` on about a third of calls and took a 94.6 s median.
Switching to `gpt-oss:120b` (11.8 s median, 4/4 decided in a spot check) gave a
usable reference.

Against **459 model-labelled commits** (64 bug fixes, 39 security fixes):

| | decided | abstained | TP | TN | FP | FN | precision | recall |
|---|---:|---:|---:|---:|---:|---:|---:|---:|
| `is_bugfix` | 104 (23%) | 355 | 11 | 66 | 26 | 1 | **0.30** | **0.92** |
| `is_security_fix` | 104 (23%) | 355 | 9 | 68 | 27 | 0 | **0.25** | **1.00** |

That is the profile of a filter, stated plainly: it nearly never misses a real
fix, and three or four of every five things it points at are not one.

### Its refutations were right every time — including where the model was not

Of the 67 commits the engine refuted, the model agreed on 66. The single
disagreement is the most instructive row in this corpus.

`solmate` commit **"📝 Comment fixes"**: all 24 changed lines are comments,
among them

```diff
-            // We use 100 because the calldata length is 4 + 32 * 2.
+            // We use 68 because the calldata length is 4 + 32 * 2.
             let callStatus := call(gas(), token, 0, callData, 68, 0, 0)
```

The code beside it always read `68`; the *comment* was wrong and got fixed.
The model reported: *"corrects the calldata length constants (changing 100/64
to the correct 68) used in ERC20 calls, fixing a functional bug"*. It had read
a comment about a constant as the constant.

The structural engine cannot make that mistake — it strips comments before it
looks at anything. So the refutations are **67 of 67 correct**, and the
reference, not the classifier, was wrong.

This is now a pipeline rule rather than an anecdote. A ground of
`no_token_change`, `empty_diff` or `docs_only` is a *fact about the diff*, not
a competing opinion, so it overrules a model's positive verdict and records
the override in the rationale (`--no-veto` turns it off). A model that reads
the diff as text can be fooled by what the text says about itself; a function
that tokenises first cannot.

## The conclusion, and why it matches the literature

Token-level heuristics cannot tell "a guard added to existing code" from "new
code that happens to contain a guard". Making that distinction needs scope —
which function, which block, does this check dominate that use — and scope is
exactly what an AST or a code property graph provides and a token stream does
not. That is why VFFinder, Fixseeker and GraphSPD all build graphs, and it is
why round 3 is where hand-written token rules stop improving.

So the deterministic core is **a filter, not a replacement**:

```
5,607 commits
   ├─   471 (8%)  refuted — and right 67/67 where a model disagreed
   ├─   566 (10%) carry a structural shape → ranked first, precision ~0.3
   └─ 4,570 (82%) abstained                → the model's actual work
```

It removes commits a model never needs to see, orders the rest, attaches a
checkable reason to every answer it gives, and — on the evidence above — is
allowed to overrule the model where its answer is a fact rather than a
judgement.

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

No, and the measurement above is only half the reason.

| stage | dominated by | Lean would |
|---|---|---|
| mirror | `git clone` over the network | change nothing |
| walk | `git log` output, streamed | change nothing; would lose pandas/parquet |
| signals | a pandas pass over a frame | be slower to write, no faster to run |
| diffs | git object reads | change nothing |
| classify | **the decision rule** | **the part worth verifying — done twice, see above** |
| sink | CSV merge, schema migration | lose the ecosystem that makes migration cheap |

Four of six stages are I/O-bound on `git`, and the two that are not depend on
pandas, pyarrow and a parquet reader that Lean does not have. A rewrite would
trade a working pipeline for a worse one, in exchange for proofs about code
whose correctness is not where the risk lives.

The risk lives in the *decision rule* — in whether a verdict is entitled to be
asserted. That part is verified twice over, and the decisive observation is
that **it is finite**: three booleans and a subset of ten signatures, 8,192
inputs in total. Finite means a solver can exhaust it, which is why typed
Python plus Z3 gives up nothing where the honesty rules actually live.
