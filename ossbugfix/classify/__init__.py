"""classify — the one stage that is allowed to decide what a commit means.

Everything before this is deterministic. This is where a model (or a human, or
a deliberately dumb baseline) reads the message and the diff and says whether
the commit fixes a bug, and whether that bug was a security bug.

Engines are looked up by name so a domain can be re-classified with a different
model without touching any other stage::

    oss-bugfixes classify --domain wallet --engine llm:claude
    oss-bugfixes classify --domain crypto --engine openai --model qwen3-coder
    oss-bugfixes classify --domain os     --engine structural   # no model at all
    oss-bugfixes classify --domain os     --engine heuristic    # message only

Adding an engine is one `register()` call; nothing else in the framework needs
to learn about it.
"""
from __future__ import annotations

from .base import Classifier, Verdict, ClassifyError
from .heuristic import HeuristicClassifier
from .lean import LeanClassifier
from .structural import StructuralClassifier
from .llm import LLMClassifier

__all__ = ["Classifier", "Verdict", "ClassifyError", "get", "register", "available"]

_REGISTRY: dict[str, type[Classifier]] = {}


def register(name: str, cls: type[Classifier]) -> None:
    _REGISTRY[name] = cls


def available() -> list[str]:
    return sorted(_REGISTRY)


def get(name: str, **kwargs) -> Classifier:
    """Resolve an engine name. `llm:claude` selects the LLM engine with the
    `claude` backend — the colon form keeps backend choice out of a separate
    flag nobody remembers to pass."""
    base, _, backend = name.partition(":")
    if base not in _REGISTRY:
        raise ClassifyError(
            f"unknown engine {name!r}; available: {', '.join(available())}")
    if backend:
        kwargs.setdefault("backend", backend)
    return _REGISTRY[base](**kwargs)


register("heuristic", HeuristicClassifier)
register("llm", LLMClassifier)
register("lean", LeanClassifier)
register("structural", StructuralClassifier)
