"""strip.py — re-render a unified diff with the prose taken out.

Why this exists. A model reads a diff as text, and a diff's text contains
sentences written by the author about the code — which is exactly the channel
this corpus has decided not to trust. The failure is not hypothetical:

    solmate "📝 Comment fixes" changed 24 lines, all of them comments, among
    them `// We use 100 because the calldata length is 4 + 32 * 2.` corrected
    to `// We use 68 …`, while the code beside it always read 68. A model
    reported "corrects the calldata length constants … fixing a functional
    bug". It had read a comment about a constant as the constant.

Handing the model code-only text makes that mistake unavailable rather than
unlikely, and it shortens the prompt, which is the other thing that was wrong
with the sweep.

What is removed: lines whose changed content is entirely a comment, and the
trailing comment on a line that also has code. What is kept: every structural
marker (`diff --git`, `---`, `+++`, `@@`) so the result is still a diff, and
every line of actual code.

What this is NOT: a lexer. It tracks quoting well enough not to cut a `//`
inside a string literal — the case that would otherwise corrupt a URL or a
revert message — and it recognises the block-comment forms that appear in the
languages this corpus covers. A construct it misreads costs a comment line in
the prompt, never a line of code.
"""
from __future__ import annotations

from dataclasses import dataclass

__all__ = ["code_only", "strip_line_comment", "StripStats", "has_code_change"]

#: Line-comment introducers, longest first so `///` is seen before `//`.
_LINE_MARKERS: tuple[str, ...] = ("///", "//", "#", "--", ";;")
_BLOCK_OPEN: tuple[str, ...] = ("/*", "<!--", '"""', "'''")
_BLOCK_CLOSE: dict[str, str] = {"/*": "*/", "<!--": "-->", '"""': '"""', "'''": "'''"}
_STRUCTURAL: tuple[str, ...] = ("diff --git", "index ", "--- ", "+++ ", "@@",
                                "new file", "deleted file", "similarity ",
                                "rename ", "old mode", "new mode", "Binary ")


@dataclass
class StripStats:
    lines_in: int = 0
    lines_out: int = 0
    chars_in: int = 0
    chars_out: int = 0

    @property
    def saved(self) -> float:
        return 0.0 if not self.chars_in else 1 - self.chars_out / self.chars_in


def has_code_change(diff_text: str) -> bool:
    """Does this diff add or remove a line that is not purely a comment?

    `code_only` keeps the structural markers so its output is still a diff,
    which means "did anything survive" cannot be answered by truthiness of the
    result — it is always non-empty. Ask the real question instead.
    """
    for line in code_only(diff_text).split("\n"):
        if line[:1] in ("+", "-") and not line.startswith(("+++", "---")):
            if line[1:].strip():
                return True
    return False


def strip_line_comment(text: str) -> str:
    """Drop a trailing line comment, respecting quotes.

    `"https://x"` keeps its slashes; `x = 1 // why` becomes `x = 1`. A `#`
    inside a string is likewise left alone, which matters for Solidity's
    `"#"` separators and for shell-ish fixtures.
    """
    quote: str | None = None
    i = 0
    n = len(text)
    while i < n:
        c = text[i]
        if quote is not None:
            if c == "\\":
                i += 2
                continue
            if c == quote:
                quote = None
            i += 1
            continue
        if c in "\"'`":
            quote = c
            i += 1
            continue
        for marker in _LINE_MARKERS:
            if text.startswith(marker, i):
                # `--` is a comment in SQL and Lua but an operator everywhere
                # else, so only honour it with whitespace on its left.
                if marker == "--" and i and not text[i - 1].isspace():
                    break
                return text[:i].rstrip()
        i += 1
    return text.rstrip()


def _is_block_only(body: str, state: list[str]) -> bool:
    """Is this line inside, or entirely, a block comment?

    `state` is a one-element list holding the currently open block marker, so
    the caller can carry it across lines.
    """
    stripped = body.strip()
    if state[0]:
        closer = _BLOCK_CLOSE[state[0]]
        if closer in stripped:
            state[0] = ""
            # Anything after the closer is code again.
            return not stripped.split(closer, 1)[1].strip()
        return True
    for opener in _BLOCK_OPEN:
        if stripped.startswith(opener):
            closer = _BLOCK_CLOSE[opener]
            rest = stripped[len(opener):]
            if closer in rest:
                return not rest.split(closer, 1)[1].strip()
            state[0] = opener
            return True
    # A javadoc/doxygen continuation line.
    return stripped.startswith("*") and not stripped.startswith("*/")


def code_only(diff_text: str, *, stats: StripStats | None = None) -> str:
    """A unified diff with comment-only lines dropped and trailing comments cut.

    Hunk headers are kept even when the hunk empties out, because a model that
    is shown "this file changed, and none of the changes were code" has learned
    something true.
    """
    out: list[str] = []
    state = [""]
    for line in diff_text.split("\n"):
        if stats is not None:
            stats.lines_in += 1
            stats.chars_in += len(line) + 1
        if not line:
            continue
        if line.startswith(_STRUCTURAL) or line.startswith("\\"):
            state[0] = ""
            out.append(line)
            continue
        marker, body = line[0], line[1:]
        if marker not in "+- ":
            out.append(line)
            continue
        if _is_block_only(body, state):
            continue
        code = strip_line_comment(body)
        if not code.strip():
            continue
        out.append(marker + code)
    text = "\n".join(out)
    if stats is not None:
        stats.lines_out = len(out)
        stats.chars_out = len(text)
    return text
