"""cli.py — `oss-bugfixes <stage> --domain <domain>`.

Each stage is a separate subcommand on purpose. A whole-history sweep is a
days-long piece of work with very different failure modes per stage (network
for mirror, CPU and disk for walk, money for classify), and being able to
re-run exactly one of them is what makes it operable. `run` chains them for
the case where you just want the thing to go.

    oss-bugfixes validate                       # every registry parses
    oss-bugfixes repos   --domain wallet --tier 1
    oss-bugfixes mirror  --domain wallet --tier 1 --workers 8
    oss-bugfixes walk    --domain wallet --tier 1
    oss-bugfixes signals --domain wallet
    oss-bugfixes diffs   --domain wallet --min-signals 1
    oss-bugfixes classify --domain wallet --engine heuristic
    oss-bugfixes status  --domain wallet
"""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

from . import __version__, classify as classify_mod
from . import manifest, mirror, paths, pipeline, registry, schema, walk
from .sink import Sink
from .state import Stage, StageState

__all__ = ["main", "build_parser"]


def build_parser() -> argparse.ArgumentParser:
    ap = argparse.ArgumentParser(
        prog="oss-bugfixes", description=__doc__,
        formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--version", action="version", version=f"oss-bugfixes {__version__}")
    sub = ap.add_subparsers(dest="cmd", required=True)

    def selection(p: argparse.ArgumentParser, *, needs_domain: bool = True) -> None:
        p.add_argument("--domain", required=needs_domain,
                       help="domain under domains/ (e.g. wallet)")
        p.add_argument("--repo", action="append", dest="slugs", metavar="SLUG",
                       help="registry slug; repeatable. Overrides every filter")
        p.add_argument("--tier", type=int, choices=(1, 2, 3),
                       help="include tiers up to this one (tiers nest)")
        p.add_argument("--category")
        p.add_argument("--language")
        p.add_argument("--tag")
        p.add_argument("--skip-archived", action="store_true")

    def runner(p: argparse.ArgumentParser) -> None:
        p.add_argument("--workers", type=int, default=4)
        p.add_argument("--force", action="store_true",
                       help="redo work the state file says is already done")
        p.add_argument("--quiet", action="store_true")

    p = sub.add_parser("domains", help="list domains that have a registry")

    p = sub.add_parser("validate", help="parse every registry and report problems")
    p.add_argument("--domain", help="just this one")

    p = sub.add_parser("repos", help="list the repositories a selection resolves to")
    selection(p)
    p.add_argument("--json", action="store_true")

    p = sub.add_parser("mirror", help="clone or refresh bare blobless mirrors")
    selection(p); runner(p)
    p.add_argument("--refresh", action="store_true", help="fetch into existing mirrors")
    p.add_argument("--hydrate", action="store_true",
                   help="also download every blob in one fetch. Needed before "
                        "--line-counts, backport detection, or diffing a whole "
                        "history; costs disk, so scope it")

    p = sub.add_parser("walk", help="enumerate every commit into a frame")
    selection(p); runner(p)
    p.add_argument("--max-files", type=int, default=walk.WalkConfig.max_files)
    p.add_argument("--limit", type=int, default=0, help="cap commits kept per repo")
    p.add_argument("--no-backports", action="store_true",
                   help="skip patch-id backport detection")
    p.add_argument("--line-counts", action="store_true",
                   help="record insertions/deletions. Needs a hydrated mirror: "
                        "on a blobless one this is a network fetch per commit")
    p.add_argument("--keep-non-source", action="store_true",
                   help="keep commits that touch no recognised source file")
    p.add_argument("--full", action="store_true",
                   help="re-read the whole history instead of only what landed "
                        "since the last walk")

    p = sub.add_parser("signals", help="annotate frames with deterministic signals")
    selection(p); runner(p)
    p.add_argument("--min-signals", type=int, default=0,
                   help="mark rows below this as drop:below_min_signals (0 = keep all)")

    p = sub.add_parser("diffs", help="materialise diffs for the rows to be classified")
    selection(p); runner(p)
    p.add_argument("--min-signals", type=int, default=0)
    p.add_argument("--limit-per-repo", type=int, default=0)

    p = sub.add_parser("classify", help="run a classifier and append to the CSV")
    selection(p)
    p.add_argument("--quiet", action="store_true")
    p.add_argument("--engine", default="heuristic",
                   help="heuristic | llm:claude | llm:openai | llm:ollama")
    p.add_argument("--model", default="")
    p.add_argument("--base-url", default="")
    p.add_argument("--api-key", default="")
    p.add_argument("--workers", type=int, default=4)
    p.add_argument("--batch", type=int, default=50)
    p.add_argument("--min-signals", type=int, default=0)
    p.add_argument("--limit-per-repo", type=int, default=0)
    p.add_argument("--no-diffs", action="store_true",
                   help="classify from the message alone (cheaper, much worse)")
    p.add_argument("--reclassify", action="store_true",
                   help="also re-run commits that already carry a verdict")
    p.add_argument("--overwrite-human", action="store_true",
                   help="let the model overwrite rows labelled by a human")
    p.add_argument("--no-prefilter", action="store_true",
                   help="send every commit to the model, including the ones "
                        "the structural engine settles for free")
    p.add_argument("--no-veto", action="store_true",
                   help="do not let a provable refutation (no code token "
                        "changed) overrule a model's positive verdict")

    p = sub.add_parser("run", help="mirror → walk → signals → diffs → classify")
    selection(p); runner(p)
    p.add_argument("--engine", default="heuristic")
    p.add_argument("--model", default="")
    p.add_argument("--base-url", default="")
    p.add_argument("--min-signals", type=int, default=0)
    p.add_argument("--limit", type=int, default=0)
    p.add_argument("--limit-per-repo", type=int, default=0)
    p.add_argument("--refresh", action="store_true")
    p.add_argument("--hydrate", action="store_true",
                   help="download blobs up front, enabling line counts, backport "
                        "detection and fast whole-history diffs")
    p.add_argument("--no-classify", action="store_true",
                   help="stop after diffs — the deterministic half")

    p = sub.add_parser("status", help="what each stage has done so far")
    p.add_argument("--domain", help="one domain, or every domain if omitted")

    p = sub.add_parser("migrate",
                       help="bring an existing table up to the current schema")
    p.add_argument("--domain", help="one domain, or every domain if omitted")
    p.add_argument("--apply", action="store_true",
                   help="actually rewrite. Without it, only say what would change")

    return ap


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    try:
        return _dispatch(args)
    except registry.RegistryError as exc:
        print(f"registry error: {exc}", file=sys.stderr)
        return 2
    except classify_mod.ClassifyError as exc:
        print(f"classifier error: {exc}", file=sys.stderr)
        return 2
    except KeyboardInterrupt:
        print("\ninterrupted — state was saved; rerun the same command to resume",
              file=sys.stderr)
        return 130


def _dispatch(args: argparse.Namespace) -> int:
    cmd = args.cmd
    if cmd == "domains":
        return _cmd_domains()
    if cmd == "validate":
        return _cmd_validate(args)
    if cmd == "status":
        return _cmd_status(args)
    if cmd == "migrate":
        return _cmd_migrate(args)

    reg = registry.load(args.domain)
    repos = reg.select(
        slugs=args.slugs, tier=args.tier, category=args.category,
        language=args.language, tag=args.tag,
        include_archived=not args.skip_archived)
    if not repos:
        print("selection matched no repositories", file=sys.stderr)
        return 1

    if cmd == "repos":
        return _cmd_repos(repos, args.json)
    if cmd == "mirror":
        rep = pipeline.run_mirror(args.domain, repos, workers=args.workers,
                                  refresh=args.refresh, hydrate=args.hydrate,
                                  force=args.force, quiet=args.quiet)
    elif cmd == "walk":
        rep = pipeline.run_walk(args.domain, repos, cfg=_walk_cfg(args),
                                workers=args.workers, force=args.force,
                                full=args.full, quiet=args.quiet)
    elif cmd == "signals":
        rep = pipeline.run_signals(args.domain, repos, min_signals=args.min_signals,
                                   workers=args.workers, force=args.force,
                                   quiet=args.quiet)
    elif cmd == "diffs":
        rep = pipeline.run_diffs(args.domain, repos, min_signals=args.min_signals,
                                 limit_per_repo=args.limit_per_repo,
                                 workers=args.workers, force=args.force,
                                 quiet=args.quiet)
    elif cmd == "classify":
        rep = pipeline.run_classify(
            args.domain, repos, _engine(args), min_signals=args.min_signals,
            limit_per_repo=args.limit_per_repo, batch=args.batch,
            with_diffs=not args.no_diffs, overwrite_human=args.overwrite_human,
            skip_classified=not args.reclassify,
            veto_trivial=not args.no_veto,
            prefilter=not args.no_prefilter, quiet=args.quiet)
    elif cmd == "run":
        return _cmd_run(args, repos)
    else:                                            # pragma: no cover
        raise AssertionError(cmd)

    print(rep)
    return 1 if rep.failed else 0


def _walk_cfg(args: argparse.Namespace) -> walk.WalkConfig:
    return walk.WalkConfig(
        max_files=args.max_files,
        require_source=not args.keep_non_source,
        limit=args.limit,
        line_counts=getattr(args, "line_counts", False),
        detect_backports=not args.no_backports,
    )


def _engine(args: argparse.Namespace):
    kwargs: dict = {}
    for name in ("model", "base_url", "api_key", "workers"):
        val = getattr(args, name, None)
        if val:
            kwargs[name] = val
    if args.engine.startswith("llm"):
        kwargs.setdefault("cache_path", paths.work_dir(args.domain) / "llm_cache.json")
    return classify_mod.get(args.engine, **kwargs)


def _cmd_domains() -> int:
    for d in paths.list_domains():
        p = paths.registry_path(d)
        if not p.exists():
            print(f"{d:<16} (no registry)")
            continue
        reg = registry.load(d)
        tiers = {t: sum(1 for r in reg if r.tier == t) for t in (1, 2, 3)}
        print(f"{d:<16} {len(reg):>4} repos   "
              f"tier1={tiers[1]} tier2={tiers[2]} tier3={tiers[3]}")
    return 0


def _cmd_validate(args: argparse.Namespace) -> int:
    domains = [args.domain] if args.domain else paths.list_domains()
    bad = 0
    for d in domains:
        p = paths.registry_path(d)
        if not p.exists():
            print(f"{d}: no registry at {p}", file=sys.stderr)
            bad += 1
            continue
        try:
            reg = registry.load(d)
        except registry.RegistryError as exc:
            print(f"{d}: {exc}", file=sys.stderr)
            bad += 1
            continue
        print(f"{d}: {len(reg)} repos ok")
    return 1 if bad else 0


def _cmd_repos(repos, as_json: bool) -> int:
    if as_json:
        print(json.dumps([{
            "slug": r.slug, "repo": r.repo, "tier": r.tier,
            "category": r.category, "language": r.language,
            "tags": list(r.tags), "archived": r.archived,
        } for r in repos], indent=1))
        return 0
    width = max((len(r.slug) for r in repos), default=4)
    for r in repos:
        flags = " [archived]" if r.archived else ""
        print(f"{r.slug:<{width}}  t{r.tier}  {r.repo}"
              f"{'  ' + r.category if r.category else ''}{flags}")
    print(f"\n{len(repos)} repositories")
    return 0


def _cmd_run(args: argparse.Namespace, repos) -> int:
    reports = [
        pipeline.run_mirror(args.domain, repos, workers=args.workers,
                            refresh=args.refresh, hydrate=args.hydrate,
                            force=args.force, quiet=args.quiet),
        pipeline.run_walk(args.domain, repos,
                          cfg=walk.WalkConfig(limit=args.limit,
                                              line_counts=args.hydrate,
                                              detect_backports=args.hydrate),
                          workers=args.workers, force=args.force, quiet=args.quiet),
        pipeline.run_signals(args.domain, repos, min_signals=args.min_signals,
                             workers=args.workers, force=args.force, quiet=args.quiet),
        pipeline.run_diffs(args.domain, repos, min_signals=args.min_signals,
                           limit_per_repo=args.limit_per_repo,
                           workers=args.workers, force=args.force, quiet=args.quiet),
    ]
    if not args.no_classify:
        reports.append(pipeline.run_classify(
            args.domain, repos, _engine(args), min_signals=args.min_signals,
            limit_per_repo=args.limit_per_repo, quiet=args.quiet))
    print()
    for rep in reports:
        print(rep)
    return 1 if any(r.failed for r in reports) else 0


def _cmd_migrate(args: argparse.Namespace) -> int:
    """Rewrite a published table under the current schema.

    Dry by default. A migration that silently rewrote 34,000 rows would be
    indistinguishable from a bug, so you get to read the diff first.
    """
    domains = [args.domain] if args.domain else paths.list_domains()
    touched = 0
    for d in domains:
        sink = Sink(d)
        if not sink.path.exists():
            continue
        df = sink.read()                      # conform() already migrates on read
        notes = manifest.plan_migration(d, df)
        rel = sink.path.relative_to(paths.repo_root())
        if not notes:
            print(f"{d}: {rel} is current ({len(df):,} rows, "
                  f"schema {schema.SCHEMA_VERSION})")
            continue
        print(f"{d}: {rel} ({len(df):,} rows)")
        for n in notes:
            print(f"  - {n}")
        if not args.apply:
            print("  (dry run — pass --apply to rewrite)")
            continue
        sink.merge(df.iloc[0:0])              # rewrites through the conformed path
        sink._write(df)
        sink.write_preview()
        manifest.write(d, sink.read())
        touched += 1
        print(f"  rewritten; manifest updated")
    if not args.apply and touched == 0:
        return 0
    return 0


def _cmd_status(args: argparse.Namespace) -> int:
    domains = [args.domain] if args.domain else paths.list_domains()
    for d in domains:
        p = paths.registry_path(d)
        if not p.exists():
            continue
        reg = registry.load(d)
        print(f"\n=== {d} ({len(reg)} repos in registry) ===")
        for stage in (Stage.MIRROR, Stage.WALK, Stage.SIGNALS,
                      Stage.DIFFS, Stage.CLASSIFY):
            st = StageState(d, stage)
            ok, total = st.summary()
            if not total:
                print(f"  {stage:<9} not started")
                continue
            failed = [s for s, e in st.entries.items() if not e.get("ok")]
            line = f"  {stage:<9} {ok}/{len(reg)} repos ok"
            if failed:
                line += f"  ({len(failed)} failed: {', '.join(sorted(failed)[:5])}"
                line += ", …)" if len(failed) > 5 else ")"
            print(line)
        used = mirror.disk_usage(d)
        if used:
            print(f"  mirrors   {used / 1e9:.1f} GB on disk")
        sink = Sink(d)
        if sink.path.exists():
            df = sink.read()
            labelled = int((df["classifier"].fillna("") != "").sum())
            fixes = int(df["is_bugfix"].fillna(False).sum())
            sec = int(df["is_security_fix"].fillna(False).sum())
            print(f"  csv       {len(df):,} rows, {labelled:,} classified, "
                  f"{fixes:,} bug fixes, {sec:,} security fixes "
                  f"→ {sink.path.relative_to(paths.repo_root())}")
            mf = manifest.read(d)
            if mf:
                by = ", ".join(f"{k} {v:,}" for k, v in
                               sorted(mf.get("by_classifier", {}).items()))
                print(f"  manifest  schema {mf.get('schema_version')}, "
                      f"oss-bugfixes {mf.get('tool_version')}, "
                      f"updated {mf.get('updated_at')}"
                      + (f" — {by}" if by else ""))
            stale = manifest.plan_migration(d, df)
            if stale:
                print(f"  migrate   {len(stale)} change(s) pending — "
                      f"run `oss-bugfixes migrate --domain {d}`")
    return 0


if __name__ == "__main__":                            # pragma: no cover
    raise SystemExit(main())
