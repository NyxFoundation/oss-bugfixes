"""Comment stripping.

The model is shown code, not prose. This is both a correctness measure — a
model that cannot see a comment cannot be fooled by one — and the single
largest reduction in prompt size available (22% across 5,607 real commits).

The rule the tests enforce: a construct the stripper misreads may cost a
comment, never a line of code.
"""
from __future__ import annotations

from hypothesis import given, settings
from hypothesis import strategies as st

from ossbugfix.detect import code_only, has_code_change, strip_line_comment

SOLMATE_COMMENT_FIXES = (
    "diff --git a/src/erc20/SafeERC20.sol b/src/erc20/SafeERC20.sol\n"
    "--- a/src/erc20/SafeERC20.sol\n+++ b/src/erc20/SafeERC20.sol\n"
    "@@ -78,7 +78,7 @@\n"
    "             mstore(add(callData, 36), amount)\n"
    "-            // We use 100 because the calldata length is 4 + 32 * 2.\n"
    "+            // We use 68 because the calldata length is 4 + 32 * 2.\n"
    "             let callStatus := call(gas(), token, 0, callData, 68, 0, 0)\n")


def test_a_comment_about_a_constant_leaves_no_changed_code():
    """The commit a model read as "corrects the calldata length constants"."""
    assert has_code_change(SOLMATE_COMMENT_FIXES) is False
    out = code_only(SOLMATE_COMMENT_FIXES)
    assert "We use 100" not in out and "We use 68" not in out
    assert "callStatus := call" in out, "the surrounding code must survive"


def test_a_url_in_a_string_keeps_its_slashes():
    assert strip_line_comment('string u = "https://example.com/a";') == \
        'string u = "https://example.com/a";'


def test_a_trailing_comment_is_cut_but_the_code_stays():
    assert strip_line_comment("x = 1; // why") == "x = 1;"
    assert strip_line_comment("y = 2  # python") == "y = 2"


def test_a_minus_minus_is_only_a_comment_with_space_before_it():
    assert strip_line_comment("i--;") == "i--;"
    assert strip_line_comment("SELECT 1 -- a comment") == "SELECT 1"


def test_block_comments_are_dropped_across_lines():
    diff = ("--- a/A.sol\n+++ b/A.sol\n@@ -1,5 +1,5 @@\n"
            "-/* this spans\n-   several lines */\n"
            "+/* and is replaced\n+   by this */\n"
            " uint256 x = 1;\n")
    assert has_code_change(diff) is False
    assert "spans" not in code_only(diff)
    assert "uint256 x = 1;" in code_only(diff)


def test_javadoc_continuations_are_dropped():
    diff = ("--- a/A.java\n+++ b/A.java\n@@ -1,3 +1,3 @@\n"
            "-     * old doc\n+     * new doc\n     return 1;\n")
    assert has_code_change(diff) is False


def test_a_real_code_change_survives():
    diff = ("--- a/A.sol\n+++ b/A.sol\n@@ -1,3 +1,3 @@\n ctx\n"
            "-        if (i < len) {   // bound\n"
            "+        if (i <= len) {  // bound\n")
    assert has_code_change(diff) is True
    out = code_only(diff)
    assert "if (i <= len) {" in out and "// bound" not in out


def test_structural_markers_are_preserved():
    out = code_only(SOLMATE_COMMENT_FIXES)
    for marker in ("diff --git", "--- a/", "+++ b/", "@@"):
        assert marker in out


@given(st.text(max_size=300))
@settings(max_examples=400, deadline=None)
def test_strip_line_comment_never_lengthens_a_line(line: str):
    assert len(strip_line_comment(line)) <= len(line)


@given(st.lists(st.text(max_size=120).map(lambda s: "+" + s), max_size=30)
       .map(lambda ls: "--- a/x\n+++ b/x\n@@ -1 +1 @@\n" + "\n".join(ls)))
@settings(max_examples=300, deadline=None)
def test_code_only_is_total_and_shrinking(diff: str):
    out = code_only(diff)
    assert isinstance(out, str)
    assert len(out) <= len(diff) + 1


@given(st.text(max_size=200))
@settings(max_examples=300, deadline=None)
def test_stripping_is_idempotent(line: str):
    once = strip_line_comment(line)
    assert strip_line_comment(once) == once
