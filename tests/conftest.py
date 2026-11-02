"""Fixtures. Everything here is offline: a real git repository is built in a
tmpdir, so the walk, diff and sink stages are exercised against actual git
behaviour rather than a mock that agrees with whatever the code does."""
from __future__ import annotations

import os
import subprocess
from pathlib import Path

import pytest


def _git(repo: Path, *args: str) -> str:
    env = {**os.environ,
           "GIT_AUTHOR_NAME": "Test", "GIT_AUTHOR_EMAIL": "t@example.com",
           "GIT_COMMITTER_NAME": "Test", "GIT_COMMITTER_EMAIL": "t@example.com",
           "GIT_AUTHOR_DATE": "2024-01-01T00:00:00+00:00",
           "GIT_COMMITTER_DATE": "2024-01-01T00:00:00+00:00"}
    r = subprocess.run(["git", "-C", str(repo), *args], capture_output=True,
                       text=True, env=env, check=True)
    return r.stdout


@pytest.fixture
def source_repo(tmp_path: Path) -> Path:
    """A small repository with one of each commit shape the walk cares about."""
    repo = tmp_path / "src"
    repo.mkdir()
    _git(repo, "init", "-q", "-b", "main")

    (repo / "lib.c").write_text("int f(int *p){ return *p; }\n")
    _git(repo, "add", "-A")
    _git(repo, "commit", "-qm", "initial commit")

    (repo / "lib.c").write_text("int f(int *p){ if(!p) return 0; return *p; }\n")
    _git(repo, "add", "-A")
    _git(repo, "commit", "-qm",
         "fix null pointer dereference in f\n\nAn attacker could crash the parser. CVE-2024-12345.")

    (repo / "README.md").write_text("# docs only\n")
    _git(repo, "add", "-A")
    _git(repo, "commit", "-qm", "docs: describe f")

    (repo / "lib.c").write_text("int f(int *p){ if (!p) return 0; return *p; }\n")
    _git(repo, "add", "-A")
    _git(repo, "commit", "-qm", "style: reformat")
    return repo


@pytest.fixture
def mirrored(tmp_path: Path, source_repo: Path, monkeypatch):
    """A registry + mirror wired to `source_repo`, with the work tree in tmp."""
    root = tmp_path / "root"
    (root / "domains" / "testdom").mkdir(parents=True)
    (root / "domains" / "testdom" / "repos.yml").write_text(
        "domain: testdom\n"
        "description: fixture\n"
        "repos:\n"
        "  sample:\n"
        "    repo: example/sample\n"
        "    tier: 1\n"
        "    language: c\n", encoding="utf-8")
    monkeypatch.setenv("OSS_BUGFIXES_ROOT", str(root))
    monkeypatch.setenv("OSS_BUGFIXES_WORK", str(tmp_path / "work"))

    from ossbugfix import mirror, registry
    reg = registry.load("testdom")
    repo = reg.get("sample")
    dest = mirror.mirror_path(repo)
    dest.parent.mkdir(parents=True, exist_ok=True)
    subprocess.run(["git", "clone", "--bare", "-q", str(source_repo), str(dest)],
                   check=True, capture_output=True)
    return repo
