"""audit.py — does the registry still describe reality?

A registry is this project's coverage claim, and claims rot. Repositories get
renamed, transferred between organisations, archived, or deleted, and none of
that announces itself: `git clone` follows a redirect silently, so a mirror
can be of `new-org/thing` while the registry still says `old-org/thing` and
every report attributes the findings to a name that no longer exists.

A hand audit of the 40-entry smartcontract registry found one repository that
had been deleted outright and four that had moved — `makerdao/dss` to
`sky-ecosystem/dss`, `safe-global/safe-smart-account` to `safe-fndn/…`,
`marsfoundation/spark-psm` to `sparkdotfi/…`, `coral-xyz/anchor` to
`otter-sec/anchor`. One in eight entries was wrong. That is why this is a
command rather than something somebody remembers to do.

    oss-bugfixes audit --domain smartcontract
    oss-bugfixes audit                     # every domain
    oss-bugfixes audit --domain wallet --fix   # rewrite moved names in place
"""
from __future__ import annotations

import json
import subprocess
from dataclasses import dataclass
from typing import Iterable, Literal

from .registry import Repo

__all__ = ["Finding", "audit_repo", "audit"]

Status = Literal["ok", "moved", "archived", "moved+archived", "missing", "unknown"]


@dataclass(frozen=True)
class Finding:
    repo: Repo
    status: Status
    canonical: str = ""
    pushed_at: str = ""
    detail: str = ""

    @property
    def needs_attention(self) -> bool:
        return self.status not in ("ok", "unknown")

    def line(self) -> str:
        if self.status == "ok":
            return f"  ok       {self.repo.slug:<28} {self.repo.repo}"
        if self.status == "missing":
            return f"  MISSING  {self.repo.slug:<28} {self.repo.repo}  — gone from the forge"
        if self.status == "unknown":
            return f"  ?        {self.repo.slug:<28} {self.repo.repo}  — {self.detail}"
        bits = []
        if "moved" in self.status:
            bits.append(f"now {self.canonical}")
        if "archived" in self.status:
            bits.append("archived upstream")
        return (f"  {self.status.upper():<8} {self.repo.slug:<28} {self.repo.repo}"
                f"  — {', '.join(bits)}")


def _gh(path: str) -> dict | None:
    """One forge lookup. Returns None when the repository is gone, and raises
    nothing — an audit that dies on the first odd answer audits nothing."""
    try:
        r = subprocess.run(["gh", "api", path], capture_output=True, text=True,
                           timeout=60)
    except (OSError, subprocess.TimeoutExpired):
        return None
    if r.returncode != 0:
        return None
    try:
        out = json.loads(r.stdout)
    except json.JSONDecodeError:
        return None
    return out if isinstance(out, dict) and "full_name" in out else None


def audit_repo(repo: Repo) -> Finding:
    if repo.forge != "github.com":
        return Finding(repo, "unknown", detail=f"no auditor for {repo.forge}")
    data = _gh(f"repos/{repo.repo}")
    if data is None:
        return Finding(repo, "missing")

    canonical = str(data.get("full_name") or "")
    archived_now = bool(data.get("archived"))
    pushed = str(data.get("pushed_at") or "")[:10]

    moved = canonical.lower() != repo.repo.lower()
    # Only report archival as news when the registry does not already say so.
    newly_archived = archived_now and not repo.archived

    if moved and newly_archived:
        status: Status = "moved+archived"
    elif moved:
        status = "moved"
    elif newly_archived:
        status = "archived"
    else:
        status = "ok"
    return Finding(repo, status, canonical=canonical, pushed_at=pushed)


def audit(repos: Iterable[Repo], workers: int = 8) -> list[Finding]:
    from concurrent.futures import ThreadPoolExecutor
    items = list(repos)
    if workers <= 1:
        return [audit_repo(r) for r in items]
    with ThreadPoolExecutor(max_workers=workers) as pool:
        return list(pool.map(audit_repo, items))


def apply_fixes(domain: str, findings: list[Finding]) -> int:
    """Rewrite moved names in the registry file, in place, textually.

    Textual rather than a YAML round trip on purpose: the registries carry
    comments that explain each domain's scope rule, and a round trip would
    discard every one of them.
    """
    from . import paths

    path = paths.registry_path(domain)
    text = path.read_text(encoding="utf-8")
    changed = 0
    for f in findings:
        if "moved" not in f.status or not f.canonical:
            continue
        old = f"repo: {f.repo.repo}"
        new = f"repo: {f.canonical}"
        if old in text:
            text = text.replace(old, new)
            changed += 1
    if changed:
        path.write_text(text, encoding="utf-8")
    return changed
