"""tokens.py — a line of source, as the tokens that matter.

Not a parser for any language. The detectors ask questions like "did a
comparison operator change" or "was a `require` introduced", and those are
answerable from a token sequence without knowing the grammar. One tokenizer
shared by Solidity, Rust, Go, C and TypeScript beats five parsers to keep
correct.

Comments are stripped, because a corpus built on "the message does not decide"
should not then let a code comment decide either.
"""
from __future__ import annotations

from typing import Final, Iterable, Sequence

__all__ = ["tokens", "tokens_of", "introduced", "removed_toks", "mentions",
           "mentions_any"]

Tok = str

#: Pairs worth keeping as one token. Splitting `<=` into `<` and `=` would make
#: every tightened bound look like an unchanged comparison, which is the single
#: most common real signature in this corpus.
_MERGEABLE: Final[frozenset[str]] = frozenset({
    "<=", ">=", "==", "!=", "&&", "||", "->", "=>", "::", "++", "--",
    "+=", "-=", "*=", "/=", "%=", "|=", "&=", "^=", "<<", ">>", "**",
    "<<=", ">>=", "===", "!==", "**=",
})

_IDENT_EXTRA: Final[frozenset[str]] = frozenset({"_", "$"})


def _is_ident_char(c: str) -> bool:
    return c.isalnum() or c in _IDENT_EXTRA


def _strip_comment(line: str) -> str:
    """Cut a trailing line comment. A `//` inside a string literal is cut too;
    that costs a token in a string, which no detector reads."""
    for marker in ("//", "#"):
        idx = line.find(marker)
        if idx >= 0:
            line = line[:idx]
    return line


def _runs(line: str) -> list[Tok]:
    """Split into runs of word characters and single other characters."""
    out: list[Tok] = []
    buf: list[str] = []
    for c in line:
        if c in " \t\r":
            if buf:
                out.append("".join(buf))
                buf = []
        elif _is_ident_char(c):
            # A digit following an identifier run belongs to that identifier
            # (`uint256`), so both word kinds accumulate into one buffer.
            buf.append(c)
        else:
            if buf:
                out.append("".join(buf))
                buf = []
            out.append(c)
    if buf:
        out.append("".join(buf))
    return out


def _merge_pass(ts: Sequence[Tok]) -> list[Tok]:
    """Glue adjacent tokens that together spell an operator. Run twice and
    `<<` + `=` becomes `<<=`."""
    out: list[Tok] = []
    for t in ts:
        if out and (out[-1] + t) in _MERGEABLE:
            out[-1] = out[-1] + t
        else:
            out.append(t)
    return out


def tokens(line: str) -> list[Tok]:
    """The tokens of a source line, comments removed."""
    return _merge_pass(_merge_pass(_runs(_strip_comment(line))))


def tokens_of(lines: Iterable[str]) -> list[Tok]:
    out: list[Tok] = []
    for line in lines:
        out.extend(tokens(line))
    return out


def mentions(ts: Sequence[Tok], name: str) -> bool:
    return name in ts


def mentions_any(ts: Sequence[Tok], names: Iterable[str]) -> bool:
    s = set(ts)
    return any(n in s for n in names)


def introduced(before: Sequence[Tok], after: Sequence[Tok]) -> list[Tok]:
    """Tokens present after and absent before — what the edit introduced."""
    b = set(before)
    return [t for t in after if t not in b]


def removed_toks(before: Sequence[Tok], after: Sequence[Tok]) -> list[Tok]:
    a = set(after)
    return [t for t in before if t not in a]
