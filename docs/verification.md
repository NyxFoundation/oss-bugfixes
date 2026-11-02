# What is proved, what is tested, and where the line is

The deterministic classifier makes claims about commits. This page says
exactly how much each claim is worth, because "verified" means three different
things in this repository and conflating them would be the same kind of
dishonesty the classifier itself is built to avoid.

## The line

The classifier has two halves with completely different mathematics.

| | domain | size | guarantee |
|---|---|---|---|
| **verdict algebra** — signatures → verdict | 3 booleans × subsets of 10 signatures | **8,192 inputs** | **proved**, two independent ways |
| **parser & tokenizer** — diff text → signatures | arbitrary strings | infinite | **tested**, property-based |

The honesty rules all live in the first half. "A security verdict names its
witness" is a statement about the verdict function, not about the parser. So
the half that carries the rules is the half that is finite, and finite means
provable.

## The finite half: proved three ways

```bash
uv run python -m ossbugfix.detect.verify
```

```
decision domain: 8,192 inputs (8 flag combinations x 2^10 signature subsets)

[exec] ran the real decide() over all 8,192 inputs: ok
[z3]   8 properties proved unsat-of-negation:
         ✓ security_needs_witness      ✓ three_valued_is_consistent
         ✓ bugfix_needs_witness        ✓ no_evidence_is_undecided
         ✓ refuted_is_silent           ✓ monotone_in_signatures
         ✓ security_implies_bugfix     ✓ trivial_is_refuted
[link] SMT model vs real decide(), all 8,192 points: agree

VERIFIED
```

Three mechanisms, deliberately redundant:

**1. Construction.** `Verdict.__post_init__` rejects any verdict that asserts
more than its evidence supports, so no such object can exist anywhere in the
process — not merely as a return value of `classify`. `Verdict.asserted()`
takes the witnessing signatures as an *argument*: there is no way to assert
without supplying them. This is the Python stand-in for making the state
unrepresentable.

**2. Z3.** Each rule is encoded as SMT and its negation checked. `unsat` is a
proof over the whole space, not a sample.

**3. Exhaustive execution.** The real `decide()` is run over all 8,192 inputs
and every rule checked on the result. Also complete, and about the code rather
than a model of it.

### The step that makes the proof worth anything

A solver proves properties of the model you hand it. The gap between that
model and the code that ships is where the bug lives — and that is not
theoretical here:

> The first run of `cross_check()` reported
> `model/code disagree at flags=(True,False,False) sigs=['comparison_tightened']:
> model=(True,None) code=(True,False)`.

The SMT encoding had `is_security_fix` as *undecided* for an asserted verdict
with no security-relevant shape, where the code says *False*. Z3 had already
"proved" all eight properties — of a model that did not describe the program.
The point-by-point link caught it.

So `decide()` is deliberately factored out of `classify()` and takes its four
inputs explicitly. **The thing proved is the thing that runs.**

## The infinite half: tested, and said so

The parser and tokenizer range over arbitrary strings. A solver cannot
discharge `∀ diff text, …` through a hand-written parser, and Python has no
termination checker, so these get [Hypothesis](https://hypothesis.works/):

| property | why it matters |
|---|---|
| `parse` is total | a classifier that raises silently drops commits, and a dropped commit is indistinguishable from one examined and rejected |
| `classify` is total | the same, one layer up |
| tokens never invent characters | a detector matching a token the line does not contain is matching noise |
| signatures are monotone under concatenation | a finding must not vanish when the classifier is handed *more* diff — what stops a split commit laundering a fix |
| `parse` is idempotent | the corpus must not depend on how many times a row was processed |

**This is assurance, not proof.** Hypothesis explores; it does not exhaust.
The claim is "no counterexample in 300 randomised attempts per property, per
run, every CI build", which is worth a great deal and is not the same as a
theorem.

## Why not Lean for all of it

[`lean/`](../lean) holds the original of this core, where five of these rules
are machine-checked theorems over the *unbounded* input space — including
parser totality, which Lean's termination checker gives for free and Python
cannot give at all.

That is strictly stronger on the infinite half. It is also a second toolchain,
a second language and a second copy of every detector, for a component whose
practical value turned out to be a 14% pre-filter rather than a replacement
classifier. The typed-Python core gets:

- the same rules, **proved** on the half where the rules live;
- `mypy --strict` across the package;
- invalid verdicts unconstructable at runtime, everywhere, not just in one
  function;
- one language, one test run, no cross-compilation in CI.

and gives up proof of parser totality, which is the property a fuzzer is
genuinely good at.

Both are in the repository. `--engine structural` is the Python one and is the
default recommendation; `--engine lean` is the proved-stronger one for anyone
who wants the unbounded theorems. They are checked against each other on the
whole corpus — see `docs/deterministic-classification.md` — and a divergence
between them is a bug in one of them.

## What none of this proves

That the classifier is *right*. Every rule above is about internal
consistency: that a claim has a witness, that a negative carries no evidence,
that more input never removes a finding. None of them says a `guard_added`
signature means a bug was fixed.

That is an empirical question, it is answered by comparing against labelled
data, and the current answer is in
[`deterministic-classification.md`](deterministic-classification.md): the
structural detectors over-fire by roughly an order of magnitude against a
model's base rate. **A verified classifier can be verifiably wrong**, and
keeping those two statements apart is the entire point of this page.
