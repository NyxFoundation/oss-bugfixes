# Contributing

## The highest-value contribution is a registry line

`domains/<domain>/repos.yml` **is** this project's coverage claim. Nothing
outside those files names a repository, so adding one is a registry edit and
nothing else:

```yaml
  my-slug:
    repo: owner/name
    category: library
    tier: 2
    language: rust
```

```bash
oss-bugfixes validate --domain <domain>
oss-bugfixes run --domain <domain> --repo my-slug --limit 200 --engine lean
```

Pick the domain by its **scope rule** — the one-sentence membership test at the
top of each registry, phrased as the consequence of a defect rather than as a
category of software. If a repository fits two, it belongs in the one whose
consequence is worse.

## Correcting the corpus

A row whose `classifier` is `human` is never overwritten by a model. Set it,
write a `rationale` saying what you checked, and the verdict is permanent:

```python
from ossbugfix.sink import Sink
import pandas as pd
sink = Sink("wallet")
df = sink.read()
df.loc[df.sha == "<sha>", ["is_security_fix", "classifier", "rationale"]] = \
    [True, "human", "confirmed: the pre-fix branch is reachable from a public entry point"]
sink.merge(df, overwrite_human=True)
```

Disagreements with the model are the most useful rows in the corpus — they are
how the classifier's calibration gets measured at all.

## Development

```bash
uv venv && uv pip install -e ".[dev]"
uv run python -m pytest -q          # 65 tests, offline, builds real git repos
uv run oss-bugfixes validate        # every registry parses
```

The Lean engine builds from a bare toolchain, no mathlib, no package index:

```bash
cd lean && lake build && ./.lake/build/bin/ossbugfix-classify --selftest
```

## Rules the code keeps

These are not style preferences. Each one is a bug this lineage already
shipped, and a change that breaks one will be asked to justify itself:

1. **A skipped check is `<NA>`, never `False`.** Backport detection that timed
   out once reported "0 backported", indistinguishable from a repository that
   backports nothing. Every tri-state column stays tri-state end to end.
2. **A failed classification is undecided, not negative.** Timeouts and
   unparseable replies record the error; they never become `is_bugfix=false`.
3. **A truncated diff says so, in the prompt.** A model handed a silently cut
   patch reasons confidently about code it never saw.
4. **An optional column never costs a repository.** A failing enrichment loses
   the column and nothing else.
5. **Human labels win.** `--overwrite-human` exists and is deliberately
   awkward to type.
6. **Signals annotate; they do not gate.** A row firing no signal still reaches
   the classifier. `--min-signals` marks rows `drop:below_min_signals` rather
   than labelling them negative.

In the Lean engine, rules 1, 2 and 6 are theorems in
[`lean/OssBugfix/Props.lean`](lean/OssBugfix/Props.lean). If you add a detector
there, the proofs must still go through — that is the point of them.

## Adding a classifier engine

Implement `classify.base.Classifier`, then `classify.register("name", Cls)`.
Two obligations:

- return `is_bugfix=None` when you could not decide;
- put something in `rationale` that cites what decided it.

## Commits

Say what changed and why it was wrong before. The commit messages in this
repository are part of its documentation — a reader should be able to learn
the reason for a constant from `git log` without finding the issue.
