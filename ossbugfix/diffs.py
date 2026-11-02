"""diffs.py — serve a commit's diff out of the local mirror.

The classifier needs the patch, not just the message. Fetching those from the
forge's REST API is what puts a rate limit on the whole project; reading them
out of a bare clone does not, and is two orders of magnitude faster.

Blobless mirrors (`--filter=blob:none`) hold no file contents until something
asks for them. `git show` on such a clone fetches the blobs it needs over git
transport on first access — still no REST limit, but it IS a network round trip,
so diffs are cached on disk, gzipped, content-addressed by sha.

Diffs are capped. A classifier prompt cannot hold a 40k-line patch, and a patch
that large is not one defect anyway; `truncate()` keeps the head of each file's
hunk set and says plainly how much it dropped, because a silently truncated
diff makes the model confidently wrong about code it never saw.
"""
from __future__ import annotations

import gzip
import os
import subprocess
from dataclasses import dataclass
from pathlib import Path

from . import mirror, paths
from .registry import Repo

__all__ = ["DiffCache", "get_diff", "truncate", "DEFAULT_CHAR_CAP"]

#: Characters, not tokens — the cap exists so a prompt builder has a bound it
#: can reason about without a tokenizer. ~6k chars is the budget the wallet
#: classifier settled on per commit.
DEFAULT_CHAR_CAP = 6_000
DIFF_TIMEOUT = int(os.environ.get("OSS_BUGFIXES_DIFF_TIMEOUT", "120"))


@dataclass
class DiffCache:
    """Gzipped, content-addressed diff store under `work/<domain>/diffs/`.

    Sharded by the first two sha characters so a domain with a million commits
    does not put a million entries in one directory.
    """

    domain: str
    root: Path | None = None

    def __post_init__(self) -> None:
        self.root = Path(self.root) if self.root else paths.diff_cache_dir(self.domain)

    def path_for(self, repo_slug: str, sha: str) -> Path:
        return self.root / repo_slug / sha[:2] / f"{sha}.diff.gz"

    def get(self, repo_slug: str, sha: str) -> str | None:
        p = self.path_for(repo_slug, sha)
        if not p.exists():
            return None
        try:
            with gzip.open(p, "rt", encoding="utf-8", errors="replace") as fh:
                return fh.read()
        except (OSError, EOFError, gzip.BadGzipFile):
            # A truncated cache entry from a killed run must not be served as
            # if it were the real diff.
            p.unlink(missing_ok=True)
            return None

    def put(self, repo_slug: str, sha: str, diff: str) -> Path:
        p = self.path_for(repo_slug, sha)
        p.parent.mkdir(parents=True, exist_ok=True)
        tmp = p.with_suffix(".tmp")
        with gzip.open(tmp, "wt", encoding="utf-8") as fh:
            fh.write(diff)
        os.replace(tmp, p)
        return p

    def has(self, repo_slug: str, sha: str) -> bool:
        return self.path_for(repo_slug, sha).exists()

    def size_bytes(self) -> int:
        if not self.root.is_dir():
            return 0
        return sum(f.stat().st_size for f in self.root.rglob("*.diff.gz"))


def get_diff(repo: Repo, sha: str, *, cache: DiffCache | None = None,
             context: int = 3, cap: int | None = None) -> str | None:
    """The unified diff for one commit, or None if it cannot be produced.

    None is a real answer: a blobless mirror whose upstream has since deleted
    the branch cannot produce the blobs, and the caller should record that
    rather than treating an empty string as an empty diff.
    """
    cache = cache or DiffCache(repo.domain)
    hit = cache.get(repo.slug, sha)
    if hit is not None:
        return truncate(hit, cap) if cap else hit

    path = mirror.mirror_path(repo)
    if not (path / "HEAD").exists():
        return None
    text = _git_show(path, sha, context)
    if text is None:
        return None
    cache.put(repo.slug, sha, text)
    return truncate(text, cap) if cap else text


def _git_show(path: Path, sha: str, context: int) -> str | None:
    args = ["git", "-C", str(path), "show", "--no-color", "--no-renames",
            f"--unified={context}", "--format=", sha]
    try:
        r = subprocess.run(args, capture_output=True, text=True,
                           encoding="utf-8", errors="replace",
                           timeout=DIFF_TIMEOUT,
                           env={**os.environ, "GIT_TERMINAL_PROMPT": "0"})
    except (subprocess.TimeoutExpired, OSError):
        return None
    if r.returncode != 0:
        return None
    return r.stdout


def truncate(diff: str, cap: int | None = DEFAULT_CHAR_CAP) -> str:
    """Cut a diff to `cap` characters at a line boundary, and say so.

    The marker is not decoration. A model handed a silently truncated patch
    will reason about the part it was shown as if it were the whole change.
    """
    if cap is None or len(diff) <= cap:
        return diff
    head = diff[:cap]
    cut = head.rfind("\n")
    if cut > cap // 2:
        head = head[:cut]
    dropped = len(diff) - len(head)
    return f"{head}\n… [diff truncated: {dropped:,} of {len(diff):,} characters not shown]"
