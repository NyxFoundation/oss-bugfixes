"""registry.py — `domains/<domain>/repos.yml` is the only place a repo is named.

No crawler, no script and no notebook in this repository hard-codes a slug. That
rule is what lets a domain be added without touching the framework, and it is
what makes coverage claims checkable: the registry *is* the claim.

Registry shape (YAML or JSON, same keys)::

    domain: wallet
    description: >
      Repos where a defect can cost a user their funds, keys or signing authority.
    defaults:
      forge: github.com
      branch: null          # null = whatever the mirror's HEAD points at
    repos:
      metamask:
        repo: MetaMask/metamask-extension
        category: browser_extension
        tier: 1
        language: typescript
        tags: [evm, self-custody]
      ...

Required per entry: `repo`. Everything else is optional and free-form, except
`tier` (1 = mass-market, 2 = significant, 3 = niche), `archived`, and `paths`,
which the framework acts on. Unknown keys are preserved, not rejected — a
domain is allowed to carry its own metadata (a wallet's custody model, a
client's consensus layer) without the framework learning about it.

`paths` narrows a repository to the part of it that is in scope. Several of
the most important projects live in monorepos whose history is mostly
something else: of `smartcontractkit/chainlink`'s 45,093 commits only 3,454
touch `contracts/`, and the other 41,639 are a Go node that belongs in
domains/blockchain by this domain's own scope rule. Without `paths` the choice
is to mis-file the repository or to classify forty thousand commits that
cannot contain the defect being looked for.
"""
from __future__ import annotations

import json
import re
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Iterable, Iterator

from . import paths

__all__ = ["Repo", "Registry", "load", "RegistryError"]

SLUG_RE = re.compile(r"^[a-z0-9][a-z0-9._-]*$")
REPO_RE = re.compile(r"^[A-Za-z0-9._-]+/[A-Za-z0-9._-]+$")


class RegistryError(ValueError):
    """The registry file is unusable. Always names the file and the entry."""


@dataclass(frozen=True)
class Repo:
    slug: str                      # registry key; stable across upstream renames
    repo: str                      # owner/name
    domain: str
    forge: str = "github.com"
    branch: str | None = None      # None = mirror HEAD
    tier: int = 2
    archived: bool = False
    category: str = ""
    language: str = ""
    tags: tuple[str, ...] = ()
    #: Subtrees that are in scope. Empty = the whole repository. Matched by
    #: git's own pathspec, so a trailing slash means "this directory".
    paths: tuple[str, ...] = ()
    extra: dict[str, Any] = field(default_factory=dict, compare=False)

    @property
    def clone_url(self) -> str:
        return f"https://{self.forge}/{self.repo}.git"

    @property
    def owner(self) -> str:
        return self.repo.split("/", 1)[0]

    @property
    def name(self) -> str:
        return self.repo.split("/", 1)[1]

    def commit_url(self, sha: str) -> str:
        return f"https://{self.forge}/{self.repo}/commit/{sha}"


@dataclass(frozen=True)
class Registry:
    domain: str
    description: str
    repos: tuple[Repo, ...]
    path: Path

    def __iter__(self) -> Iterator[Repo]:
        return iter(self.repos)

    def __len__(self) -> int:
        return len(self.repos)

    def get(self, slug: str) -> Repo:
        for r in self.repos:
            if r.slug == slug:
                return r
        raise KeyError(
            f"{self.path}: no repo {slug!r}; known slugs: {', '.join(r.slug for r in self.repos[:12])}…")

    def select(
        self,
        *,
        slugs: Iterable[str] | None = None,
        tier: int | None = None,
        category: str | None = None,
        language: str | None = None,
        tag: str | None = None,
        include_archived: bool = True,
    ) -> list[Repo]:
        """Filter the registry. `tier=2` means "tier 1 and 2" — tiers nest, so
        scoping a crawl down is a single number rather than a list."""
        if slugs is not None:
            wanted = list(slugs)
            return [self.get(s) for s in wanted]
        out = list(self.repos)
        if tier is not None:
            out = [r for r in out if r.tier <= tier]
        if category:
            out = [r for r in out if r.category == category]
        if language:
            out = [r for r in out if r.language == language]
        if tag:
            out = [r for r in out if tag in r.tags]
        if not include_archived:
            out = [r for r in out if not r.archived]
        return out


def load(domain: str, path: Path | None = None) -> Registry:
    p = Path(path) if path is not None else paths.registry_path(domain)
    if not p.exists():
        raise RegistryError(f"no registry for domain {domain!r} (looked for {p})")
    raw = _read(p)
    if not isinstance(raw, dict):
        raise RegistryError(f"{p}: top level must be a mapping, got {type(raw).__name__}")

    declared = str(raw.get("domain") or domain)
    if declared != domain:
        raise RegistryError(
            f"{p}: declares domain {declared!r} but lives in domains/{domain}/ — rename one of them")

    defaults = raw.get("defaults") or {}
    if not isinstance(defaults, dict):
        raise RegistryError(f"{p}: `defaults` must be a mapping")

    entries = raw.get("repos")
    if not isinstance(entries, dict):
        raise RegistryError(f"{p}: `repos` must be a mapping of slug -> entry")

    repos: list[Repo] = []
    seen_repo: dict[str, str] = {}
    for slug, entry in entries.items():
        repos.append(_parse_entry(p, domain, str(slug), entry, defaults, seen_repo))

    return Registry(domain=domain,
                    description=str(raw.get("description") or "").strip(),
                    repos=tuple(repos),
                    path=p)


def _read(p: Path) -> Any:
    text = p.read_text(encoding="utf-8")
    if p.suffix == ".json":
        return json.loads(text)
    import yaml
    return yaml.safe_load(text)


_KNOWN = {"repo", "forge", "branch", "tier", "archived", "category", "language",
          "tags", "paths"}


def _parse_entry(p: Path, domain: str, slug: str, entry: Any,
                 defaults: dict, seen_repo: dict[str, str]) -> Repo:
    where = f"{p}: repos.{slug}"
    if not SLUG_RE.match(slug):
        raise RegistryError(f"{where}: slug must match {SLUG_RE.pattern}")
    if isinstance(entry, str):            # shorthand: `slug: owner/name`
        entry = {"repo": entry}
    if not isinstance(entry, dict):
        raise RegistryError(f"{where}: entry must be a string or a mapping")

    merged = {**defaults, **entry}
    repo = str(merged.get("repo") or "").strip()
    if not REPO_RE.match(repo):
        raise RegistryError(f"{where}: `repo` must look like owner/name, got {repo!r}")
    if repo.lower() in seen_repo:
        raise RegistryError(
            f"{where}: {repo} already listed as {seen_repo[repo.lower()]!r} — one repo, one slug")
    seen_repo[repo.lower()] = slug

    tier = merged.get("tier", 2)
    try:
        tier = int(tier)
    except (TypeError, ValueError):
        raise RegistryError(f"{where}: `tier` must be an integer, got {tier!r}") from None
    if tier not in (1, 2, 3):
        raise RegistryError(f"{where}: `tier` must be 1, 2 or 3, got {tier}")

    tags = merged.get("tags") or []
    if isinstance(tags, str):
        tags = [tags]
    if not all(isinstance(t, str) for t in tags):
        raise RegistryError(f"{where}: `tags` must be a list of strings")

    scope = merged.get("paths") or []
    if isinstance(scope, str):
        scope = [scope]
    if not all(isinstance(t, str) and t.strip() for t in scope):
        raise RegistryError(f"{where}: `paths` must be a list of non-empty strings")
    for t in scope:
        if t.startswith("/") or ".." in t:
            raise RegistryError(
                f"{where}: `paths` entries are repo-relative, got {t!r}")

    branch = merged.get("branch")
    return Repo(
        slug=slug,
        repo=repo,
        domain=domain,
        forge=str(merged.get("forge") or "github.com"),
        branch=str(branch) if branch else None,
        tier=tier,
        archived=bool(merged.get("archived", False)),
        category=str(merged.get("category") or ""),
        language=str(merged.get("language") or ""),
        tags=tuple(tags),
        paths=tuple(scope),
        extra={k: v for k, v in merged.items() if k not in _KNOWN},
    )


def load_all() -> dict[str, Registry]:
    out: dict[str, Registry] = {}
    for d in paths.list_domains():
        if paths.registry_path(d).exists():
            out[d] = load(d)
    return out
