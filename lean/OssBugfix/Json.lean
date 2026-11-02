/-
  Json.lean — just enough JSON to emit a verdict.

  Hand-rolled rather than imported so the package depends on nothing but the
  bare toolchain. A classifier that needs a package index to build is a
  classifier that stops building the day the index moves.
-/
import OssBugfix.Classify

namespace OssBugfix

private def escapeChar (c : Char) : String :=
  if c == '"' then "\\\""
  else if c == '\\' then "\\\\"
  else if c == '\n' then "\\n"
  else if c == '\r' then "\\r"
  else if c == '\t' then "\\t"
  else if c.toNat < 0x20 then
    let hex := Nat.toDigits 16 c.toNat
    "\\u" ++ "".pushn '0' (4 - hex.length) ++ String.ofList hex
  else String.ofList [c]

def jsonString (s : String) : String :=
  "\"" ++ String.join (s.toList.map escapeChar) ++ "\""

private def jsonBool : Option Bool → String
  | none       => "null"
  | some true  => "true"
  | some false => "false"

/-- One NDJSON record per commit. Keys match the framework's CSV columns so
    the Python side can drop them straight into a verdict row. -/
def Verdict.toJson (id : String) (v : Verdict) : String :=
  let sigs := String.intercalate "," (v.distinct.map (fun s => jsonString s.name))
  String.join
    [ "{\"id\":", jsonString id
    , ",\"is_bugfix\":", jsonBool v.isBugfix
    , ",\"is_security_fix\":", jsonBool v.isSecurityFix
    , ",\"ground\":", jsonString v.ground.name
    , ",\"signatures\":[", sigs, "]"
    , ",\"weight\":", toString v.weight
    , "}" ]

end OssBugfix
