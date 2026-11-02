# `lean/` — the deterministic classifier

A classifier for bug-fix commits written in Lean 4: a **total** parser from
unified-diff text to a structured `Diff`, a tokenizer, ten structural edit
detectors, and a three-valued verdict.

```bash
lake build
./.lake/build/bin/ossbugfix-classify --selftest
printf '\x1eabc\x1f%s' "$(git show HEAD)" | ./.lake/build/bin/ossbugfix-classify
```

From the pipeline:

```bash
oss-bugfixes classify --domain smartcontract --engine lean
```

No mathlib, no package index — it builds from a bare toolchain in seconds,
which is what makes it usable as a pipeline stage rather than a research
artifact.

## Why Lean and not more Python

The project's README states five honesty rules. Everywhere else they are kept
by review and by tests over the inputs somebody thought of. Here three of them
are theorems, checked by the compiler over every input there is:

| theorem | rules out |
|---|---|
| `security_needs_witness` | asserting a security fix with no structural shape to point at |
| `bugfix_needs_witness` | the same for "this is a bug fix" |
| `refuted_is_silent` | showing evidence for a claim that was not made |
| `trivial_is_refuted` | an edit that changed no token being read as anything but "no behaviour change" |
| `signatures_monotone` | a finding vanishing when the classifier is given *more* diff — a split commit laundering a fix |

```
#print axioms OssBugfix.security_needs_witness
-- depends on axioms: [propext, Classical.choice, Quot.sound]
```

No `sorry` in the package. Totality is checked rather than hoped for, and that
matters operationally: a classifier that can diverge on some input silently
drops commits, and a dropped commit is indistinguishable from one that was
examined and rejected.

## Layout

| file | |
|---|---|
| `OssBugfix/Diff.lean` | `Hunk`, `Diff`, and a total unified-diff parser. Tracks `newFile`, because every line of a created file is "added" and an edit signature cannot be read off that |
| `OssBugfix/Token.lean` | comment-stripping tokenizer, structural recursion only |
| `OssBugfix/Signature.lean` | the ten detectors, and the one-token paired-edit machinery they rest on |
| `OssBugfix/Classify.lean` | the three-valued verdict and its grounds |
| `OssBugfix/Props.lean` | the theorems |
| `OssBugfix/Json.lean` | NDJSON output, hand-rolled so the package needs no dependency |
| `Main.lean` | CLI, including `--selftest` |

## Protocol

stdin is a sequence of records, each introduced by `\x1e`, with the id and the
diff separated by `\x1f`. Both are C0 control characters git never emits, so a
diff needs no escaping on the way in. Output is NDJSON, one line per record, in
input order.

```json
{"id":"7372ab","is_bugfix":true,"is_security_fix":true,
 "ground":"structural_signature","signatures":["guard_added"],"weight":1}
```

`null` means *undecided*, and it is the common answer by design.

## What it is for

A **filter**, not a replacement for a model. On 2,418 smart-contract commits it
refutes 14% with a proof, abstains on 78%, and runs at 43 commits/second
against roughly 0.25 for a hosted model.

Hand-written token rules over-fire by about an order of magnitude against a
model's base rate, and three rounds of tightening did not close that gap. The
reason is structural: telling "a guard added to existing code" from "new code
that contains a guard" needs scope, and scope is what an AST or code property
graph gives you and a token stream does not.
[`../docs/deterministic-classification.md`](../docs/deterministic-classification.md)
has the measurements, the prior art, and where a GNN would go.
