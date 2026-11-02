/-
  Token.lean — a line of source, as the tokens that matter.

  Not a parser for any language. The detectors in `Signature.lean` ask
  questions like "did a comparison operator change" or "was a `require`
  introduced", and those are answerable from a token sequence without knowing
  the grammar. That is deliberate: this file is shared by Solidity, Rust, Go,
  C and TypeScript, and a real parser for each would be five parsers to keep
  correct instead of one tokenizer to keep total.

  Comments are stripped, because a corpus built on "the message does not
  decide" should not then let a code comment decide either.
-/

namespace OssBugfix

/-- A token is just its text. Keeping it a `String` rather than an inductive
    keeps the detectors decidable by `decide` and the proofs about them short. -/
abbrev Tok := String

private def isIdentStart (c : Char) : Bool := c.isAlpha || c == '_' || c == '$'
private def isIdentChar  (c : Char) : Bool := c.isAlphanum || c == '_' || c == '$'

/-- Operator pairs worth keeping as one token. Splitting `<=` into `<` and
    `=` would make every tightened bound look like an unchanged comparison,
    which is the single most common real signature in this corpus. -/
private def mergeable : List String :=
  ["<=", ">=", "==", "!=", "&&", "||", "->", "=>", "::", "++", "--",
   "+=", "-=", "*=", "/=", "%=", "|=", "&=", "^=", "<<", ">>", "**",
   "<<=", ">>=", "===", "!==", "**="]

/-- Strip a trailing line comment. `//` for C-likes, `#` for the rest. A `//`
    inside a string literal would be cut too; that costs a token in a string,
    which no detector reads. -/
private def stripComment : List Char → List Char
  | [] => []
  | '/' :: '/' :: _ => []
  | '#' :: _ => []
  | c :: rest => c :: stripComment rest

private inductive Kind | ident | digit | space | other
  deriving DecidableEq

private def kindOf (c : Char) : Kind :=
  if c == ' ' || c == '\t' || c == '\r' then Kind.space
  else if isIdentChar c then (if c.isDigit then Kind.digit else Kind.ident)
  else Kind.other

/-- Split into runs. A fold over the characters, so termination is structural
    and the function is total by construction rather than by a measure. -/
private def runs (cs : List Char) : List Tok :=
  let (out, buf) := cs.foldl step ([], ([] : List Char))
  (flushBuf out buf).reverse
where
  flushBuf (out : List Tok) (buf : List Char) : List Tok :=
    if buf.isEmpty then out else String.ofList buf.reverse :: out
  step (st : List Tok × List Char) (c : Char) : List Tok × List Char :=
    let (out, buf) := st
    match kindOf c with
    | Kind.space => (flushBuf out buf, [])
    | Kind.other => (String.ofList [c] :: flushBuf out buf, [])
    | _ =>
      -- A digit that follows an identifier run belongs to that identifier
      -- (`uint256`), so both word kinds accumulate into the same buffer.
      (out, c :: buf)

/-- One merge pass: glue adjacent tokens that together spell an operator.
    Run twice and `<<` + `=` becomes `<<=`. -/
private def mergePass (ts : List Tok) : List Tok :=
  (ts.foldl step []).reverse
where
  step (acc : List Tok) (t : Tok) : List Tok :=
    match acc with
    | prev :: rest =>
      let joined := prev ++ t
      if mergeable.contains joined then joined :: rest else t :: acc
    | [] => [t]

/-- The tokens of a source line, comments removed. -/
def tokens (line : String) : List Tok :=
  mergePass (mergePass (runs (stripComment line.toList)))

/-- Every token of a list of lines, in order. -/
def tokensOf (lines : List String) : List Tok := lines.flatMap tokens

/-- Does this token sequence mention `name` as an identifier? -/
def mentions (ts : List Tok) (name : String) : Bool := ts.contains name

/-- Does it mention any of these? -/
def mentionsAny (ts : List Tok) (names : List String) : Bool := names.any (mentions ts)

/-- Tokens present in `b` but not in `a`. The basic shape of "what the edit
    introduced", used by almost every detector. -/
def introduced (a b : List Tok) : List Tok := b.filter (fun t => !a.contains t)

/-- Tokens present in `a` but not in `b`. -/
def removedToks (a b : List Tok) : List Tok := a.filter (fun t => !b.contains t)

end OssBugfix
