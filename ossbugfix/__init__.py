"""oss-bugfixes — mine bug-fix commits out of open-source repositories.

The package is the *shared* half of this repository: a registry loader, a git
mirror manager, a whole-history commit walker, a diff server, a deterministic
signal pass, a pluggable classifier and an append-only CSV sink. The *per-domain*
half (which repositories, which past vulnerabilities, which analysis) lives under
`domains/<domain>/` and contains no code.

The pipeline is five stages. Everything before `classify` is deterministic,
offline after the mirror step, and resumable; `classify` is the only stage that
needs a model.

    mirror → walk → signals → diffs → classify → CSV
"""

__version__ = "0.1.0"
