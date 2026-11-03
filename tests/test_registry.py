from __future__ import annotations

import pytest

from ossbugfix import paths, registry


def write(tmp_path, body: str, domain: str = "d"):
    d = tmp_path / "domains" / domain
    d.mkdir(parents=True, exist_ok=True)
    (d / "repos.yml").write_text(body, encoding="utf-8")
    return d / "repos.yml"


def test_shorthand_entry_and_defaults(tmp_path, monkeypatch):
    p = write(tmp_path, "domain: d\nrepos:\n  a: owner/a\n")
    monkeypatch.setenv("OSS_BUGFIXES_ROOT", str(tmp_path))
    reg = registry.load("d", p)
    a = reg.get("a")
    assert a.repo == "owner/a"
    assert a.forge == "github.com"
    assert a.tier == 2                      # the documented default
    assert a.clone_url == "https://github.com/owner/a.git"


def test_tiers_nest(tmp_path):
    p = write(tmp_path, "domain: d\nrepos:\n"
                        "  a: {repo: o/a, tier: 1}\n"
                        "  b: {repo: o/b, tier: 2}\n"
                        "  c: {repo: o/c, tier: 3}\n")
    reg = registry.load("d", p)
    assert [r.slug for r in reg.select(tier=2)] == ["a", "b"]
    assert len(reg.select(tier=3)) == 3


def test_duplicate_repo_is_rejected(tmp_path):
    p = write(tmp_path, "domain: d\nrepos:\n  a: o/x\n  b: O/X\n")
    with pytest.raises(registry.RegistryError, match="one repo, one slug"):
        registry.load("d", p)


def test_domain_mismatch_is_rejected(tmp_path):
    p = write(tmp_path, "domain: other\nrepos:\n  a: o/a\n")
    with pytest.raises(registry.RegistryError, match="declares domain"):
        registry.load("d", p)


def test_bad_repo_shape_is_rejected(tmp_path):
    p = write(tmp_path, "domain: d\nrepos:\n  a: not-a-slug\n")
    with pytest.raises(registry.RegistryError, match="owner/name"):
        registry.load("d", p)


def test_unknown_keys_are_preserved(tmp_path):
    p = write(tmp_path, "domain: d\nrepos:\n  a: {repo: o/a, custody: hw}\n")
    reg = registry.load("d", p)
    assert reg.get("a").extra["custody"] == "hw"


def test_shipped_registries_all_parse():
    found = [d for d in paths.list_domains() if paths.registry_path(d).exists()]
    assert found, "the repository ships no registries"
    for domain in found:
        reg = registry.load(domain)
        assert len(reg) > 0
        assert reg.description, f"{domain}: registry has no description"


def test_registries_carry_no_obviously_dead_entries():
    """A registry IS the coverage claim, so a slug nobody can clone is a false
    claim. This cannot reach the network in CI, so it checks the shape only —
    `oss-bugfixes audit` is what checks reality, and the first audit of the
    40-entry smartcontract registry found one deleted repo and four that had
    moved organisation."""
    for domain in paths.list_domains():
        if not paths.registry_path(domain).exists():
            continue
        for repo in registry.load(domain):
            assert "/" in repo.repo, f"{domain}/{repo.slug}: not owner/name"
            assert not repo.repo.endswith(".git"), f"{domain}/{repo.slug}"
            assert " " not in repo.repo, f"{domain}/{repo.slug}"


def test_smartcontract_covers_more_than_solidity():
    """`smart contract` is not one language. A corpus that is only Solidity
    cannot say anything about account-validation bugs on Solana or capability
    bugs in Move."""
    reg = registry.load("smartcontract")
    ecosystems = {r.extra.get("ecosystem") for r in reg}
    assert {"evm", "solana", "sui", "aptos"} <= ecosystems
    langs = {r.language for r in reg}
    assert {"solidity", "rust", "move"} <= langs
    for eco in ("solana", "sui", "aptos"):
        n = sum(1 for r in reg if r.extra.get("ecosystem") == eco)
        assert n >= 5, f"{eco}: only {n} repos — too few to say anything"
