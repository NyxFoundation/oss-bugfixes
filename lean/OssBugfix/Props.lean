/-
  Props.lean — the honesty rules, as theorems instead of prose.

  The README of this repository states five rules the code is supposed to
  keep. In Python they are enforced by review and by tests over the inputs
  somebody thought of. Here three of them are enforced by the compiler, over
  every input there is. That — not speed — is the reason this component is
  written in Lean.

  What is proved:

    security_needs_witness   the classifier cannot call something a security
                             fix without naming a structural shape that makes
                             it one
    bugfix_needs_witness     likewise for "this is a bug fix"
    refuted_is_silent        a negative verdict never carries signatures, so a
                             reader cannot be shown evidence for a claim that
                             was not made
    trivial_is_refuted       an edit that changed no token is reported as not
                             a behaviour change — the single negative this
                             classifier is entitled to assert
    signatures_monotone      adding more diff never removes a finding, so
                             splitting a commit cannot hide one
-/
import OssBugfix.Classify

namespace OssBugfix

/-- A security verdict always has a witness among the reported signatures. -/
theorem security_needs_witness (d : Diff) :
    (classify d).isSecurityFix = some true →
    (classify d).sigs.any Signature.securityRelevant = true := by
  unfold classify
  repeat' split
  all_goals simp_all [Verdict.refuted]

/-- A bug-fix verdict always has a witness too. -/
theorem bugfix_needs_witness (d : Diff) :
    (classify d).isBugfix = some true →
    (classify d).sigs.any Signature.bugEvidence = true := by
  unfold classify
  repeat' split
  all_goals simp_all [Verdict.refuted]

/-- A negative verdict carries no signatures: the reader is never shown
    evidence for a claim the classifier did not make. -/
theorem refuted_is_silent (d : Diff) :
    (classify d).isBugfix = some false → (classify d).sigs = [] := by
  unfold classify
  repeat' split
  all_goals simp_all [Verdict.refuted]

/-- An edit that changed no token changed no behaviour. This is the only
    negative the classifier asserts on its own authority. -/
theorem trivial_is_refuted (d : Diff) :
    semanticallyTrivial d = true → (classify d).isBugfix = some false := by
  intro h
  unfold classify
  repeat' split
  all_goals simp_all [Verdict.refuted]

/-- Concatenating diffs concatenates their signatures. -/
theorem signatures_append (a b : Diff) :
    signatures (a ++ b) = signatures a ++ signatures b := by
  -- `simp` loops here: unfolding `Diff.append` re-exposes `++` on lists, which
  -- it then unfolds again. Say the reduction once, by hand.
  show (a.hunks ++ b.hunks).flatMap detect
     = a.hunks.flatMap detect ++ b.hunks.flatMap detect
  exact List.flatMap_append

/-- So a finding can never be hidden by handing the classifier MORE diff —
    which is what stops a split commit from laundering a fix. -/
theorem signatures_monotone (a b : Diff) (s : Signature) :
    s ∈ signatures a → s ∈ signatures (a ++ b) := by
  intro h
  rw [signatures_append]
  exact List.mem_append_left _ h

theorem signatures_monotone_right (a b : Diff) (s : Signature) :
    s ∈ signatures b → s ∈ signatures (a ++ b) := by
  intro h
  rw [signatures_append]
  exact List.mem_append_right _ h

/-- Undecided is reachable and is a real answer: a diff that changed tokens
    but matched no shape gets `none`, not a negative. -/
theorem unmatched_is_undecided (d : Diff) :
    d.touched = true → semanticallyTrivial d = false → onlyDocs d = false →
    (signatures d).any Signature.bugEvidence = false →
    (classify d).isBugfix = none := by
  intro ht htriv hdoc hsig
  unfold classify
  simp [ht, htriv, hdoc, hsig]

end OssBugfix
