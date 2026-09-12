"""
What: the one function every LLM call in this project goes through. All of
      pipeline/, api/ and eval/ call call_llm() instead of touching the
      openai SDK directly (CLAUDE.md: "Never call the OpenAI SDK directly
      elsewhere") — that includes RAGAS's calls in eval/metrics.py, via a
      small adapter there.

Why one function covers two providers: Ollama serves an OpenAI-compatible
      /v1/chat/completions endpoint, so the *same* openai.OpenAI client works
      for both real OpenAI and the local Qwen 2.5 7B ablation — only
      base_url, api_key and model differ. That is what makes the D-open
      ablation config a one-argument change (provider="ollama") instead of a
      second call path to keep in sync with the first.

Why the cache is mandatory, not an optimisation: CLAUDE.md is explicit that
      eval re-runs the same ~150 questions many times, and uncached that is
      real API spend. Every call is looked up by a hash of everything that
      affects the output (prompt_version, model, provider, system, prompt,
      temperature, max_tokens) before any network call happens. get_stats()
      reports calls/hits/misses/tokens since process start so an eval run
      can print what it actually cost, cache hits included.
"""

from __future__ import annotations

import hashlib
import json
import time
from dataclasses import dataclass
from pathlib import Path

from app.core.config import get_settings

# backend/app/pipeline/llm.py -> parents[3] is the repo root (matches
# core/config.py's _ENV_FILE resolution, same directory depth).
_REPO_ROOT = Path(__file__).resolve().parents[3]

_stats = {"calls": 0, "hits": 0, "misses": 0, "prompt_tokens": 0, "completion_tokens": 0}


@dataclass(frozen=True)
class LLMResult:
    text: str
    model: str
    provider: str
    prompt_version: str
    cached: bool
    prompt_tokens: int | None
    completion_tokens: int | None


def _cache_root() -> Path:
    cache_dir = Path(get_settings().llm_cache_dir)
    if not cache_dir.is_absolute():
        cache_dir = _REPO_ROOT / cache_dir
    return cache_dir


def cache_key(
    prompt_version: str,
    model: str,
    provider: str,
    system: str | None,
    prompt: str,
    temperature: float,
    max_tokens: int | None,
) -> str:
    """Pure hash of everything that determines the response. CLAUDE.md calls
    this (prompt_version, model, input hash); provider is folded into the
    same hash — a provider switch (openai -> ollama) must land a different
    cache entry, same as any other input change."""
    payload = {
        "prompt_version": prompt_version,
        "model": model,
        "provider": provider,
        "system": system,
        "prompt": prompt,
        "temperature": temperature,
        "max_tokens": max_tokens,
    }
    blob = json.dumps(payload, sort_keys=True, ensure_ascii=False)
    return hashlib.sha256(blob.encode("utf-8")).hexdigest()


def _cache_path(prompt_version: str, key: str) -> Path:
    return _cache_root() / prompt_version / f"{key}.json"


def _model_for(provider: str) -> str:
    settings = get_settings()
    if provider == "openai":
        if not settings.openai_model:
            raise ValueError(
                "OPENAI_MODEL is not set in .env — pin an exact model string "
                "before calling the OpenAI provider."
            )
        return settings.openai_model
    if provider == "ollama":
        return settings.ollama_model
    raise ValueError(f"Unknown provider {provider!r} — expected 'openai' or 'ollama'.")


def _client_for(provider: str):
    from openai import OpenAI

    settings = get_settings()
    if provider == "openai":
        if not settings.openai_api_key:
            raise ValueError("OPENAI_API_KEY is not set in .env.")
        return OpenAI(api_key=settings.openai_api_key)
    if provider == "ollama":
        # Ollama's OpenAI-compatible endpoint ignores the API key, but the
        # SDK requires a non-empty string to construct a client.
        return OpenAI(api_key="ollama", base_url=f"{settings.ollama_base_url}/v1")
    raise ValueError(f"Unknown provider {provider!r} — expected 'openai' or 'ollama'.")


def call_llm(
    prompt: str,
    *,
    prompt_version: str,
    system: str | None = None,
    provider: str = "openai",
    temperature: float = 0.0,
    max_tokens: int | None = None,
) -> LLMResult:
    """The only function in this project that calls an LLM. Checks the disk
    cache first; only calls the network on a miss."""
    settings = get_settings()
    model = _model_for(provider)
    key = cache_key(prompt_version, model, provider, system, prompt, temperature, max_tokens)

    _stats["calls"] += 1

    if settings.llm_cache_enabled:
        cached = _read_cache(prompt_version, key)
        if cached is not None:
            _stats["hits"] += 1
            return LLMResult(
                text=cached["text"],
                model=model,
                provider=provider,
                prompt_version=prompt_version,
                cached=True,
                prompt_tokens=cached.get("prompt_tokens"),
                completion_tokens=cached.get("completion_tokens"),
            )

    _stats["misses"] += 1

    messages = []
    if system:
        messages.append({"role": "system", "content": system})
    messages.append({"role": "user", "content": prompt})

    client = _client_for(provider)
    kwargs = {"model": model, "messages": messages, "temperature": temperature}
    if max_tokens is not None:
        kwargs["max_tokens"] = max_tokens
    response = client.chat.completions.create(**kwargs)

    text = response.choices[0].message.content or ""
    prompt_tokens = response.usage.prompt_tokens if response.usage else None
    completion_tokens = response.usage.completion_tokens if response.usage else None

    if prompt_tokens:
        _stats["prompt_tokens"] += prompt_tokens
    if completion_tokens:
        _stats["completion_tokens"] += completion_tokens

    if settings.llm_cache_enabled:
        _write_cache(
            prompt_version,
            key,
            {
                "prompt_version": prompt_version,
                "model": model,
                "provider": provider,
                "system": system,
                "prompt": prompt,
                "temperature": temperature,
                "max_tokens": max_tokens,
                "text": text,
                "prompt_tokens": prompt_tokens,
                "completion_tokens": completion_tokens,
                "cached_at": time.time(),
            },
        )

    return LLMResult(
        text=text,
        model=model,
        provider=provider,
        prompt_version=prompt_version,
        cached=False,
        prompt_tokens=prompt_tokens,
        completion_tokens=completion_tokens,
    )


def _read_cache(prompt_version: str, key: str) -> dict | None:
    path = _cache_path(prompt_version, key)
    if not path.exists():
        return None
    return json.loads(path.read_text(encoding="utf-8"))


def _write_cache(prompt_version: str, key: str, record: dict) -> None:
    path = _cache_path(prompt_version, key)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(record, indent=2, ensure_ascii=False), encoding="utf-8")


def get_stats() -> dict:
    """Snapshot of calls/hits/misses/tokens since process start or the last
    reset_stats() — what eval/runner.py prints at the end of a run so an
    eval run's real cost is visible, not just implied."""
    return dict(_stats)


def reset_stats() -> None:
    for k in _stats:
        _stats[k] = 0
