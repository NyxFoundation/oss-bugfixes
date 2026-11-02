"""llm.py — the model-backed classifier.

Three backends, same prompt and same contract:

  claude    the `claude` CLI in print mode. No API key plumbing, uses whatever
            the developer is already logged into.
  openai    any OpenAI-compatible `/v1/chat/completions` endpoint — Ollama
            Cloud, vLLM, LM Studio, local ollama, or OpenAI itself. This is the
            one to use for a whole-corpus sweep, because it is where the cheap
            models are.
  ollama    a local ollama daemon's native `/api/generate`.

Prompt design follows LLM4VFD (arXiv:2501.14983): code-change intention plus
development artifacts plus light structural context, answered as a single JSON
object. Chain-of-thought is requested in a `rationale` field rather than as
free text before the JSON, so a truncated reply still parses.

Two things this module refuses to do:

  * **invent a verdict when the model failed.** A timeout, a refusal, or
    unparseable output produces a Verdict with `is_bugfix=None` and the error
    recorded. A corpus where failures silently became False is a corpus whose
    negatives cannot be trusted.
  * **re-ask a question it already has an answer to.** Replies are cached on
    disk by (model, prompt) hash, so a rerun after a crash costs nothing for
    the part that finished.
"""
from __future__ import annotations

import hashlib
import json
import os
import re
import shutil
import ssl
import subprocess
import threading
import time
import urllib.error
import urllib.request
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from typing import Any, Sequence

from .base import Classifier, ClassifyError, CommitView, Verdict

__all__ = ["LLMClassifier", "PROMPT_TEMPLATE", "ssl_context"]

#: Where a CA bundle lives on systems whose Python cannot find its own.
#: A relocatable interpreter (uv's python-build-standalone, conda, a venv
#: copied between machines) is compiled with a cafile path from the build host:
#: on NixOS that is `/etc/ssl/cert.pem`, which does not exist, and every HTTPS
#: call dies with CERTIFICATE_VERIFY_FAILED. Making the caller export
#: SSL_CERT_FILE works and is what the predecessor pipelines did, but it means
#: the tool is broken out of the box on a common platform, so resolve it here.
_CA_CANDIDATES = (
    "/etc/ssl/certs/ca-certificates.crt",    # Debian, Ubuntu, Arch, NixOS
    "/etc/pki/tls/certs/ca-bundle.crt",      # RHEL, Fedora
    "/etc/ssl/ca-bundle.pem",                # openSUSE
    "/etc/ssl/cert.pem",                     # Alpine, macOS ports
    "/usr/local/etc/openssl/cert.pem",       # Homebrew
)
_ssl_ctx: ssl.SSLContext | None = None


def ssl_context() -> ssl.SSLContext:
    """A verifying SSL context that actually has a CA bundle.

    Never returns an unverified context: a classifier that silently stopped
    checking certificates would be a worse bug than the one it works around.
    If nothing is found, the platform default is returned and the caller gets
    the real error.
    """
    global _ssl_ctx
    if _ssl_ctx is not None:
        return _ssl_ctx
    ctx = ssl.create_default_context()
    paths = ctx.cert_store_stats()
    if paths.get("x509") or paths.get("x509_ca"):
        _ssl_ctx = ctx
        return ctx
    for env in ("SSL_CERT_FILE", "REQUESTS_CA_BUNDLE", "CURL_CA_BUNDLE"):
        val = os.environ.get(env)
        if val and os.path.exists(val):
            ctx.load_verify_locations(cafile=val)
            _ssl_ctx = ctx
            return ctx
    try:
        import certifi
        ctx.load_verify_locations(cafile=certifi.where())
        _ssl_ctx = ctx
        return ctx
    except (ImportError, OSError):
        pass
    for cand in _CA_CANDIDATES:
        if os.path.exists(cand):
            try:
                ctx.load_verify_locations(cafile=cand)
            except OSError:
                continue
            _ssl_ctx = ctx
            return ctx
    _ssl_ctx = ctx
    return ctx

DEFAULT_TIMEOUT = int(os.environ.get("OSS_BUGFIXES_LLM_TIMEOUT", "180"))
MAX_RETRIES = 4

SYSTEM = (
    "You are a security engineer triaging commits from an open-source repository. "
    "You read a commit message and its diff and decide, conservatively, whether the "
    "commit fixes a defect and whether that defect had security consequences. "
    "You answer with one JSON object and nothing else."
)

PROMPT_TEMPLATE = """\
Repository: {repo}  ({languages})
Commit: {sha}
Subject: {subject}

Message body:
{body}

Files changed ({n_files}), +{insertions}/-{deletions}:
{files}

Deterministic signals already computed (these are hints, not conclusions):
{signals}

Diff:
```diff
{diff}
```

Decide:
1. is_bugfix — does this commit correct behaviour that was wrong before it?
   A new feature, a refactor with no behaviour change, a dependency bump, a
   docs/test-only change, or a formatting sweep is NOT a bug fix.
2. is_security_fix — if it is a bug fix, could the pre-fix behaviour have been
   induced or exploited by an attacker, or cost a user funds, keys, availability
   or confidentiality? Most bug fixes are not security fixes. Say false unless
   the diff shows it.
3. severity — Critical / High / Medium / Low / Info, or "" if not a security fix.
4. cwe — the single best CWE identifier (e.g. CWE-125), or "".
5. stride — Spoofing / Tampering / Repudiation / InformationDisclosure /
   DenialOfService / ElevationOfPrivilege, or "".
6. confidence — 0.0-1.0, your own calibration.
7. rationale — one or two sentences, citing what in the diff decided it.

A commit whose message says nothing about security can still be a security fix;
this corpus exists to find exactly those. Judge the diff, not the wording.
If the diff is truncated or absent, lower your confidence rather than guessing.

Reply with ONLY this JSON object:
{{"is_bugfix": true|false, "is_security_fix": true|false, "severity": "...",
 "cwe": "...", "stride": "...", "confidence": 0.0, "rationale": "..."}}
"""


class LLMClassifier(Classifier):
    name = "llm"

    def __init__(self, *, backend: str = "claude", model: str = "",
                 base_url: str = "", api_key: str = "",
                 timeout: int = DEFAULT_TIMEOUT, workers: int = 4,
                 diff_chars: int = 6_000, cache_path: Path | None = None,
                 **_: object) -> None:
        self.backend = backend or "claude"
        self.model = model
        self.timeout = timeout
        self.workers = max(1, int(workers))
        self.diff_chars = diff_chars
        self.base_url = base_url or _default_base_url(self.backend)
        self.api_key = api_key or _default_api_key(self.backend)
        self._cache_path = Path(cache_path) if cache_path else None
        self._cache: dict[str, dict] = _load_cache(self._cache_path)
        self._lock = threading.Lock()
        self._validate()

    @property
    def engine_label(self) -> str:
        return f"llm:{self.backend}"

    def _validate(self) -> None:
        if self.backend == "claude":
            if not shutil.which("claude"):
                raise ClassifyError(
                    "backend 'claude' needs the `claude` CLI on PATH; "
                    "use --engine openai with --base-url for a hosted model instead")
        elif self.backend in ("openai", "ollama"):
            if not self.base_url:
                raise ClassifyError(f"backend {self.backend!r} needs --base-url")
        else:
            raise ClassifyError(
                f"unknown backend {self.backend!r}; expected claude, openai or ollama")

    # --- public API --------------------------------------------------------

    def classify(self, view: CommitView) -> Verdict:
        prompt = build_prompt(view, diff_chars=self.diff_chars)
        key = _cache_key(self.backend, self.model, prompt)
        with self._lock:
            hit = self._cache.get(key)
        if hit is not None:
            return self._verdict_from(hit, cached=True)
        try:
            raw = self._call(prompt)
        except Exception as exc:                      # noqa: BLE001 — recorded, not raised
            return Verdict(classifier=self.engine_label, model=self.model,
                           rationale="", error=f"{type(exc).__name__}: {exc}")
        obj = _extract_json(raw)
        if obj is None:
            return Verdict(classifier=self.engine_label, model=self.model,
                           error=f"unparseable reply: {raw[:200]!r}")
        with self._lock:
            self._cache[key] = obj
        return self._verdict_from(obj)

    def classify_many(self, views: Sequence[CommitView]) -> list[Verdict]:
        if self.workers == 1 or len(views) <= 1:
            out = [self.classify(v) for v in views]
        else:
            with ThreadPoolExecutor(max_workers=self.workers) as pool:
                out = list(pool.map(self.classify, views))
        self.save_cache()
        return out

    def save_cache(self) -> None:
        if not self._cache_path:
            return
        with self._lock:
            snapshot = dict(self._cache)
        self._cache_path.parent.mkdir(parents=True, exist_ok=True)
        tmp = self._cache_path.with_suffix(".tmp")
        tmp.write_text(json.dumps(snapshot, ensure_ascii=False), encoding="utf-8")
        os.replace(tmp, self._cache_path)

    # --- backends ----------------------------------------------------------

    def _call(self, prompt: str) -> str:
        last: Exception | None = None
        for attempt in range(MAX_RETRIES):
            try:
                if self.backend == "claude":
                    return self._call_claude(prompt)
                if self.backend == "ollama":
                    return self._call_ollama(prompt)
                return self._call_openai(prompt)
            except _Retryable as exc:
                last = exc
                _backoff(attempt, exc.retry_after)
        raise ClassifyError(f"gave up after {MAX_RETRIES} attempts: {last}")

    def _call_claude(self, prompt: str) -> str:
        args = ["claude", "-p", "--output-format", "text"]
        if self.model:
            args += ["--model", self.model]
        try:
            r = subprocess.run(args, input=f"{SYSTEM}\n\n{prompt}",
                               capture_output=True, text=True,
                               encoding="utf-8", errors="replace",
                               timeout=self.timeout)
        except subprocess.TimeoutExpired as exc:
            raise _Retryable(f"claude timed out after {self.timeout}s") from exc
        if r.returncode != 0:
            err = (r.stderr or "").strip()[:200]
            if any(w in err.lower() for w in ("rate", "overloaded", "429", "529")):
                raise _Retryable(err)
            raise ClassifyError(f"claude exited {r.returncode}: {err}")
        return r.stdout

    def _call_openai(self, prompt: str) -> str:
        payload = {
            "model": self.model or "gpt-4o-mini",
            "messages": [{"role": "system", "content": SYSTEM},
                         {"role": "user", "content": prompt}],
            "temperature": 0,
            "max_tokens": 700,
        }
        headers = {"Content-Type": "application/json"}
        if self.api_key:
            headers["Authorization"] = f"Bearer {self.api_key}"
        data = self._post(f"{self.base_url.rstrip('/')}/chat/completions", payload, headers)
        try:
            return data["choices"][0]["message"]["content"]
        except (KeyError, IndexError, TypeError) as exc:
            raise ClassifyError(f"unexpected response shape: {str(data)[:200]}") from exc

    def _call_ollama(self, prompt: str) -> str:
        payload = {"model": self.model or "qwen2.5-coder",
                   "prompt": f"{SYSTEM}\n\n{prompt}",
                   "stream": False, "options": {"temperature": 0}}
        data = self._post(f"{self.base_url.rstrip('/')}/api/generate", payload,
                          {"Content-Type": "application/json"})
        return str(data.get("response", ""))

    def _post(self, url: str, payload: dict, headers: dict) -> dict[str, Any]:
        req = urllib.request.Request(
            url, data=json.dumps(payload).encode("utf-8"), headers=headers)
        try:
            with urllib.request.urlopen(req, timeout=self.timeout,
                                        context=ssl_context()) as r:
                return json.loads(r.read().decode("utf-8", "replace"))
        except urllib.error.HTTPError as exc:
            body = exc.read().decode("utf-8", "replace")[:200]
            if exc.code in (408, 409, 429, 500, 502, 503, 504, 529):
                raise _Retryable(f"HTTP {exc.code}: {body}",
                                 exc.headers.get("Retry-After")) from exc
            raise ClassifyError(f"HTTP {exc.code}: {body}") from exc
        except (urllib.error.URLError, TimeoutError, json.JSONDecodeError) as exc:
            raise _Retryable(f"{type(exc).__name__}: {exc}") from exc

    # --- shaping -----------------------------------------------------------

    def _verdict_from(self, obj: dict, *, cached: bool = False) -> Verdict:
        return Verdict(
            is_bugfix=_as_bool(obj.get("is_bugfix")),
            is_security_fix=_as_bool(obj.get("is_security_fix")),
            severity=_clean(obj.get("severity"), _SEVERITIES),
            cwe=_clean_cwe(obj.get("cwe")),
            stride=_clean(obj.get("stride"), _STRIDE),
            confidence=_as_float(obj.get("confidence")),
            rationale=str(obj.get("rationale") or "")[:1000],
            classifier=self.engine_label,
            model=self.model,
        )


# --- prompt -----------------------------------------------------------------

def build_prompt(view: CommitView, *, diff_chars: int = 6_000) -> str:
    files = "\n".join(f"  {f}" for f in list(view.files)[:40])
    if len(view.files) > 40:
        files += f"\n  … and {len(view.files) - 40} more"
    signals = _signal_lines(view)
    diff = view.diff or "(diff unavailable — judge from the message and say so)"
    if len(diff) > diff_chars:
        diff = diff[:diff_chars] + "\n… [truncated]"
    return PROMPT_TEMPLATE.format(
        repo=view.repo, languages=view.languages or "unknown", sha=view.sha,
        subject=view.subject or "(empty)", body=(view.body or "(empty)")[:2000],
        n_files=len(view.files), insertions=view.insertions,
        deletions=view.deletions, files=files or "  (none)",
        signals=signals, diff=diff)


def _signal_lines(view: CommitView) -> str:
    out = []
    if view.advisory_ids:
        out.append(f"  advisory ids: {view.advisory_ids}")
    if view.impact_class:
        out.append(f"  impact vocabulary in message: {view.impact_class}")
    if view.is_backport:
        out.append("  the same patch appears under more than one sha "
                   "(backported to a maintenance branch)")
    if view.is_revert:
        out.append("  this commit reverts an earlier one")
    if view.keyword_score:
        out.append(f"  security keyword score: {view.keyword_score:.2f}")
    return "\n".join(out) or "  (none fired — this is common for silent fixes)"


# --- parsing helpers --------------------------------------------------------

_SEVERITIES = {"critical": "Critical", "high": "High", "medium": "Medium",
               "low": "Low", "info": "Info", "informational": "Info"}
_STRIDE = {"spoofing": "Spoofing", "tampering": "Tampering",
           "repudiation": "Repudiation",
           "informationdisclosure": "InformationDisclosure",
           "information_disclosure": "InformationDisclosure",
           "denialofservice": "DenialOfService", "denial_of_service": "DenialOfService",
           "elevationofprivilege": "ElevationOfPrivilege",
           "elevation_of_privilege": "ElevationOfPrivilege"}
_CWE_RE = re.compile(r"CWE[-_ ]?(\d{1,4})", re.I)
_JSON_RE = re.compile(r"\{.*\}", re.S)


def _extract_json(text: str) -> dict | None:
    """Pull the JSON object out of a reply that may be wrapped in prose or a
    fenced block. Models add commentary no matter how firmly asked not to."""
    if not text:
        return None
    text = text.strip()
    for candidate in (text, _strip_fence(text)):
        try:
            obj = json.loads(candidate)
            if isinstance(obj, dict):
                return obj
        except json.JSONDecodeError:
            pass
    m = _JSON_RE.search(text)
    if not m:
        return None
    try:
        obj = json.loads(m.group(0))
    except json.JSONDecodeError:
        return None
    return obj if isinstance(obj, dict) else None


def _strip_fence(text: str) -> str:
    if not text.startswith("```"):
        return text
    body = text.split("\n", 1)[1] if "\n" in text else ""
    return body.rsplit("```", 1)[0].strip()


def _as_bool(v: Any) -> bool | None:
    if isinstance(v, bool):
        return v
    if v is None:
        return None
    t = str(v).strip().lower()
    if t in ("true", "yes", "1"):
        return True
    if t in ("false", "no", "0"):
        return False
    return None


def _as_float(v: Any) -> float:
    try:
        return round(max(0.0, min(1.0, float(v))), 3)
    except (TypeError, ValueError):
        return 0.0


def _clean(v: Any, table: dict[str, str]) -> str:
    return table.get(str(v or "").strip().lower().replace(" ", ""), "")


def _clean_cwe(v: Any) -> str:
    m = _CWE_RE.search(str(v or ""))
    return f"CWE-{int(m.group(1))}" if m else ""


class _Retryable(Exception):
    def __init__(self, msg: str, retry_after: str | None = None) -> None:
        super().__init__(msg)
        self.retry_after = retry_after


def _backoff(attempt: int, retry_after: str | None) -> None:
    if retry_after:
        try:
            time.sleep(min(60.0, float(retry_after)))
            return
        except (TypeError, ValueError):
            pass
    time.sleep(min(60.0, 2.0 ** attempt))


def _cache_key(backend: str, model: str, prompt: str) -> str:
    h = hashlib.sha256(f"{backend}\x00{model}\x00{prompt}".encode()).hexdigest()
    return h[:32]


def _load_cache(path: Path | None) -> dict[str, dict]:
    if not path or not path.exists():
        return {}
    try:
        raw = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {}
    return raw if isinstance(raw, dict) else {}


def _default_base_url(backend: str) -> str:
    if backend == "ollama":
        return os.environ.get("OLLAMA_HOST", "http://localhost:11434")
    if backend == "openai":
        return os.environ.get("OSS_BUGFIXES_BASE_URL", "https://api.openai.com/v1")
    return ""


def _default_api_key(backend: str) -> str:
    if backend == "openai":
        for var in ("OSS_BUGFIXES_API_KEY", "OPENAI_API_KEY", "OLLAMA_API_KEY"):
            if os.environ.get(var):
                return os.environ[var]
    return ""
