/-
  Classify.lean — signatures to a verdict, with the same honesty contract the
  rest of the pipeline keeps.

  The verdict is THREE-VALUED, and that is the whole design. `none` means the
  classifier has no opinion, which is a different claim from `some false`, and
  conflating them is the exact mistake this project has already paid for once
  (a skipped backport check recorded as "0 backported"). A deterministic
  classifier is entitled to very few negatives — it can prove that an edit
  which changed no tokens changed no behaviour, and little else — so almost
  everything it cannot name becomes `none` and goes on to a model.

  That makes this a FILTER, not a replacement: it answers cheaply wherever a
  structural fact settles the question, and hands the rest on.
-/
import OssBugfix.Signature

namespace OssBugfix

/-- Why the classifier said what it said. Carried into the output so a row is
    auditable without re-running anything. -/
inductive Ground where
  | emptyDiff
  | noTokenChange
  | docsOnly
  | structural
  | noSignature
  deriving DecidableEq, Repr, Inhabited

def Ground.name : Ground → String
  | emptyDiff     => "empty_diff"
  | noTokenChange => "no_token_change"
  | docsOnly      => "docs_only"
  | structural    => "structural_signature"
  | noSignature   => "no_structural_signature"

structure Verdict where
  isBugfix      : Option Bool
  isSecurityFix : Option Bool
  sigs          : List Signature
  ground        : Ground
  deriving Repr, Inhabited

namespace Verdict

/-- Nothing asserted either way. -/
def undecided (g : Ground) : Verdict := ⟨none, none, [], g⟩

/-- Provably not a behaviour change. -/
def refuted (g : Ground) : Verdict := ⟨some false, some false, [], g⟩

/-- The distinct shapes, order preserved.

    `sigs` holds one entry per firing hunk, because that is the form the
    theorems in Props.lean are stated over. A report wants the set: a hunk
    that adds six `require`s is one shape, and printing it six times would
    make the output a measure of file size. -/
def distinct (v : Verdict) : List Signature :=
  v.sigs.foldl (fun acc s => if acc.contains s then acc else acc ++ [s]) []

/-- Not a probability and not calibrated against anything. It orders rows for
    review: more independent shapes, more worth a look. -/
def weight (v : Verdict) : Nat := v.distinct.length

end Verdict

/-- The classifier.

    Order matters and is from most certain to least:
      1. nothing changed            → refuted
      2. no token changed           → refuted (comments, whitespace, format)
      3. only non-code files        → refuted
      4. at least one bug-evidence shape → asserted
      5. otherwise                  → undecided, explicitly -/
def classify (d : Diff) : Verdict :=
  if !d.touched then Verdict.refuted Ground.emptyDiff
  else if semanticallyTrivial d then Verdict.refuted Ground.noTokenChange
  else if onlyDocs d then Verdict.refuted Ground.docsOnly
  -- `signatures d` is repeated rather than bound to a `let`: a let-binding
  -- hides the branch from `split`, and the proofs in Props.lean are worth
  -- more than the one less repetition.
  else if (signatures d).any Signature.bugEvidence then
    ⟨some true, some ((signatures d).any Signature.securityRelevant),
     signatures d, Ground.structural⟩
  else
    -- Shapes may have fired that are real but are not evidence of a defect
    -- (a tightened visibility). They are reported; the verdict is still
    -- withheld, because "something changed" is not "something was wrong".
    ⟨none, none, signatures d, Ground.noSignature⟩

end OssBugfix
