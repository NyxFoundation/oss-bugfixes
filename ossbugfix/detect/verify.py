"""verify.py — the honesty rules, discharged over the whole input space.

The classifier's decision has a finite domain, but no longer a small one:
three booleans read off the diff, plus a subset of 27 signatures — 8 x 2^27,
which is 1.07 billion and not enumerable. Enumeration is not needed, because
the decision reads the signature set through exactly two predicates:

    any(s.bug_evidence for s in found)        decides is_bugfix
    any(s.security_relevant for s in found)   decides is_security_fix

so every subset with the same answers to those two is the same input as far
as `decide` is concerned. That collapses 2^27 subsets into 4 equivalence
classes, and 8 x 4 = 32 inputs IS enumerable — completely, not as a sample.

The reduction is not assumed. `class_constancy()` checks it: within each
class, hundreds of randomly drawn subsets must all produce the same verdict
booleans. If a detector were ever wired into the decision some other way,
that check fails before the enumeration claims anything.

Two independent methods, both complete:

  Z3    the rules are encoded as SMT constraints over 27 symbolic booleans and
        their negations checked for satisfiability. `unsat` is a proof over
        every subset at once — a solver does not enumerate, which is exactly
        why it keeps working as the taxonomy grows.
  exec  the real `decide()` is run over every equivalence class, cross-checked
        against the model, and the classes themselves are validated by
        sampling.

The second is what makes the first worth anything. A solver proves properties
of the model you gave it; the gap between that model and the code that ships
is where the bug lives. Here both halves are exhaustive, so the gap is closed
rather than argued about.

Run it:

    uv run python -m ossbugfix.detect.verify

The properties mirror the Lean theorems in `lean/OssBugfix/Props.lean`. What
is NOT covered here, and is covered there: anything quantified over diff TEXT
— parser totality, and monotonicity under concatenation. Those range over an
infinite space that a solver cannot discharge through a hand-written parser,
so in Python they get property-based tests. `docs/verification.md` is explicit
about which guarantee each rule has.
"""
from __future__ import annotations

import itertools
import random
import sys
from dataclasses import dataclass
from types import ModuleType
from typing import Any, Callable, Sequence

from .signature import Signature
from .verdict import Ground, Verdict, decide

__all__ = ["Property", "PROPERTIES", "prove_all", "cross_check",
           "class_constancy", "main"]

SIGNATURES: tuple[Signature, ...] = tuple(Signature)
#: touched / trivial / docs_only
FLAGS: tuple[tuple[bool, bool, bool], ...] = tuple(
    itertools.product((False, True), repeat=3))  # type: ignore[arg-type]


@dataclass(frozen=True)
class Property:
    name: str
    why: str
    #: Given the decision inputs and the verdict, does the rule hold here?
    holds: Callable[[bool, bool, bool, tuple[Signature, ...], Verdict], bool]


def _p_security_witness(t: bool, tr: bool, d: bool,
                        sigs: tuple[Signature, ...], v: Verdict) -> bool:
    return v.is_security_fix is not True or any(s.security_relevant for s in v.sigs)


def _p_bugfix_witness(t: bool, tr: bool, d: bool,
                      sigs: tuple[Signature, ...], v: Verdict) -> bool:
    return v.is_bugfix is not True or any(s.bug_evidence for s in v.sigs)


def _p_refuted_silent(t: bool, tr: bool, d: bool,
                      sigs: tuple[Signature, ...], v: Verdict) -> bool:
    return v.is_bugfix is not False or not v.sigs


def _p_security_implies_bugfix(t: bool, tr: bool, d: bool,
                               sigs: tuple[Signature, ...], v: Verdict) -> bool:
    return v.is_security_fix is not True or v.is_bugfix is True


def _p_trivial_refuted(t: bool, tr: bool, d: bool,
                       sigs: tuple[Signature, ...], v: Verdict) -> bool:
    return not tr or v.is_bugfix is False


def _p_no_evidence_undecided(t: bool, tr: bool, d: bool,
                             sigs: tuple[Signature, ...], v: Verdict) -> bool:
    """A diff that changed code but matched no bug-evidence shape gets `None`,
    never a negative. The classifier read it; it did not judge it."""
    if not (t and not tr and not d):
        return True
    if any(s.bug_evidence for s in sigs):
        return True
    return v.is_bugfix is None


PROPERTIES: tuple[Property, ...] = (
    Property("security_needs_witness",
             "cannot call something a security fix with no shape to point at",
             _p_security_witness),
    Property("bugfix_needs_witness",
             "the same for 'this is a bug fix'",
             _p_bugfix_witness),
    Property("refuted_is_silent",
             "a negative verdict shows no evidence for a claim not made",
             _p_refuted_silent),
    Property("security_implies_bugfix",
             "nothing had a security consequence without having been wrong",
             _p_security_implies_bugfix),
    Property("trivial_is_refuted",
             "an edit that changed no token is not a behaviour change",
             _p_trivial_refuted),
    Property("no_evidence_is_undecided",
             "no matching shape yields None, never False",
             _p_no_evidence_undecided),
)


def _representatives() -> list[tuple[Signature, ...]]:
    """One signature subset per equivalence class, plus the degenerate ones.

    The four classes are (any_bug_evidence, any_security_relevant). Not every
    combination is inhabited — a signature that is bug evidence but not
    security relevant exists, and so does the reverse — so the classes are
    discovered from the taxonomy rather than assumed.
    """
    seen: dict[tuple[bool, bool], tuple[Signature, ...]] = {}
    # Singletons first, so each representative is as small as possible and a
    # failure report names one signature rather than a set.
    for r in (1, 2):
        for subset in itertools.combinations(SIGNATURES, r):
            key = (any(s.bug_evidence for s in subset),
                   any(s.security_relevant for s in subset))
            seen.setdefault(key, subset)
    out: list[tuple[Signature, ...]] = [()]
    out.extend(seen.values())
    return out


def _all_inputs() -> list[tuple[bool, bool, bool, tuple[Signature, ...]]]:
    """Every decision input, up to equivalence: 8 flag combinations times one
    representative of each inhabited signature class."""
    return [(t, tr, d, subset)
            for subset in _representatives()
            for t, tr, d in FLAGS]


def class_constancy(trials: int = 400, seed: int = 0) -> list[str]:
    """Does the verdict really depend on the signature set only through those
    two predicates?

    Everything above rests on this. Draw random subsets, bucket them by the
    two predicates, and insist every member of a bucket produces the same
    verdict booleans. A detector wired into the decision some other way breaks
    this before the enumeration can claim completeness it does not have.
    """
    rng = random.Random(seed)
    buckets: dict[tuple[bool, bool, bool, bool, bool],
                  tuple[bool | None, bool | None, Ground]] = {}
    failures: list[str] = []
    for _ in range(trials):
        k = rng.randrange(0, len(SIGNATURES) + 1)
        subset = tuple(rng.sample(SIGNATURES, k))
        for touched, trivial, docs in FLAGS:
            key = (touched, trivial, docs,
                   any(s.bug_evidence for s in subset),
                   any(s.security_relevant for s in subset))
            v = decide(touched=touched, trivial=trivial, docs_only=docs,
                       found=list(subset))
            got = (v.is_bugfix, v.is_security_fix, v.ground)
            if key in buckets and buckets[key] != got:
                failures.append(
                    f"class {key} is not constant: {buckets[key]} vs {got} "
                    f"at sigs={[s.value for s in subset]}")
            buckets.setdefault(key, got)
    return failures


def exhaustive_check() -> list[str]:
    """Run the REAL `decide()` over the whole input space.

    Complete, not a sample: the domain is finite, so this is a proof too — of
    the code rather than of a model of it.
    """
    failures: list[str] = []
    for touched, trivial, docs, subset in _all_inputs():
        try:
            v = decide(touched=touched, trivial=trivial, docs_only=docs,
                       found=list(subset))
        except Exception as exc:              # noqa: BLE001 - that IS a failure
            failures.append(f"decide raised on {subset} flags=({touched},{trivial},{docs}): {exc}")
            continue
        for prop in PROPERTIES:
            if not prop.holds(touched, trivial, docs, subset, v):
                failures.append(
                    f"{prop.name} violated: flags=(touched={touched}, trivial={trivial}, "
                    f"docs={docs}) sigs={[s.value for s in subset]} -> "
                    f"bugfix={v.is_bugfix} security={v.is_security_fix} "
                    f"sigs={[s.value for s in v.sigs]}")
    return failures


# --- the solver half --------------------------------------------------------

def _z3() -> ModuleType | None:
    """z3-solver ships no type stubs, so everything it touches is `Any`. That
    is contained to this module: the verifier is allowed to be dynamically
    typed, the classifier it verifies is not."""
    try:
        import z3  # type: ignore[import-untyped]
    except ImportError:                       # pragma: no cover
        return None
    module: ModuleType = z3
    return module


@dataclass(frozen=True)
class SmtVerdict:
    """The decision as SMT formulas. Three-valued is two booleans, so `None`
    is "neither holds" — the same shape the Python returns."""

    bug_true: Any
    bug_false: Any
    sec_true: Any
    sec_false: Any
    sigs_empty: Any
    bug_ev: Any
    sec_ev: Any


def smt_model(z3: Any, present: Sequence[Any], touched: Any, trivial: Any,
              docs: Any) -> SmtVerdict:
    """The decision as SMT, in the same order as `decide`."""
    bug_ev = z3.Or([p for p, s in zip(present, SIGNATURES) if s.bug_evidence])
    sec_ev = z3.Or([p for p, s in zip(present, SIGNATURES) if s.security_relevant])
    refuted = z3.Or(z3.Not(touched), trivial, docs)
    asserted = z3.And(z3.Not(refuted), bug_ev)
    return SmtVerdict(
        bug_true=asserted,
        bug_false=refuted,
        sec_true=z3.And(asserted, sec_ev),
        # False, not None: an asserted verdict with no security-relevant shape
        # says "not a security fix" rather than declining. The cross-check
        # found this by disagreeing with the code, which is what it is for.
        sec_false=z3.Or(refuted, z3.And(asserted, z3.Not(sec_ev))),
        sigs_empty=refuted,
        bug_ev=bug_ev,
        sec_ev=sec_ev,
    )


def prove_with_z3() -> tuple[list[str], list[str]]:
    """Discharge each property as an SMT query. Returns (proved, failures)."""
    z3 = _z3()
    if z3 is None:
        return [], ["z3-solver is not installed"]

    present = [z3.Bool(f"sig_{s.value}") for s in SIGNATURES]
    touched, trivial, docs = z3.Bools("touched trivial docs_only")
    m = smt_model(z3, present, touched, trivial, docs)

    queries = {
        "security_needs_witness": z3.And(m.sec_true, z3.Not(m.sec_ev)),
        "bugfix_needs_witness": z3.And(m.bug_true, z3.Not(m.bug_ev)),
        "refuted_is_silent": z3.And(m.bug_false, z3.Not(m.sigs_empty)),
        "security_implies_bugfix": z3.And(m.sec_true, z3.Not(m.bug_true)),
        "trivial_is_refuted": z3.And(trivial, z3.Not(m.bug_false)),
        "three_valued_is_consistent": z3.And(m.sec_true, m.sec_false),
        "no_evidence_is_undecided": z3.And(
            touched, z3.Not(trivial), z3.Not(docs), z3.Not(m.bug_ev),
            z3.Or(m.bug_true, m.bug_false)),
        # Monotonicity: adding a signature never withdraws a positive verdict.
        # Stated over two assignments that agree except that one has a strict
        # superset of signatures.
        "monotone_in_signatures": _monotone_query(z3, present, touched, trivial, docs),
    }

    proved, failures = [], []
    for name, negation in queries.items():
        solver = z3.Solver()
        solver.add(negation)
        result = solver.check()
        if result == z3.unsat:
            proved.append(name)
        elif result == z3.sat:
            failures.append(f"{name}: COUNTEREXAMPLE {solver.model()}")
        else:
            failures.append(f"{name}: solver returned {result}")
    return proved, failures


def _monotone_query(z3: Any, present: Sequence[Any], touched: Any,
                    trivial: Any, docs: Any) -> Any:
    """Negation of: a superset of signatures never loses a positive verdict."""
    other = [z3.Bool(f"sup_{s.value}") for s in SIGNATURES]
    superset = z3.And([z3.Implies(p, q) for p, q in zip(present, other)])
    a = smt_model(z3, present, touched, trivial, docs)
    b = smt_model(z3, other, touched, trivial, docs)
    return z3.And(superset, a.bug_true, z3.Not(b.bug_true))


def cross_check() -> list[str]:
    """Every point of the SMT model must agree with the real `decide()`.

    This is the step that makes the proof mean something: without it, Z3 has
    verified a story about the code rather than the code.
    """
    z3 = _z3()
    if z3 is None:
        return ["z3-solver is not installed"]
    mismatches: list[str] = []
    for touched, trivial, docs, subset in _all_inputs():
        v = decide(touched=touched, trivial=trivial, docs_only=docs, found=list(subset))
        present = [z3.BoolVal(s in subset) for s in SIGNATURES]
        m = smt_model(z3, present, z3.BoolVal(touched), z3.BoolVal(trivial),
                      z3.BoolVal(docs))
        model_bug = (True if z3.is_true(z3.simplify(m.bug_true))
                     else False if z3.is_true(z3.simplify(m.bug_false)) else None)
        model_sec = (True if z3.is_true(z3.simplify(m.sec_true))
                     else False if z3.is_true(z3.simplify(m.sec_false)) else None)
        if model_bug != v.is_bugfix or model_sec != v.is_security_fix:
            mismatches.append(
                f"model/code disagree at flags=({touched},{trivial},{docs}) "
                f"sigs={[s.value for s in subset]}: "
                f"model=({model_bug},{model_sec}) code=({v.is_bugfix},{v.is_security_fix})")
            if len(mismatches) >= 5:
                break
    return mismatches


def prove_all(verbose: bool = True) -> int:
    inputs = _all_inputs()
    if verbose:
        total = len(FLAGS) * 2 ** len(SIGNATURES)
        print(f"decision domain: {len(FLAGS)} flag combinations x 2^{len(SIGNATURES)} "
              f"signature subsets = {total:,} inputs,")
        print(f"                 collapsing to {len(inputs)} by equivalence "
              f"(the decision reads the set through two predicates)")

    constancy = class_constancy()
    if verbose:
        print(f"\n[class] the equivalence itself, over 400 random subsets x "
              f"{len(FLAGS)} flag combinations: "
              + ("holds" if not constancy else f"{len(constancy)} FAILURES"))
    for f in constancy[:5]:
        print("  -", f, file=sys.stderr)

    failures = exhaustive_check()
    if verbose:
        print(f"[exec] ran the real decide() over all {len(inputs)} classes: "
              + ("ok" if not failures else f"{len(failures)} FAILURES"))
    for f in failures[:5]:
        print("  -", f, file=sys.stderr)

    proved, z3_failures = prove_with_z3()
    if verbose:
        print(f"[z3]   {len(proved)} properties proved unsat-of-negation:")
        for name in proved:
            print(f"         ✓ {name}")
    for f in z3_failures:
        print("  -", f, file=sys.stderr)

    mismatches = cross_check()
    if verbose:
        print(f"[link] SMT model vs real decide(), all {len(inputs)} classes: "
              + ("agree" if not mismatches else f"{len(mismatches)} MISMATCHES"))
    for m in mismatches[:5]:
        print("  -", m, file=sys.stderr)

    bad = len(failures) + len(z3_failures) + len(mismatches) + len(constancy)
    if verbose:
        print("\nVERIFIED" if not bad else f"\nFAILED ({bad} problems)")
    return 1 if bad else 0


def main() -> int:
    return prove_all()


if __name__ == "__main__":
    raise SystemExit(main())
