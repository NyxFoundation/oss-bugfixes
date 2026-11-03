"""walk.py — every commit in a repository's history, with no keyword filter.

This is the stage the whole repository exists for, and the reason it does not
start from a search query. A keyword crawl decides what to look at from the
words the author chose, which is precisely the assumption a *silent fix*
breaks: the commit that says "cleanup" or "refactor validation" is the one
worth reading. So the walk reads everything and lets the classifier be the only
judge of meaning.

What is filtered here is STRUCTURAL ONLY — facts about the commit, never a
guess at what it means:

  * merge commits carry no diff of their own
  * a commit touching no source file cannot contain a code defect
  * a commit above `max_files` is a vendored drop, generated output or a
    reformatting sweep: its diff neither fits a prompt nor describes one defect

Two deterministic signals are recorded, both computed from git rather than from
text:

  is_backport        the same patch (by `git patch-id`, which hashes the diff
                     and so survives a cherry-pick's new sha) appears under more
                     than one sha. Backporting to a maintenance branch is extra
                     work a team only does when users on the old release cannot
                     wait — the developers' own statement of urgency, made by
                     action rather than in words, which is why it survives where
                     a keyword does not.
  n_patch_siblings   how many shas share that patch-id.

`is_backport` is boolean-with-NA and is **never** written as False when
detection was skipped. Reporting "0 backported" for a repo that in fact timed
out is indistinguishable from a repo that really backports nothing, and that
confusion has already cost one analysis its conclusion.

Performance notes that are not negotiable:
  * ONE `git log` for the whole history. A per-commit `git show` spends all its
    time on process spawn and cannot walk a 1,000-commit repo in two minutes.
  * The log is parsed as a STREAM. A 600k-commit monorepo does not fit in
    memory as one string.
  * `--name-only`, NOT `--numstat`, by default. Line counts require git to
    diff file CONTENTS, and on a blobless mirror every one of those is a lazy
    blob fetch — a network round trip per commit. Measured on a fresh blobless
    argon2 (496 commits): name-only finished in under 0.1 s, numstat took
    214.7 s. File paths come from the trees, which a blobless clone already
    has. Ask for `line_counts=True` only against a hydrated mirror
    (`oss-bugfixes mirror --hydrate`), where the same numstat takes 2.0 s.
"""
from __future__ import annotations

import json
import os
import signal
import subprocess
import sys
from dataclasses import dataclass, field, replace
from pathlib import Path
from typing import Iterator, Sequence

import pandas as pd

from . import mirror, schema
from .registry import Repo

__all__ = ["WalkConfig", "WalkResult", "walk", "iter_commits", "SOURCE_EXT",
           "current_tips", "live_tips"]

#: Extensions that make a path "source". Deliberately generous — a domain that
#: needs more (`.cairo`, `.move`, `.tla`) adds it here rather than in a crawler.
SOURCE_EXT: frozenset[str] = frozenset({
    ".ts", ".tsx", ".js", ".jsx", ".mjs", ".cjs", ".go", ".rs", ".py", ".c",
    ".cc", ".cpp", ".cxx", ".h", ".hpp", ".hh", ".java", ".kt", ".kts",
    ".swift", ".m", ".mm", ".sol", ".vy", ".nim", ".dart", ".rb", ".cs",
    ".scala", ".ex", ".exs", ".erl", ".hs", ".ml", ".mli", ".zig", ".move",
    ".cairo", ".s", ".asm", ".php", ".pl", ".lua", ".v", ".sv", ".vhd",
    ".proto", ".lean", ".fs", ".fsi", ".clj", ".jl",
})

LANG_BY_EXT: dict[str, str] = {
    ".ts": "typescript", ".tsx": "typescript", ".js": "javascript",
    ".jsx": "javascript", ".mjs": "javascript", ".cjs": "javascript",
    ".go": "go", ".rs": "rust", ".py": "python", ".c": "c", ".h": "c",
    ".cc": "cpp", ".cpp": "cpp", ".cxx": "cpp", ".hpp": "cpp", ".hh": "cpp",
    ".java": "java", ".kt": "kotlin", ".kts": "kotlin", ".swift": "swift",
    ".m": "objc", ".mm": "objc", ".sol": "solidity", ".vy": "vyper",
    ".nim": "nim", ".dart": "dart", ".rb": "ruby", ".cs": "csharp",
    ".scala": "scala", ".ex": "elixir", ".exs": "elixir", ".erl": "erlang",
    ".hs": "haskell", ".ml": "ocaml", ".mli": "ocaml", ".zig": "zig",
    ".move": "move", ".cairo": "cairo", ".php": "php", ".lua": "lua",
    ".v": "verilog", ".sv": "verilog", ".vhd": "vhdl", ".lean": "lean",
    ".clj": "clojure", ".jl": "julia", ".proto": "protobuf",
}

# Record and body terminators. \x1e starts a record, \x1d ends the message body;
# the numstat block follows. Both are C0 control characters that do not occur in
# real commit messages, which is what lets a body contain newlines (and even a
# stray field separator) without derailing the parse.
_REC = "\x1e"
_FLD = "\x1f"
_END = "\x1d"
_FORMAT = (f"{_REC}%H{_FLD}%P{_FLD}%an{_FLD}%ae{_FLD}%aI{_FLD}%cI{_FLD}%s{_FLD}%b{_END}")

_REVERT_PREFIXES = ('revert "', "revert '", "revert commit", "this reverts commit")


@dataclass(frozen=True)
class WalkConfig:
    """Structural knobs. None of these encode an opinion about meaning."""

    #: Above this many files the commit is a vendored drop / generated output /
    #: formatting sweep. 60 is the value the wallet corpus settled on.
    max_files: int = 60
    #: Drop commits that touch no recognised source file.
    require_source: bool = True
    #: Walk every ref, not just the default branch. Silent fixes live on
    #: maintenance branches as often as on main.
    all_refs: bool = True
    #: Record insertions/deletions. Off by default: it forces git to read file
    #: contents, which on a blobless mirror is one network fetch per commit.
    #: Requires a hydrated mirror to be anything but ruinously slow.
    line_counts: bool = False
    #: Cap commits walked. 0 = the whole history. Smoke runs only.
    limit: int = 0
    #: Attempt patch-id backport detection. It renders every diff in the
    #: history, so on a blobless mirror it is the same lazy-fetch trap as
    #: `line_counts` — the walk skips it there and says so rather than burning
    #: the timeout to learn nothing.
    detect_backports: bool = True
    #: Seconds for the patch-id render. 90, not 420: no repo where detection
    #: succeeds needs anything close, so a short budget caps the waste.
    patch_id_timeout: int = 90
    #: Above this many commits the render never finishes inside the budget, so
    #: attempting it costs the full timeout to learn nothing. Measured against
    #: repos that completed (≤4,900 commits) and timed out (≥6,200).
    patch_id_max_commits: int = 6_000
    #: Hard ceiling on one `git log`. A repo that cannot be walked in this is
    #: reported as failed rather than hanging a 200-repo sweep.
    log_timeout: int = 3_600
    #: Ref tips from a previous walk. Commits reachable from these are not
    #: emitted again, which turns a re-walk into "what landed since last time".
    #: Empty = walk the whole history.
    since_tips: tuple[str, ...] = ()
    #: Subtrees in scope, from the registry. Empty = the whole repository.
    #: Passed to git as a pathspec, so git does the filtering and never emits
    #: the commits at all — which is the point: the alternative is reading
    #: forty thousand commits in order to discard them.
    paths: tuple[str, ...] = ()

    def fingerprint(self) -> str:
        """Identity of the SHAPE of the output, so an incremental walk can
        refuse to append rows the previous pass would not have produced.

        `limit` and `since_tips` are excluded: they change how much is walked,
        not what a kept row looks like.
        """
        parts = (self.max_files, self.require_source, self.all_refs,
                 self.line_counts, ",".join(sorted(self.paths)))
        return "|".join(str(x) for x in parts)


@dataclass
class WalkResult:
    repo: Repo
    frame: pd.DataFrame
    seen: int = 0                 # commits git emitted
    kept: int = 0                 # commits that survived the structural filters
    dropped: dict[str, int] = field(default_factory=dict)
    backport_state: str = "not attempted"
    error: str = ""
    #: Ref tips at the moment of this walk. Hand these back as
    #: `WalkConfig.since_tips` next time to collect only what landed after.
    tips: tuple[str, ...] = ()
    #: How many of the requested `since_tips` still existed in the mirror. A
    #: force-push upstream can delete one, and silently excluding nothing would
    #: look identical to "nothing new landed".
    tips_used: int = 0
    incremental: bool = False

    @property
    def ok(self) -> bool:
        return not self.error


def walk(repo: Repo, cfg: WalkConfig | None = None) -> WalkResult:
    """Walk one repository's history into a commit frame."""
    cfg = cfg or WalkConfig()
    if repo.paths and not cfg.paths:
        cfg = replace(cfg, paths=repo.paths)
    if not mirror.exists(repo):
        return WalkResult(repo, schema.empty(schema.COMMIT_COLUMNS),
                          error="no mirror — run `oss-bugfixes mirror` first")

    path = mirror.mirror_path(repo)
    usable = live_tips(path, cfg.since_tips) if cfg.since_tips else ()
    if cfg.since_tips and not usable:
        # Every recorded tip is gone — a force-push, a re-clone, or a rewritten
        # history. Excluding nothing is correct here, but saying so matters:
        # silently falling back would look like "nothing new landed".
        cfg = replace(cfg, since_tips=())
    elif usable != tuple(cfg.since_tips):
        cfg = replace(cfg, since_tips=usable)

    rows: list[dict] = []
    dropped: dict[str, int] = {}
    seen = 0
    try:
        for rec in iter_commits(path, cfg):
            seen += 1
            reason = _structural_drop(rec, cfg)
            if reason:
                dropped[reason] = dropped.get(reason, 0) + 1
                continue
            rows.append(_to_row(repo, rec))
            if cfg.limit and len(rows) >= cfg.limit:
                break
    except subprocess.TimeoutExpired:
        return WalkResult(repo, schema.empty(schema.COMMIT_COLUMNS), seen=seen,
                          error=f"git log exceeded {cfg.log_timeout}s")
    except OSError as exc:
        return WalkResult(repo, schema.empty(schema.COMMIT_COLUMNS), seen=seen,
                          error=f"{type(exc).__name__}: {exc}")

    df = schema.conform(pd.DataFrame(rows), schema.COMMIT_COLUMNS)
    res = WalkResult(repo, df, seen=seen, kept=len(df), dropped=dropped,
                     tips=current_tips(path), tips_used=len(cfg.since_tips),
                     incremental=bool(cfg.since_tips))
    _attach_backports(res, path, seen, cfg)
    return res


def current_tips(path: Path) -> tuple[str, ...]:
    """Every ref's sha, right now. Recorded after a walk so the next one can
    ask git for the difference instead of re-reading the whole history."""
    r = subprocess.run(["git", "-C", str(path), "for-each-ref", "--format=%(objectname)"],
                       capture_output=True, text=True, encoding="utf-8",
                       errors="replace", timeout=120)
    if r.returncode != 0:
        return ()
    return tuple(sorted({ln.strip() for ln in r.stdout.splitlines() if len(ln.strip()) == 40}))


def live_tips(path: Path, tips: Sequence[str]) -> tuple[str, ...]:
    """The subset of `tips` the mirror still has.

    `git log --not <missing sha>` is a fatal error, so a single rewritten
    branch would otherwise abort the walk of an otherwise healthy repository.
    """
    out = []
    for t in tips:
        t = str(t).strip()
        if len(t) != 40:
            continue
        r = subprocess.run(["git", "-C", str(path), "cat-file", "-e", f"{t}^{{commit}}"],
                           capture_output=True, timeout=60)
        if r.returncode == 0:
            out.append(t)
    return tuple(sorted(set(out)))


# --- streaming git log parse ------------------------------------------------

@dataclass
class _Record:
    sha: str
    parents: str
    author_name: str
    author_email: str
    authored_at: str
    committed_at: str
    subject: str
    body: str
    files: list[str]
    insertions: int
    deletions: int


def iter_commits(path: Path, cfg: WalkConfig) -> Iterator[_Record]:
    """Yield one `_Record` per commit, streaming. Never materialises the log."""
    shape = "--numstat" if cfg.line_counts else "--name-only"
    args = ["git", "-C", str(path), "log", "--no-merges", shape,
            "--no-renames", f"--format={_FORMAT}"]
    if cfg.all_refs:
        args.insert(4, "--all")
    if cfg.since_tips:
        # Everything reachable from the previous walk's tips is already
        # collected. This is the whole of "incremental": git does the set
        # difference, so nothing is re-read and nothing is re-parsed.
        args += ["--not", *cfg.since_tips]
    if cfg.paths:
        args += ["--", *cfg.paths]
    proc = subprocess.Popen(
        args, stdout=subprocess.PIPE, stderr=subprocess.DEVNULL,
        text=True, encoding="utf-8", errors="replace", bufsize=1 << 20,
        env={**os.environ, "GIT_TERMINAL_PROMPT": "0"},
        start_new_session=True,
    )
    buf = ""
    try:
        assert proc.stdout is not None
        while True:
            chunk = proc.stdout.read(1 << 20)
            if not chunk:
                break
            buf += chunk
            # Everything up to the last record marker is complete; the tail may
            # be a half-written record, so it stays in the buffer.
            head, sep, buf = buf.rpartition(_REC)
            if not sep:
                buf = head
                continue
            for raw in head.split(_REC):
                rec = _parse(raw)
                if rec is not None:
                    yield rec
        rec = _parse(buf.lstrip(_REC))
        if rec is not None:
            yield rec
    finally:
        _kill_group(proc, grace=cfg.log_timeout if proc.poll() is None else 5)


def _parse(raw: str) -> _Record | None:
    if not raw.strip():
        return None
    meta, _, numstat = raw.partition(_END)
    # maxsplit keeps a stray separator inside the body from eating a field.
    parts = meta.split(_FLD, 7)
    if len(parts) < 8:
        return None
    sha, parents, an, ae, aat, cat, subject, body = parts
    sha = sha.strip()
    if len(sha) != 40:
        return None
    files, ins, dels = _parse_changes(numstat)
    return _Record(sha=sha, parents=parents.strip(), author_name=an,
                   author_email=ae, authored_at=aat.strip(),
                   committed_at=cat.strip(), subject=subject, body=body,
                   files=files, insertions=ins, deletions=dels)


def _parse_changes(block: str) -> tuple[list[str], int, int]:
    """Parse either shape of change list.

    `--name-only` gives one path per line. `--numstat` gives
    `adds<TAB>dels<TAB>path`, where a binary file's counts are "-" — that is
    information, not an error, so the file is counted and the lines are not.
    """
    files: list[str] = []
    ins = dels = 0
    for line in block.splitlines():
        if not line.strip():
            continue
        bits = line.split("\t")
        if len(bits) >= 3:
            a, d, path = bits[0], bits[1], bits[-1]
            if a.isdigit():
                ins += int(a)
            if d.isdigit():
                dels += int(d)
        else:
            path = bits[-1]
        path = path.strip()
        if path:
            files.append(path)
    return files, ins, dels


def _structural_drop(rec: _Record, cfg: WalkConfig) -> str:
    if not rec.files:
        return "no_files"
    if cfg.max_files and len(rec.files) > cfg.max_files:
        return "too_many_files"
    if cfg.require_source and not touches_source(rec.files):
        return "no_source_file"
    return ""


def touches_source(files: list[str]) -> bool:
    return any(Path(f).suffix.lower() in SOURCE_EXT for f in files)


def languages_of(files: list[str]) -> str:
    seen: list[str] = []
    for f in files:
        lang = LANG_BY_EXT.get(Path(f).suffix.lower())
        if lang and lang not in seen:
            seen.append(lang)
    return " ".join(sorted(seen))


def _to_row(repo: Repo, rec: _Record) -> dict:
    subject = rec.subject.strip()
    return {
        "domain": repo.domain,
        "repo_slug": repo.slug,
        "repo": repo.repo,
        "sha": rec.sha,
        "parents": rec.parents,
        "author_name": rec.author_name.strip(),
        "author_email": rec.author_email.strip(),
        "authored_at": rec.authored_at,
        "committed_at": rec.committed_at,
        "subject": subject[:1000],
        "body": rec.body.strip()[:8000],
        "files_changed": json.dumps(rec.files, ensure_ascii=False),
        "n_files": len(rec.files),
        "insertions": rec.insertions,
        "deletions": rec.deletions,
        "touches_source": touches_source(rec.files),
        "is_revert": subject.lower().startswith(_REVERT_PREFIXES)
                     or "this reverts commit" in rec.body.lower(),
        "languages": languages_of(rec.files),
    }


# --- patch-id backport detection -------------------------------------------

def _attach_backports(res: WalkResult, path: Path, seen: int, cfg: WalkConfig) -> None:
    """Additive metadata. A failure here costs the column and nothing else —
    an optional column is never worth losing a repository over."""
    df = res.frame
    df["is_backport"] = pd.Series([pd.NA] * len(df), dtype="boolean")
    df["n_patch_siblings"] = pd.Series([pd.NA] * len(df), dtype="Int64")
    if not len(df) or not cfg.detect_backports:
        res.backport_state = "not attempted"
        return
    if seen > cfg.patch_id_max_commits:
        res.backport_state = (f"not attempted ({seen} commits > "
                              f"{cfg.patch_id_max_commits})")
        return
    if mirror.is_blobless(path):
        # Rendering every diff out of a blobless mirror fetches every blob one
        # at a time. Saying so is worth more than spending the timeout to fail.
        res.backport_state = "not attempted (blobless mirror; run `mirror --hydrate`)"
        return
    try:
        out = _patch_ids(path, cfg.patch_id_timeout)
    except (subprocess.TimeoutExpired, OSError) as exc:
        res.backport_state = f"skipped ({type(exc).__name__})"
        return

    by_patch: dict[str, list[str]] = {}
    for line in out.splitlines():
        bits = line.split()
        if len(bits) == 2:
            by_patch.setdefault(bits[0], []).append(bits[1])
    siblings = {sha: len(shas) for shas in by_patch.values() for sha in shas}
    counts = df["sha"].map(lambda s: siblings.get(s))
    df["n_patch_siblings"] = counts.astype("Int64")
    df["is_backport"] = counts.map(
        lambda n: pd.NA if n is None else bool(n > 1)).astype("boolean")
    res.backport_state = f"{int(df['is_backport'].fillna(False).sum())} backported"


def _patch_ids(path: Path, timeout: float) -> str:
    """`git log -p | git patch-id`, killed as a GROUP when it overruns.

    `subprocess.run(shell=True, timeout=…)` terminates the shell and returns,
    but the pipeline's children survive it: an orphaned `git patch-id` was once
    still burning a core half an hour past its deadline, invisible to the run
    that had moved on. Own the process group so the timeout stops the work it
    claims to stop.
    """
    proc = subprocess.Popen(
        ["sh", "-c",
         f'git -C "{path}" log --all --no-merges --format=%H -p | git patch-id --stable'],
        stdout=subprocess.PIPE, stderr=subprocess.DEVNULL,
        text=True, encoding="utf-8", errors="replace", start_new_session=True)
    try:
        out, _ = proc.communicate(timeout=timeout)
        return out or ""
    except subprocess.TimeoutExpired:
        _kill_group(proc, grace=30)
        raise


def _kill_group(proc: subprocess.Popen, grace: float) -> None:
    if proc.poll() is not None:
        return
    try:
        os.killpg(os.getpgid(proc.pid), signal.SIGKILL)
    except (ProcessLookupError, PermissionError, OSError):
        try:
            proc.kill()
        except OSError:
            pass
    try:
        proc.wait(timeout=grace)
    except subprocess.TimeoutExpired:
        print(f"[walk] warning: child {proc.pid} would not die", file=sys.stderr)
