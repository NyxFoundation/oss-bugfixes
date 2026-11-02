"""diff.py — a unified diff, and a total parser for one.

Deliberately structural rather than clever. It consumes text git already
produced, so it never has to recover from malformed input: anything
unrecognised becomes context, which is the safe default because context can
only ever *fail* to produce a signature.

Totality matters operationally. A classifier that can raise on some input
silently drops commits from a corpus, and a dropped commit is
indistinguishable from one that was examined and rejected. Python cannot prove
termination the way Lean does, so `parse` is written as a single pass over
lines with no recursion and no unbounded loop, and `tests/test_detect.py`
hammers it with Hypothesis.
"""
from __future__ import annotations

from dataclasses import dataclass, field, replace
from typing import Iterator, Sequence

__all__ = ["Hunk", "Diff", "parse"]


@dataclass(frozen=True, slots=True)
class Hunk:
    """One contiguous edit: the lines that left and the lines that arrived.

    Context is kept because several signatures are about an edit *relative to*
    code that did not change — a guard wrapped around an existing statement
    looks like "nothing removed, a check added, and the statement is still
    there".
    """

    file: str
    removed: tuple[str, ...] = ()
    added: tuple[str, ...] = ()
    context: tuple[str, ...] = ()
    #: The `---` side was `/dev/null`: this hunk is a file being created. Every
    #: line in a new file is "added", so an edit signature meaning "X was
    #: introduced into code that already existed" cannot be read off it.
    #: Without this flag the first commit of any repository looks like forty
    #: security fixes at once.
    new_file: bool = False

    @property
    def touched(self) -> bool:
        return bool(self.removed or self.added)

    @property
    def edits_existing_code(self) -> bool:
        """Does this hunk modify code that was already there?"""
        return not self.new_file and bool(self.removed or self.context)


@dataclass(frozen=True, slots=True)
class Diff:
    hunks: tuple[Hunk, ...] = ()

    def __iter__(self) -> Iterator[Hunk]:
        return iter(self.hunks)

    def __len__(self) -> int:
        return len(self.hunks)

    def __add__(self, other: "Diff") -> "Diff":
        """Concatenation, used to state the monotonicity property."""
        return Diff(self.hunks + other.hunks)

    @property
    def touched(self) -> bool:
        return any(h.touched for h in self.hunks)

    @property
    def files(self) -> tuple[str, ...]:
        seen: list[str] = []
        for h in self.hunks:
            if h.file not in seen:
                seen.append(h.file)
        return tuple(seen)


def _header_path(line: str) -> str | None:
    """A path out of a `+++ b/path` or `--- a/path` header.

    `None` means `/dev/null` — git saying the file does not exist on that side.
    """
    for prefix in ("+++ ", "--- "):
        if line.startswith(prefix):
            rest = line[len(prefix):].strip()
            if rest == "/dev/null":
                return None
            if rest.startswith(("a/", "b/")):
                rest = rest[2:]
            return rest
    return None


@dataclass(slots=True)
class _State:
    file: str = ""
    is_new: bool = False
    cur: Hunk | None = None
    done: list[Hunk] = field(default_factory=list)

    def flush(self) -> None:
        if self.cur is not None:
            self.done.append(self.cur)
            self.cur = None


def parse(text: str) -> Diff:
    """Parse a unified diff. One pass over the lines; no recursion."""
    st = _State()
    for line in text.split("\n"):
        if line.startswith("diff --git"):
            st.flush()
            st.file, st.is_new = "", False
        elif line.startswith("---"):
            # `--- /dev/null` is git saying the file did not exist before.
            st.flush()
            st.is_new = _header_path(line) is None
        elif line.startswith("+++"):
            st.flush()
            path = _header_path(line)
            if path is not None:
                st.file = path
        elif line.startswith("@@"):
            st.flush()
            st.cur = Hunk(file=st.file, new_file=st.is_new)
        elif st.cur is not None:
            # `\ No newline at end of file` is metadata, not a line of code.
            if line.startswith("\\"):
                continue
            body = line[1:]
            if line.startswith("+"):
                st.cur = replace(st.cur, added=st.cur.added + (body,))
            elif line.startswith("-"):
                st.cur = replace(st.cur, removed=st.cur.removed + (body,))
            else:
                st.cur = replace(st.cur, context=st.cur.context + (body,))
    st.flush()
    return Diff(tuple(st.done))


def concat(diffs: Sequence[Diff]) -> Diff:
    out: tuple[Hunk, ...] = ()
    for d in diffs:
        out += d.hunks
    return Diff(out)
