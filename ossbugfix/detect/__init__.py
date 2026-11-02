"""detect — the deterministic classifier, in typed Python.

A port of the Lean core (`lean/`), keeping its contract and trading its
unbounded proofs for two cheaper guarantees that cover the part that matters:

  * the honesty rules live in the VERDICT ALGEBRA, and that algebra is finite —
    ten signatures, two grounds flags, 2^10 subsets. `verify.py` discharges
    them with Z3 over the whole space, which is a proof, not a sample.
  * the parser and tokenizer work over strings, which is infinite and beyond a
    solver here. Those get `mypy --strict` and property-based tests instead:
    assurance rather than proof, and the README says so.

What is NOT weaker than the Lean version: the positive verdicts are
unrepresentable without a witness. `Verdict.asserted` takes the witnessing
signatures as an argument, so "a security verdict with no evidence" cannot be
constructed at all, let alone returned.
"""

from .diff import Diff, Hunk, parse
from .signature import Signature, detect, signatures, semantically_trivial, only_docs
from .tokens import tokens, tokens_of
from .verdict import Ground, Verdict, classify

__all__ = [
    "Diff", "Hunk", "parse",
    "Signature", "detect", "signatures", "semantically_trivial", "only_docs",
    "tokens", "tokens_of",
    "Ground", "Verdict", "classify",
]
