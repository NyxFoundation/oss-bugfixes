"""mirror.py — one bare, blobless clone per repository.

Why git transport and not the REST API: the forge's REST API is capped at
5,000 requests/hour and will serve secondary-rate-limit 403s long before that,
which is the single thing that makes a whole-history crawl of a few hundred
repositories impractical. `git clone`/`git fetch` are not subject to either.
A diff that costs ~0.3-1s over REST costs ~1-5ms out of a local clone.

`--filter=blob:none` gives the full commit and tree graph immediately while
leaving file contents on the server, fetched lazily the first time a diff is
rendered. That is what makes mirroring a 180-repo domain finish in minutes
rather than filling a disk: the graph is what the walk stage needs, and most
commits are never diffed.

Nothing here is destructive. `ensure()` creates a mirror if it is missing and
otherwise leaves it alone; refreshing is an explicit `--refresh`.
"""
from __future__ import annotations

import os
import shutil
import subprocess
import time
from dataclasses import dataclass
from pathlib import Path

from . import paths
from .registry import Repo

__all__ = ["MirrorError", "MirrorResult", "mirror_path", "ensure", "refresh",
           "exists", "hydrate", "is_blobless"]

CLONE_TIMEOUT = int(os.environ.get("OSS_BUGFIXES_CLONE_TIMEOUT", "1800"))
FETCH_TIMEOUT = int(os.environ.get("OSS_BUGFIXES_FETCH_TIMEOUT", "900"))


class MirrorError(RuntimeError):
    pass


@dataclass(frozen=True)
class MirrorResult:
    repo: Repo
    path: Path
    action: str          # "cloned" | "fetched" | "cached" | "failed"
    seconds: float
    error: str = ""

    @property
    def ok(self) -> bool:
        return self.action != "failed"


def mirror_path(repo: Repo) -> Path:
    return paths.mirror_dir(repo.domain) / f"{repo.slug}.git"


def exists(repo: Repo) -> bool:
    p = mirror_path(repo)
    return (p / "HEAD").exists()


def _git(args: list[str], cwd: Path | None = None, timeout: int = 600) -> subprocess.CompletedProcess:
    return subprocess.run(
        ["git"] + (["-C", str(cwd)] if cwd else []) + args,
        capture_output=True, text=True, encoding="utf-8", errors="replace",
        timeout=timeout,
        # git prompting for credentials in a 200-repo sweep hangs the sweep.
        env={**os.environ, "GIT_TERMINAL_PROMPT": "0", "GIT_ASKPASS": "echo"},
    )


def ensure(repo: Repo, *, blobless: bool = True, refresh_if_present: bool = False) -> MirrorResult:
    """Make sure a usable mirror exists. Returns what it had to do to get one."""
    p = mirror_path(repo)
    t0 = time.monotonic()

    if exists(repo):
        if not refresh_if_present:
            return MirrorResult(repo, p, "cached", time.monotonic() - t0)
        return refresh(repo)

    # A half-written clone from a killed run is worse than no clone: git will
    # refuse to clone into it and every later stage reads an empty graph.
    if p.exists():
        shutil.rmtree(p, ignore_errors=True)
    p.parent.mkdir(parents=True, exist_ok=True)

    args = ["clone", "--bare", "--no-tags"]
    if blobless:
        args += ["--filter=blob:none"]
    args += [repo.clone_url, str(p)]
    try:
        r = _git(args, timeout=CLONE_TIMEOUT)
    except subprocess.TimeoutExpired:
        shutil.rmtree(p, ignore_errors=True)
        return MirrorResult(repo, p, "failed", time.monotonic() - t0,
                            f"clone timed out after {CLONE_TIMEOUT}s")
    if r.returncode != 0:
        shutil.rmtree(p, ignore_errors=True)
        return MirrorResult(repo, p, "failed", time.monotonic() - t0,
                            _tail(r.stderr) or f"git clone exited {r.returncode}")

    # --no-tags keeps the clone small, but the walk wants every branch; a bare
    # clone's default refspec only tracks the default branch for later fetches.
    _git(["config", "remote.origin.fetch", "+refs/heads/*:refs/heads/*"], cwd=p)
    return MirrorResult(repo, p, "cloned", time.monotonic() - t0)


def refresh(repo: Repo) -> MirrorResult:
    """Fetch new commits into an existing mirror. A failure here is not fatal —
    the mirror still holds everything it held before, so the walk can proceed
    on slightly stale history rather than losing the repo."""
    p = mirror_path(repo)
    t0 = time.monotonic()
    if not exists(repo):
        return ensure(repo)
    try:
        r = _git(["fetch", "--prune", "--force", "origin",
                  "+refs/heads/*:refs/heads/*"], cwd=p, timeout=FETCH_TIMEOUT)
    except subprocess.TimeoutExpired:
        return MirrorResult(repo, p, "failed", time.monotonic() - t0,
                            f"fetch timed out after {FETCH_TIMEOUT}s (mirror left intact)")
    if r.returncode != 0:
        return MirrorResult(repo, p, "failed", time.monotonic() - t0,
                            _tail(r.stderr) or f"git fetch exited {r.returncode}")
    return MirrorResult(repo, p, "fetched", time.monotonic() - t0)


def is_blobless(path: Path) -> bool:
    """Whether this mirror still leaves file contents on the server."""
    r = _git(["config", "--get", "remote.origin.partialclonefilter"], cwd=path)
    return r.returncode == 0 and bool(r.stdout.strip())


def hydrate(repo: Repo) -> MirrorResult:
    """Download every blob in ONE fetch, turning a blobless mirror into a full one.

    This is the difference between a diff-heavy stage finishing and not. A
    blobless clone fetches each missing blob on first access — one network
    round trip per object — so rendering a whole history's diffs costs hundreds
    of serial round trips. Dropping the partial-clone filter and refetching
    moves the same bytes in a single negotiation.

    Measured on P-H-C/phc-winner-argon2 (496 commits), fresh clone each time:

        blobless, diff the whole history lazily   214.7 s
        blobless + hydrate, then diff              0.9 s + 2.0 s

    — a hundredfold, bought with 0.9 s and 6 MB of disk. It does cost disk, so
    hydrate the repositories you will actually diff, not the whole registry.
    """
    p = mirror_path(repo)
    t0 = time.monotonic()
    if not exists(repo):
        res = ensure(repo)
        if not res.ok:
            return res
    if not is_blobless(p):
        return MirrorResult(repo, p, "cached", time.monotonic() - t0)
    _git(["config", "--unset-all", "remote.origin.partialclonefilter"], cwd=p)
    try:
        r = _git(["fetch", "--refetch", "origin"], cwd=p, timeout=CLONE_TIMEOUT)
    except subprocess.TimeoutExpired:
        return MirrorResult(repo, p, "failed", time.monotonic() - t0,
                            f"hydrate timed out after {CLONE_TIMEOUT}s")
    if r.returncode != 0:
        return MirrorResult(repo, p, "failed", time.monotonic() - t0,
                            _tail(r.stderr) or f"git fetch --refetch exited {r.returncode}")
    # The promisor flag is what makes git keep asking the remote for objects it
    # now has locally; leaving it set makes a full mirror behave like a partial one.
    _git(["config", "--unset-all", "remote.origin.promisor"], cwd=p)
    return MirrorResult(repo, p, "hydrated", time.monotonic() - t0)


def default_ref(repo: Repo) -> str:
    """Whatever the mirror considers HEAD, falling back to a branch that exists.
    Used by the diff stage for three-dot PR diffs; the walk never needs it
    because it walks `--all`."""
    if repo.branch:
        return repo.branch
    p = mirror_path(repo)
    r = _git(["symbolic-ref", "--short", "HEAD"], cwd=p)
    if r.returncode == 0 and r.stdout.strip():
        return r.stdout.strip()
    for cand in ("main", "master", "develop"):
        if _git(["rev-parse", "--verify", "--quiet", cand], cwd=p).returncode == 0:
            return cand
    r = _git(["for-each-ref", "--count=1", "--format=%(refname:short)", "refs/heads/"], cwd=p)
    return r.stdout.strip() or "HEAD"


def disk_usage(domain: str) -> int:
    root = paths.mirror_dir(domain)
    if not root.is_dir():
        return 0
    return sum(f.stat().st_size for f in root.rglob("*") if f.is_file())


def _tail(text: str, n: int = 3) -> str:
    lines = [ln for ln in (text or "").strip().splitlines() if ln.strip()]
    return " / ".join(lines[-n:])
