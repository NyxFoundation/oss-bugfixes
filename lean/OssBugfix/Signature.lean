/-
  Signature.lean — the structural edit shapes that constitute evidence.

  A detector here answers one question about a hunk, from tokens alone, with
  no model and no prose. The design rule is that a detector may only fire on
  something an author DID to the code, never on something they SAID about it:
  the whole premise of this corpus is that the commit message is unreliable,
  and a detector keyed on wording would reintroduce exactly the blind spot the
  pipeline exists to remove.

  Each signature carries two gradings:

    bugEvidence       the edit asserts that the previous behaviour was wrong
    securityRelevant  and that being wrong had a security consequence

  They are separate because most of these shapes are bug evidence and only
  some are security evidence. Tightening a visibility modifier is a real
  change in the contract's surface; it is not by itself a defect fix.
-/
import OssBugfix.Diff
import OssBugfix.Token

namespace OssBugfix

inductive Signature where
  /-- A `require` / `assert` / `revert` / error return appeared where there
      was none. The author decided an input they previously accepted must be
      rejected. -/
  | guardAdded
  /-- A comparison operator changed strictness (`<` ↔ `<=`, `==` ↔ `!=`).
      Almost never a refactor; almost always a boundary that was wrong. -/
  | comparisonTightened
  /-- A zero-address / null check appeared. -/
  | zeroCheckAdded
  /-- A call's success flag started being checked. -/
  | returnValueChecked
  /-- An index or bound gained or lost a `± 1`, or a literal bound changed. -/
  | boundsAdjusted
  /-- `unchecked` removed, a checked-arithmetic helper introduced, or an
      integer type widened. -/
  | overflowGuarded
  /-- The same statements, reordered — the shape of a check-effects-interaction
      fix, where a state write moves ahead of an external call. -/
  | orderingChanged
  /-- `public` became `internal` / `private` / `external`. -/
  | visibilityTightened
  /-- An authorisation modifier or an `msg.sender` check appeared. -/
  | accessControlAdded
  /-- A reentrancy guard appeared. -/
  | reentrancyGuardAdded
  deriving DecidableEq, Repr, Inhabited, BEq

namespace Signature

def name : Signature → String
  | guardAdded          => "guard_added"
  | comparisonTightened => "comparison_tightened"
  | zeroCheckAdded      => "zero_check_added"
  | returnValueChecked  => "return_value_checked"
  | boundsAdjusted      => "bounds_adjusted"
  | overflowGuarded     => "overflow_guarded"
  | orderingChanged     => "ordering_changed"
  | visibilityTightened => "visibility_tightened"
  | accessControlAdded  => "access_control_added"
  | reentrancyGuardAdded => "reentrancy_guard_added"

/-- Does this shape assert that the previous behaviour was wrong? -/
def bugEvidence : Signature → Bool
  | guardAdded | comparisonTightened | zeroCheckAdded | returnValueChecked
  | boundsAdjusted | overflowGuarded | orderingChanged | reentrancyGuardAdded => true
  | visibilityTightened | accessControlAdded => false

/-- And that being wrong had a security consequence? -/
def securityRelevant : Signature → Bool
  | guardAdded | zeroCheckAdded | returnValueChecked | boundsAdjusted
  | overflowGuarded | orderingChanged | accessControlAdded
  | reentrancyGuardAdded => true
  | comparisonTightened | visibilityTightened => false

end Signature

/-! ## Vocabularies

    Identifier names, not natural language. A name here is part of the code's
    own surface — `require` is a Solidity builtin, not a word someone chose to
    describe their work. -/

private def guardWords : List String :=
  ["require", "assert", "revert", "_require", "ensure", "require_", "expect",
   "checkArgument", "Require", "panicking"]

private def zeroWords : List String :=
  ["ZeroAddress", "AddressZero", "ZERO_ADDRESS", "isZero", "NullAddress",
   "ZeroAmount", "nullptr", "NULL"]

private def successWords : List String :=
  ["success", "ok", "succeeded", "callStatus", "returnData"]

private def checkedArith : List String :=
  ["SafeMath", "checked_add", "checked_sub", "checked_mul", "checked_div",
   "saturating_add", "saturating_sub", "mulDiv", "tryAdd", "trySub",
   "safe_add", "safe_sub", "SafeCast", "toUint128", "toUint64", "toInt256"]

private def authWords : List String :=
  ["onlyOwner", "onlyRole", "onlyAdmin", "onlyGovernor", "onlyAuthorized",
   "requireAuth", "auth", "_checkOwner", "_checkRole", "accessControl",
   "onlySelf", "onlyManager"]

private def reentrancyWords : List String :=
  ["nonReentrant", "ReentrancyGuard", "_nonReentrantBefore", "noReentrancy",
   "lock", "_lock"]

/-- Paths that hold tests. A fix inside a test is still evidence that
    something was wrong, so these are not excluded from the walk — but a
    change confined to them did not alter what ships, so it may not carry a
    SECURITY verdict. Measured cause: "Test cleanup" and "use logs_gas
    modifier in concrete tests" both fired production-looking shapes. -/
private def testLike (path : String) : Bool :=
  [".t.sol", "_test.go", "_test.py", ".test.ts", ".test.js", ".spec.ts",
   ".spec.js", "_test.rs", "Test.java", "Tests.cs"].any (fun e => path.endsWith e)
  || ["test/", "tests/", "spec/", "mock/", "mocks/", "fixtures/", "__tests__/",
      "testdata/"].any (fun seg => (path.splitOn seg).length > 1)

private def strictOps : List String := ["<", ">"]
private def looseOps  : List String := ["<=", ">=", "==", "!="]
private def intTypes  : List String :=
  ["uint8", "uint16", "uint32", "uint64", "uint96", "uint128", "uint256",
   "int8", "int16", "int32", "int64", "int128", "int256",
   "u8", "u16", "u32", "u64", "u128", "i8", "i16", "i32", "i64", "i128"]

/-! ## Paired edits — the shape that actually carries precision

    Counting operator occurrences across a hunk was the first attempt and it
    failed badly: measured against an LLM reference on 246 real commits it
    reached a precision of 0.01, because any refactor changes operator counts
    and any `pragma solidity ^0.8.0 -> ^0.8.13` changes a numeric literal.

    What carries signal instead is a REMOVED line and an ADDED line that are
    identical except at one token. That is an author changing one thing on
    purpose, and the token they changed says what they meant. It is also the
    cheapest possible stand-in for the AST-level matching the literature uses
    (VFFinder, GraphSPD), without a parser per language. -/

/-- Lines that carry no runtime behaviour, so a literal changing inside one is
    never a bound being fixed. -/
private def nonBehavioural (ts : List Tok) : Bool :=
  match ts with
  | [] => true
  | t :: _ => ["pragma", "import", "use", "using", "from", "require_relative",
               "include", "package", "version", "SPDX"].contains t

/-- Same length, differing at exactly one position. Returns the added line's
    tokens together with the token that left and the token that arrived. -/
private def onePoint (before after : List Tok) : Option (List Tok × Tok × Tok) :=
  if before.length != after.length || nonBehavioural after then none
  else
    match (before.zip after).filter (fun p => p.1 != p.2) with
    | [(o, n)] => some (after, o, n)
    | _ => none

/-- Every one-token edit in the hunk. Bounded: a hunk large enough for the
    cross product to matter is a rewrite, and a rewrite has no single edit. -/
private def onePointEdits (h : Hunk) : List (List Tok × Tok × Tok) :=
  if h.removed.length > 40 || h.added.length > 40 then []
  else h.removed.flatMap (fun r =>
         h.added.filterMap (fun a => onePoint (tokens r) (tokens a)))

private def isComparison (t : Tok) : Bool := (strictOps ++ looseOps).contains t
private def isNumeric (t : Tok) : Bool :=
  match t.toList with | c :: _ => c.isDigit | [] => false

/-! ## Detectors -/

/-- Tokens the edit introduced, i.e. present after and absent before. -/
private def addedToks (h : Hunk) : List Tok :=
  introduced (tokensOf h.removed) (tokensOf h.added)

private def goneToks (h : Hunk) : List Tok :=
  removedToks (tokensOf h.removed) (tokensOf h.added)

private def countOf (ts : List Tok) (t : Tok) : Nat := (ts.filter (· == t)).length

/-- One comparison operator, in one line, swapped for a different one. -/
private def comparisonSwapped (h : Hunk) : Bool :=
  (onePointEdits h).any (fun e => isComparison e.2.1 && isComparison e.2.2)

/-- One numeric literal changed, in a line that performs a comparison or an
    index. A version string is excluded by `nonBehavioural`. -/
private def boundChanged (h : Hunk) : Bool :=
  (onePointEdits h).any (fun e =>
    isNumeric e.2.1 && isNumeric e.2.2
    && (e.1.any isComparison || e.1.contains "[") )

private def numericLiterals (ts : List Tok) : List Tok :=
  ts.filter (fun t => match t.toList with | c :: _ => c.isDigit | [] => false)

/-- Deduplicate, order preserved. A hunk that adds six `require`s is one
    shape, reported once; listing it six times would make `weight` a measure
    of file size. -/
private def dedup (ss : List Signature) : List Signature :=
  ss.foldl (fun acc s => if acc.contains s then acc else acc ++ [s]) []

def detect (h : Hunk) : List Signature :=
  let before := tokensOf h.removed
  let after  := tokensOf h.added
  let newT   := addedToks h
  let goneT  := goneToks h
  -- Every "X was introduced" shape below is a claim about code that already
  -- existed. In a file being created, or in a pure append with no surrounding
  -- context, the claim is unfounded: the author wrote new code, they did not
  -- decide that old code was wrong.
  let edits  := h.editsExistingCode
  -- A guard that arrives inside a large block of new code is part of that new
  -- code, not a decision that old code was wrong. Measured cause: "packed
  -- allowance and signature" and "add nonce invalidation" both fired
  -- `guard_added` while adding a feature. A surgical insertion is small
  -- relative to what it was inserted into.
  let surgical := h.added.length ≤ 12 || h.added.length * 2 ≤ h.context.length
  let prod     := !testLike h.file
  let sigs : List (Bool × Signature) :=
    [ (edits && surgical && mentionsAny newT guardWords
        && !mentionsAny before guardWords,
       Signature.guardAdded)
    , (edits && comparisonSwapped h, Signature.comparisonTightened)
    , (edits && surgical && (mentionsAny newT zeroWords
        || (mentionsAny after guardWords && mentions after "address"
            && mentions newT "0")),
       Signature.zeroCheckAdded)
    , (edits && surgical && mentionsAny newT successWords
        && mentionsAny after guardWords,
       Signature.returnValueChecked)
    , (edits && boundChanged h, Signature.boundsAdjusted)
    , (edits && surgical && (mentionsAny newT checkedArith
        || (mentions goneT "unchecked" && !mentions newT "unchecked")
        || (intTypes.any (mentions goneT) && intTypes.any (mentions newT))),
       Signature.overflowGuarded)
    , -- Reordering only means check-effects-interaction if there IS an
      -- interaction. Without this it fires on reordered function parameters,
      -- which is a signature change and not a fix.
      (!h.removed.isEmpty && !h.added.isEmpty
        && before.length == after.length
        && before != after
        && newT.isEmpty && goneT.isEmpty
        && mentionsAny after ["call", "transfer", "transferFrom", "send",
                              "delegatecall", "safeTransfer", "safeTransferFrom"],
       Signature.orderingChanged)
    , (edits && mentions goneT "public"
        && mentionsAny newT ["internal", "private", "external"],
       Signature.visibilityTightened)
    , (edits && surgical && (mentionsAny newT authWords
        || (mentionsAny after guardWords && mentions newT "sender"
            && !mentions before "sender")),
       Signature.accessControlAdded)
    , (edits && surgical && mentionsAny newT reentrancyWords,
       Signature.reentrancyGuardAdded)
    ]
  let fired := dedup ((sigs.filter (·.1)).map (·.2))
  -- A change confined to tests did not alter what ships. The shapes are still
  -- reported; they just stop counting as security evidence.
  if prod then fired else fired.filter (fun s => !s.securityRelevant)

/-! ## Whole-diff views -/

/-- Every signature the diff produces, in hunk order. -/
def signatures (d : Diff) : List Signature := d.hunks.flatMap detect

/-- The distinct shapes a diff shows. What a report should print. -/
def distinctSignatures (d : Diff) : List Signature := dedup (signatures d)

/-- Did the tokens change at all? If not, the edit was comments, whitespace or
    formatting — provably not a behaviour change, which is the one negative
    this classifier is entitled to assert. -/
def hunkTrivial (h : Hunk) : Bool := tokensOf h.removed == tokensOf h.added

def semanticallyTrivial (d : Diff) : Bool := d.hunks.all hunkTrivial

/-- Paths that cannot hold shipped behaviour. Conservative on purpose: a fix
    inside a test is still evidence of a bug, so tests are NOT listed here. -/
private def docLike (path : String) : Bool :=
  [".md", ".txt", ".rst", ".adoc", ".json", ".lock", ".yml", ".yaml",
   ".toml", ".cfg", ".ini", ".svg", ".png"].any (fun e => path.endsWith e)

def onlyDocs (d : Diff) : Bool := !d.hunks.isEmpty && d.hunks.all (fun h => docLike h.file)

end OssBugfix
