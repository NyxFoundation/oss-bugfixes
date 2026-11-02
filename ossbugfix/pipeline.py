"""pipeline.py — the five stages, each resumable, each able to lose one
repository without losing the run.

That last property is the one that matters at this scale. A 180-repo sweep
where a single clone failure aborts everything is a sweep that never finishes,
so every stage catches per-repository failure, records it in the state file,
and carries on. `oss-bugfixes status` is then how you find out what to retry.

    mirror   → work/<domain>/mirrors/<slug>.git
    walk     → work/<domain>/commits/<slug>.parquet
    signals  → work/<domain>/candidates/<slug>.parquet
    diffs    → work/<domain>/diffs/<slug>/<ab>/<sha>.diff.gz
    classify → domains/<domain>/data/bugfixes.csv
"""
from __future__ import annotations

import sys
from concurrent.futures import ThreadPoolExecutor, as_completed
from dataclasses import dataclass
from pathlib import Path
from typing import Callable, Iterable, Sequence

import pandas as pd

from . import diffs as diffs_mod
from . import mirror, paths, schema, signals, walk
from .classify.base import Classifier, CommitView
from .registry import Repo
from .sink import Sink
from .state import Stage, StageState

__all__ = ["StageReport", "run_mirror", "run_walk", "run_signals",
           "run_diffs", "run_classify", "load_frames"]


@dataclass
class StageReport:
    stage: str
    ok: int = 0
    failed: int = 0
    skipped: int = 0
    rows: int = 0
    notes: list[str] = None  # type: ignore[assignment]

    def __post_init__(self) -> None:
        if self.notes is None:
            self.notes = []

    def __str__(self) -> str:
        bits = [f"{self.stage}: {self.ok} ok"]
        if self.skipped:
            bits.append(f"{self.skipped} skipped")
        if self.failed:
            bits.append(f"{self.failed} failed")
        if self.rows:
            bits.append(f"{self.rows:,} rows")
        return ", ".join(bits)


def _parallel(items: Sequence[Repo], fn: Callable[[Repo], tuple[bool, str]],
              workers: int, label: str, quiet: bool) -> tuple[int, int, list[str]]:
    ok = failed = 0
    notes: list[str] = []
    if workers <= 1:
        results = ((r, fn(r)) for r in items)
        for repo, (good, note) in results:
            ok, failed = _tally(repo, good, note, ok, failed, notes, label, quiet)
        return ok, failed, notes

    with ThreadPoolExecutor(max_workers=workers) as pool:
        futures = {pool.submit(fn, r): r for r in items}
        for fut in as_completed(futures):
            repo = futures[fut]
            try:
                good, note = fut.result()
            except Exception as exc:              # noqa: BLE001 — one repo, not the run
                good, note = False, f"{type(exc).__name__}: {exc}"
            ok, failed = _tally(repo, good, note, ok, failed, notes, label, quiet)
    return ok, failed, notes


def _tally(repo, good, note, ok, failed, notes, label, quiet):
    if good:
        ok += 1
        if not quiet:
            print(f"  [{label}] {repo.slug}: {note}")
    else:
        failed += 1
        notes.append(f"{repo.slug}: {note}")
        print(f"  [{label}] {repo.slug}: FAILED — {note}", file=sys.stderr)
    return ok, failed


# --- stage 1: mirror --------------------------------------------------------

def run_mirror(domain: str, repos: Sequence[Repo], *, workers: int = 4,
               refresh: bool = False, hydrate: bool = False, force: bool = False,
               quiet: bool = False) -> StageReport:
    st = StageState(domain, Stage.MIRROR)
    rep = StageReport(Stage.MIRROR)
    todo = []
    for r in repos:
        already = st.done(r.slug) and mirror.exists(r)
        if hydrate and already and mirror.is_blobless(mirror.mirror_path(r)):
            already = False          # present, but not yet in the state asked for
        if not force and not refresh and already:
            rep.skipped += 1
            continue
        todo.append(r)

    def one(repo: Repo) -> tuple[bool, str]:
        res = mirror.ensure(repo, refresh_if_present=refresh)
        if res.ok and hydrate:
            res = mirror.hydrate(repo)
        st.record(repo.slug, ok=res.ok, action=res.action,
                  seconds=round(res.seconds, 1), error=res.error)
        return res.ok, (f"{res.action} in {res.seconds:.1f}s" if res.ok else res.error)

    rep.ok, rep.failed, rep.notes = _parallel(todo, one, workers, "mirror", quiet)
    st.save()
    return rep


# --- stage 2: walk ----------------------------------------------------------

def run_walk(domain: str, repos: Sequence[Repo], *, cfg: walk.WalkConfig | None = None,
             workers: int = 4, force: bool = False, quiet: bool = False) -> StageReport:
    cfg = cfg or walk.WalkConfig()
    st = StageState(domain, Stage.WALK)
    rep = StageReport(Stage.WALK)
    out_dir = paths.commits_dir(domain)
    out_dir.mkdir(parents=True, exist_ok=True)

    todo = []
    for r in repos:
        if not force and st.done(r.slug) and (out_dir / f"{r.slug}.parquet").exists():
            rep.skipped += 1
            continue
        todo.append(r)

    def one(repo: Repo) -> tuple[bool, str]:
        res = walk.walk(repo, cfg)
        if not res.ok:
            st.record(repo.slug, ok=False, error=res.error)
            return False, res.error
        path = out_dir / f"{repo.slug}.parquet"
        res.frame.to_parquet(path, index=False)
        st.record(repo.slug, ok=True, seen=res.seen, kept=res.kept,
                  dropped=res.dropped, backports=res.backport_state)
        rep.rows += res.kept
        drops = ", ".join(f"{k}={v}" for k, v in sorted(res.dropped.items()))
        return True, (f"{res.kept:,}/{res.seen:,} kept"
                      + (f" (dropped {drops})" if drops else "")
                      + f"; {res.backport_state}")

    rep.ok, rep.failed, rep.notes = _parallel(todo, one, workers, "walk", quiet)
    st.save()
    return rep


# --- stage 3: signals -------------------------------------------------------

def run_signals(domain: str, repos: Sequence[Repo], *, min_signals: int = 0,
                workers: int = 4, force: bool = False,
                quiet: bool = False) -> StageReport:
    st = StageState(domain, Stage.SIGNALS)
    rep = StageReport(Stage.SIGNALS)
    src_dir = paths.commits_dir(domain)
    out_dir = paths.candidates_dir(domain)
    out_dir.mkdir(parents=True, exist_ok=True)

    todo = []
    for r in repos:
        if not (src_dir / f"{r.slug}.parquet").exists():
            continue
        if not force and st.done(r.slug) and (out_dir / f"{r.slug}.parquet").exists():
            rep.skipped += 1
            continue
        todo.append(r)

    def one(repo: Repo) -> tuple[bool, str]:
        df = pd.read_parquet(src_dir / f"{repo.slug}.parquet")
        annotated = signals.annotate(df, min_signals=min_signals)
        annotated.to_parquet(out_dir / f"{repo.slug}.parquet", index=False)
        kept = int((annotated["prefilter"] == "keep").sum())
        fired = int((annotated["n_signals"].fillna(0) > 0).sum())
        st.record(repo.slug, ok=True, rows=len(annotated), kept=kept, fired=fired)
        rep.rows += kept
        return True, f"{len(annotated):,} rows, {fired:,} with a signal, {kept:,} kept"

    rep.ok, rep.failed, rep.notes = _parallel(todo, one, workers, "signals", quiet)
    st.save()
    return rep


# --- stage 4: diffs ---------------------------------------------------------

def run_diffs(domain: str, repos: Sequence[Repo], *, limit_per_repo: int = 0,
              min_signals: int = 0, workers: int = 4, force: bool = False,
              hydrate_over: int = 200, quiet: bool = False) -> StageReport:
    """Materialise diffs for the rows a classifier will actually be given.

    Not for every commit by default: on a blobless mirror each uncached diff is
    a network round trip, so a 600k-commit history would take days. Two ways
    out, and the stage picks between them:

      * few diffs  → fetch them lazily, one `git show` at a time;
      * many diffs → hydrate the mirror first (one fetch for every blob), then
        every `git show` is local.

    `hydrate_over` is where that trade flips. A lazy diff measured ~0.43 s on
    argon2 (214.7 s for 496 commits) while hydrating that repo cost 0.9 s, so
    the crossover is in the low hundreds, not the thousands — 200 is a
    deliberately conservative read of that.
    """
    st = StageState(domain, Stage.DIFFS)
    rep = StageReport(Stage.DIFFS)
    cache = diffs_mod.DiffCache(domain)

    def one(repo: Repo) -> tuple[bool, str]:
        df = _candidates_for(domain, repo, min_signals=min_signals)
        if df is None:
            return False, "no commit frame — run walk/signals first"
        if limit_per_repo:
            df = df.head(limit_per_repo)
        wanted = [s for s in df["sha"].tolist()
                  if force or not cache.has(repo.slug, s)]
        hydrated = ""
        if (hydrate_over and len(wanted) > hydrate_over
                and mirror.is_blobless(mirror.mirror_path(repo))):
            res = mirror.hydrate(repo)
            hydrated = (f"hydrated in {res.seconds:.0f}s; " if res.ok
                        else f"hydrate failed ({res.error}); ")
        got = missing = cached = 0
        for sha in df["sha"].tolist():
            if not force and cache.has(repo.slug, sha):
                cached += 1
                continue
            if diffs_mod.get_diff(repo, sha, cache=cache) is None:
                missing += 1
            else:
                got += 1
        st.record(repo.slug, ok=True, fetched=got, cached=cached, missing=missing)
        rep.rows += got + cached
        return True, (hydrated + f"{got:,} fetched, {cached:,} already cached"
                      + (f", {missing:,} unavailable" if missing else ""))

    rep.ok, rep.failed, rep.notes = _parallel(list(repos), one, workers, "diffs", quiet)
    st.save()
    return rep


# --- stage 5: classify ------------------------------------------------------

def run_classify(domain: str, repos: Sequence[Repo], classifier: Classifier, *,
                 min_signals: int = 0, limit_per_repo: int = 0, batch: int = 50,
                 with_diffs: bool = True, overwrite_human: bool = False,
                 skip_classified: bool = True, quiet: bool = False) -> StageReport:
    st = StageState(domain, Stage.CLASSIFY)
    rep = StageReport(Stage.CLASSIFY)
    sink = Sink(domain)
    cache = diffs_mod.DiffCache(domain)

    already: set[tuple[str, str, str]] = set()
    if skip_classified:
        done = sink.read()
        if len(done):
            labelled = done[done["classifier"].fillna("") != ""]
            already = set(map(tuple, labelled[list(schema.KEY_COLUMNS)].values))

    for repo in repos:
        df = _candidates_for(domain, repo, min_signals=min_signals)
        if df is None:
            rep.skipped += 1
            continue
        if already:
            mask = [tuple(t) not in already
                    for t in df[list(schema.KEY_COLUMNS)].values]
            df = df[pd.Series(mask, index=df.index)]
        if limit_per_repo:
            df = df.head(limit_per_repo)
        if not len(df):
            rep.skipped += 1
            continue

        produced = 0
        for start in range(0, len(df), batch):
            chunk = df.iloc[start:start + batch]
            views = [_view(repo, row, cache if with_diffs else None) for _, row in chunk.iterrows()]
            try:
                verdicts = classifier.classify_many(views)
            except Exception as exc:              # noqa: BLE001
                rep.failed += 1
                rep.notes.append(f"{repo.slug}: {type(exc).__name__}: {exc}")
                print(f"  [classify] {repo.slug}: FAILED — {exc}", file=sys.stderr)
                break
            out = chunk.copy()
            for col, values in _verdict_columns(verdicts).items():
                out[col] = values
            stats = sink.merge(out, overwrite_human=overwrite_human)
            produced += len(out)
            rep.rows += len(out)
            if not quiet:
                print(f"  [classify] {repo.slug}: {stats}")
        if produced:
            rep.ok += 1
            st.record(repo.slug, ok=True, classified=produced,
                      engine=getattr(classifier, "engine_label", classifier.name))
    st.save()
    sink.write_preview()
    return rep


def _verdict_columns(verdicts) -> dict[str, list]:
    cols: dict[str, list] = {c: [] for c in schema.VERDICT_COLUMNS}
    for v in verdicts:
        row = v.as_row()
        for c in schema.VERDICT_COLUMNS:
            cols[c].append(row.get(c))
    return cols


def _view(repo: Repo, row: pd.Series, cache) -> CommitView:
    files = signals.files_of(row)
    diff = ""
    if cache is not None:
        diff = diffs_mod.get_diff(repo, str(row["sha"]), cache=cache,
                                  cap=diffs_mod.DEFAULT_CHAR_CAP) or ""
    return CommitView(
        id=schema.commit_id(repo.domain, repo.slug, str(row["sha"])),
        repo=repo.repo, sha=str(row["sha"]), url=repo.commit_url(str(row["sha"])),
        subject=_s(row.get("subject")), body=_s(row.get("body")),
        files=files, insertions=_i(row.get("insertions")), deletions=_i(row.get("deletions")),
        languages=_s(row.get("languages")), diff=diff,
        advisory_ids=_s(row.get("advisory_ids")), impact_class=_s(row.get("impact_class")),
        is_backport=_b(row.get("is_backport")), is_revert=_b(row.get("is_revert")),
        keyword_score=_f(row.get("keyword_score")),
    )


# --- shared helpers ---------------------------------------------------------

def _candidates_for(domain: str, repo: Repo, *, min_signals: int = 0) -> pd.DataFrame | None:
    """The best frame available for a repo: the signal-annotated one if the
    signals stage has run, the raw commit frame otherwise."""
    cand = paths.candidates_dir(domain) / f"{repo.slug}.parquet"
    src = cand if cand.exists() else paths.commits_dir(domain) / f"{repo.slug}.parquet"
    if not src.exists():
        return None
    df = pd.read_parquet(src)
    if "prefilter" in df.columns:
        df = df[df["prefilter"].fillna("keep") == "keep"]
    if min_signals and "n_signals" in df.columns:
        df = df[df["n_signals"].fillna(0) >= min_signals]
    if "n_signals" in df.columns:
        df = df.sort_values("n_signals", ascending=False, na_position="last")
    return df.reset_index(drop=True)


def load_frames(domain: str, repos: Iterable[Repo], *, stage: str = "candidates") -> pd.DataFrame:
    """Concatenate every per-repo frame for a domain. Used by `status` and by
    anyone analysing the corpus without going through the CSV."""
    root = paths.candidates_dir(domain) if stage == "candidates" else paths.commits_dir(domain)
    frames = [pd.read_parquet(p) for r in repos
              if (p := root / f"{r.slug}.parquet").exists()]
    if not frames:
        cols = schema.COMMIT_COLUMNS + (schema.SIGNAL_COLUMNS if stage == "candidates" else [])
        return schema.empty(cols)
    return pd.concat(frames, ignore_index=True)


def _s(v) -> str:
    return "" if v is None or pd.isna(v) else str(v)


def _i(v) -> int:
    try:
        return int(v)
    except (TypeError, ValueError):
        return 0


def _f(v) -> float:
    try:
        return float(v)
    except (TypeError, ValueError):
        return 0.0


def _b(v):
    if v is None or (isinstance(v, float) and pd.isna(v)) or v is pd.NA:
        return None
    return bool(v)
