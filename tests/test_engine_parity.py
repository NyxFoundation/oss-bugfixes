"""The two deterministic engines must not drift apart.

`ossbugfix/detect` (typed Python) and `lean/OssBugfix` (Lean 4) implement the
same ten detectors and the same verdict algebra. Two copies of a decision rule
in two languages will drift, and a drift is a bug in one of them — this is the
test that notices.

The Lean half is skipped when the binary has not been built, so the suite
still runs on a machine with no Lean toolchain.
"""
from __future__ import annotations

import json
import subprocess

import pytest

from ossbugfix.classify.lean import binary_path
from ossbugfix.detect import classify, parse

REC, FLD = "\x1e", "\x1f"

CASES: dict[str, str] = {
    "guard_into_existing": (
        "--- a/A.sol\n+++ b/A.sol\n@@ -1,3 +1,4 @@\n"
        " function f(uint256 i) {\n+        require(i < len);\n"
        "         return a[i];\n }\n"),
    "comment_only": (
        "--- a/A.sol\n+++ b/A.sol\n@@ -1,2 +1,2 @@\n"
        "-// old words\n+// new words\n   return 1;\n"),
    "new_file_with_guards": (
        "--- /dev/null\n+++ b/New.sol\n@@ -0,0 +1,3 @@\n"
        "+function f() {\n+    require(msg.sender == owner);\n+}\n"),
    "pragma_bump": (
        "--- a/A.sol\n+++ b/A.sol\n@@ -1,1 +1,1 @@\n"
        "-pragma solidity ^0.8.0;\n+pragma solidity ^0.8.13;\n"),
    "comparison_swap": (
        "--- a/A.sol\n+++ b/A.sol\n@@ -1,2 +1,2 @@\n ctx\n"
        "-        if (i < len) {\n+        if (i <= len) {\n"),
    "unchecked_removed": (
        "--- a/A.sol\n+++ b/A.sol\n@@ -1,3 +1,3 @@\n ctx\n"
        "-        unchecked { x += y; }\n+        x += y;\n"),
    "test_only_guard": (
        "--- a/test/A.t.sol\n+++ b/test/A.t.sol\n@@ -1,3 +1,4 @@\n"
        " function testF() {\n+        require(x > 0);\n         f();\n }\n"),
    "docs_only": (
        "--- a/README.md\n+++ b/README.md\n@@ -1,1 +1,1 @@\n-old\n+new\n"),
    "empty": "",
}


def _lean_verdicts() -> dict[str, dict]:
    payload = "".join(f"{REC}{name}{FLD}{text}" for name, text in CASES.items())
    r = subprocess.run([str(binary_path())], input=payload, capture_output=True,
                       text=True, timeout=120)
    assert r.returncode == 0, r.stderr[-400:]
    return {json.loads(l)["id"]: json.loads(l)
            for l in r.stdout.splitlines() if l.strip()}


@pytest.mark.skipif(not binary_path().exists(),
                    reason="lean binary not built (cd lean && lake build)")
@pytest.mark.parametrize("name", list(CASES))
def test_python_and_lean_agree(name: str):
    lean = _lean_verdicts().get(name)
    py = classify(parse(CASES[name]))
    if lean is None:
        # The Lean side emits nothing for an empty record, which is its way of
        # saying "no commit here"; Python refutes an empty diff. Both are
        # defensible, neither is a verdict about code.
        assert not CASES[name].strip()
        return
    assert lean["is_bugfix"] == py.is_bugfix, f"{name}: bugfix differs"
    assert lean["is_security_fix"] == py.is_security_fix, f"{name}: security differs"
    assert sorted(lean["signatures"]) == sorted(s.value for s in py.distinct), \
        f"{name}: signatures differ"
