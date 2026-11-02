/-
  Diff.lean — a unified diff, and a total parser for one.

  The parser is deliberately structural rather than clever. It consumes the
  text git already produced, so it never has to recover from malformed input:
  anything it does not recognise becomes context, which is the safe default
  because context can only ever *fail* to produce a signature.

  Totality is the point. A classifier that can diverge or throw on some input
  is a classifier that silently drops commits from a corpus, and a dropped
  commit is indistinguishable from a commit that was examined and rejected.
  Lean checks that for us here rather than leaving it to a test suite.
-/

namespace OssBugfix

/-- One contiguous edit: the lines that left and the lines that arrived.

    Context lines are kept separately because several signatures are about an
    edit *relative to* code that did not change — a guard wrapped around an
    existing statement looks like "nothing removed, a check added, and the
    statement is still there". -/
structure Hunk where
  file    : String
  removed : List String
  added   : List String
  context : List String
  /-- The `---` side was `/dev/null`: this hunk is a file being created.
      Every line in a new file is "added", so an edit signature that means
      "X was introduced into code that already existed" cannot be read off it.
      Without this flag the first commit of any repository looks like forty
      security fixes at once. -/
  newFile : Bool
  deriving Repr, Inhabited, DecidableEq

/-- A whole commit's patch. -/
structure Diff where
  hunks : List Hunk
  deriving Repr, Inhabited, DecidableEq

namespace Hunk

def empty (file : String) (newFile : Bool := false) : Hunk :=
  ⟨file, [], [], [], newFile⟩

/-- Does this hunk modify code that was already there? -/
def editsExistingCode (h : Hunk) : Bool :=
  !h.newFile && (!h.removed.isEmpty || !h.context.isEmpty)

/-- Did anything at all change here? -/
def touched (h : Hunk) : Bool := !h.removed.isEmpty || !h.added.isEmpty

end Hunk

namespace Diff

/-- Distinct file paths, order preserved. A fold rather than a recursive
    filter so termination is structural and needs no measure. -/
def files (d : Diff) : List String :=
  (d.hunks.map (·.file)).foldl (fun acc f => if acc.contains f then acc else acc ++ [f]) []

def touched (d : Diff) : Bool := d.hunks.any Hunk.touched

/-- Concatenation, used to state the monotonicity property. -/
def append (a b : Diff) : Diff := ⟨a.hunks ++ b.hunks⟩

instance : Append Diff := ⟨append⟩

end Diff

/-! ## Parsing -/

private def stripPrefix (s p : String) : Option String :=
  if s.startsWith p then some (s.drop p.length).toString else none

/-- A file path out of a `+++ b/path` or `--- a/path` header. -/
private def headerPath (line : String) : Option String :=
  match stripPrefix line "+++ " <|> stripPrefix line "--- " with
  | none => none
  | some rest =>
    let rest := rest.trimAscii.toString
    if rest == "/dev/null" then none
    else some (if rest.startsWith "a/" || rest.startsWith "b/" then (rest.drop 2).toString else rest)

/-- Parser state: the file we are in, the hunk being built, and what is done. -/
private structure St where
  file  : String
  isNew : Bool
  cur   : Option Hunk
  done  : List Hunk

private def flush (st : St) : St :=
  match st.cur with
  | none => st
  | some h => { st with cur := none, done := h :: st.done }

private def step (st : St) (line : String) : St :=
  if line.startsWith "diff --git" then
    { flush st with file := "", isNew := false }
  else if line.startsWith "---" then
    -- `--- /dev/null` is git saying the file did not exist before.
    match headerPath line with
    | some _ => { flush st with isNew := false }
    | none   => { flush st with isNew := true }
  else if line.startsWith "+++" then
    match headerPath line with
    | some p => { flush st with file := p }
    | none   => flush st
  else if line.startsWith "@@" then
    let st := flush st
    { st with cur := some (Hunk.empty st.file st.isNew) }
  else
    match st.cur with
    | none => st
    | some h =>
      -- `\ No newline at end of file` is metadata, not a line of code.
      if line.startsWith "\\" then st
      else if line.startsWith "+" then
        { st with cur := some { h with added := h.added ++ [(line.drop 1).toString] } }
      else if line.startsWith "-" then
        { st with cur := some { h with removed := h.removed ++ [(line.drop 1).toString] } }
      else
        { st with cur := some { h with context := h.context ++ [(line.drop 1).toString] } }

/-- Parse a unified diff. Total: a fold over the lines, with every
    unrecognised line treated as context. -/
def parse (text : String) : Diff :=
  let st := (text.splitOn "\n").foldl step { file := "", isNew := false, cur := none, done := [] }
  ⟨(flush st).done.reverse⟩

end OssBugfix
