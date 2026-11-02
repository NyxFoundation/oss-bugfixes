/-
  Main — the deterministic classifier as a pipeline stage.

  Protocol, chosen so that diff text needs no escaping on the way in: stdin is
  a sequence of records, each introduced by \x1e, with the id and the diff
  separated by \x1f. Both are C0 control characters that git never emits, so a
  diff can contain anything at all — including newlines, quotes and backslashes
  — and still arrive intact. The same framing the commit walker uses.

  Output is NDJSON on stdout, one line per record, in input order.

    oss-bugfixes classify --engine lean      # the Python side speaks this
    printf '\x1eabc\x1f%s' "$(git show HEAD)" | ossbugfix-classify
-/
import OssBugfix

open OssBugfix

def handle (record : String) : Option String :=
  if record.isEmpty then none
  else
    let parts := record.splitOn "\x1F"
    match parts with
    | [] => none
    | [_] => none
    | id :: rest =>
      let diffText := String.intercalate "\x1F" rest
      some (Verdict.toJson id.trimAscii.toString (classify (parse diffText)))

def main (args : List String) : IO UInt32 := do
  if args.contains "--version" then
    IO.println "ossbugfix-classify 0.1.0 (lean)"
    return 0
  if args.contains "--selftest" then
    -- A patch that adds a bound check must come back as a security fix, and a
    -- comment-only edit must come back refuted. If those two ever stop
    -- holding, every downstream number is wrong.
    let guard := "--- a/A.sol\n+++ b/A.sol\n@@ -1,3 +1,4 @@\n function f(uint256 i) {\n+        require(i < len, \"oob\");\n         return a[i];\n }\n"
    let cmt   := "--- a/A.sol\n+++ b/A.sol\n@@ -1,2 +1,2 @@\n-// old words\n+// new words\n   return 1;\n"
    let v1 := classify (parse guard)
    let v2 := classify (parse cmt)
    IO.println (Verdict.toJson "guard" v1)
    IO.println (Verdict.toJson "comment" v2)
    if v1.isSecurityFix != some true then
      IO.eprintln "selftest: a added guard was not reported as a security fix"
      return 1
    if v2.isBugfix != some false then
      IO.eprintln "selftest: a comment-only edit was not refuted"
      return 1
    IO.println "selftest ok"
    return 0
  let stdin ← IO.getStdin
  let text ← stdin.readToEnd
  let out ← IO.getStdout
  for record in text.splitOn "\x1E" do
    match handle record with
    | some line => out.putStrLn line
    | none => pure ()
  return 0
