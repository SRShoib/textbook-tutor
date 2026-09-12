"""Tests for pipeline/llm.py's disk cache and provider routing.
CLAUDE.md: test pipeline/ functions, skip route tests. No real network call
is ever made here: _client_for is monkeypatched with a fake client that
records every "request" it receives, so a test failure here would mean the
cache is broken, not that an API call failed."""

import json

import pytest

from app.pipeline import llm


class FakeMessage:
    def __init__(self, content):
        self.content = content


class FakeChoice:
    def __init__(self, content):
        self.message = FakeMessage(content)


class FakeUsage:
    def __init__(self, prompt_tokens, completion_tokens):
        self.prompt_tokens = prompt_tokens
        self.completion_tokens = completion_tokens


class FakeResponse:
    def __init__(self, content, prompt_tokens=10, completion_tokens=5):
        self.choices = [FakeChoice(content)]
        self.usage = FakeUsage(prompt_tokens, completion_tokens)


class FakeCompletions:
    def __init__(self, response_text="fake answer"):
        self.calls = []
        self.response_text = response_text

    def create(self, **kwargs):
        self.calls.append(kwargs)
        return FakeResponse(self.response_text)


class FakeChat:
    def __init__(self, completions):
        self.completions = completions


class FakeClient:
    def __init__(self, response_text="fake answer"):
        self.completions = FakeCompletions(response_text)
        self.chat = FakeChat(self.completions)


@pytest.fixture(autouse=True)
def isolated_llm_env(tmp_path, monkeypatch):
    """Every test gets its own empty cache dir, fresh stats, and settings
    that can never accidentally reach a real API."""
    monkeypatch.setattr(llm, "_REPO_ROOT", tmp_path)
    settings = llm.get_settings()
    settings.llm_cache_dir = ".llm_cache"
    settings.llm_cache_enabled = True
    settings.openai_model = "gpt-test"
    settings.openai_api_key = "sk-test"
    settings.ollama_model = "qwen-test"
    llm.reset_stats()
    yield


# --- cache_key ---------------------------------------------------------


def test_cache_key_deterministic():
    a = llm.cache_key("v1", "gpt-test", "openai", "sys", "prompt", 0.0, None)
    b = llm.cache_key("v1", "gpt-test", "openai", "sys", "prompt", 0.0, None)
    assert a == b


@pytest.mark.parametrize(
    "changed_field,changed_value",
    [
        ("prompt_version", "v2"),
        ("model", "gpt-other"),
        ("provider", "ollama"),
        ("system", "different sys"),
        ("prompt", "different prompt"),
        ("temperature", 0.7),
        ("max_tokens", 100),
    ],
)
def test_cache_key_sensitive_to_every_component(changed_field, changed_value):
    base = dict(
        prompt_version="v1",
        model="gpt-test",
        provider="openai",
        system="sys",
        prompt="prompt",
        temperature=0.0,
        max_tokens=None,
    )
    changed = dict(base)
    changed[changed_field] = changed_value
    assert llm.cache_key(**base) != llm.cache_key(**changed)


# --- call_llm: caching behaviour -----------------------------------------


def test_call_llm_cache_miss_then_hit(monkeypatch):
    fake_client = FakeClient("the answer")
    monkeypatch.setattr(llm, "_client_for", lambda provider: fake_client)

    first = llm.call_llm("what is X?", prompt_version="v1_stage1")
    second = llm.call_llm("what is X?", prompt_version="v1_stage1")

    assert first.cached is False
    assert second.cached is True
    assert first.text == second.text == "the answer"
    assert len(fake_client.completions.calls) == 1  # only the miss hit the "network"

    stats = llm.get_stats()
    assert stats["calls"] == 2
    assert stats["hits"] == 1
    assert stats["misses"] == 1
    assert stats["prompt_tokens"] == 10  # only counted once, on the real call
    assert stats["completion_tokens"] == 5


def test_call_llm_different_prompt_is_a_cache_miss(monkeypatch):
    fake_client = FakeClient("answer")
    monkeypatch.setattr(llm, "_client_for", lambda provider: fake_client)

    llm.call_llm("question A", prompt_version="v1_stage1")
    llm.call_llm("question B", prompt_version="v1_stage1")

    assert len(fake_client.completions.calls) == 2


def test_call_llm_writes_an_auditable_cache_file(monkeypatch):
    fake_client = FakeClient("the answer")
    monkeypatch.setattr(llm, "_client_for", lambda provider: fake_client)

    llm.call_llm("what is X?", prompt_version="v1_stage1", system="be terse")

    cache_files = list((llm._cache_root() / "v1_stage1").glob("*.json"))
    assert len(cache_files) == 1
    record = json.loads(cache_files[0].read_text(encoding="utf-8"))
    assert record["text"] == "the answer"
    assert record["prompt"] == "what is X?"
    assert record["system"] == "be terse"
    assert record["model"] == "gpt-test"
    assert record["provider"] == "openai"
    assert "cached_at" in record


def test_call_llm_cache_disabled_always_calls_through(monkeypatch):
    llm.get_settings().llm_cache_enabled = False
    fake_client = FakeClient("answer")
    monkeypatch.setattr(llm, "_client_for", lambda provider: fake_client)

    llm.call_llm("same question", prompt_version="v1_stage1")
    llm.call_llm("same question", prompt_version="v1_stage1")

    assert len(fake_client.completions.calls) == 2


# --- provider routing ------------------------------------------------------


def test_call_llm_ollama_provider_uses_ollama_model(monkeypatch):
    fake_client = FakeClient("answer")
    monkeypatch.setattr(llm, "_client_for", lambda provider: fake_client)

    result = llm.call_llm("q", prompt_version="v1_stage1", provider="ollama")

    assert result.model == "qwen-test"
    assert result.provider == "ollama"
    assert fake_client.completions.calls[0]["model"] == "qwen-test"


def test_call_llm_unknown_provider_raises():
    with pytest.raises(ValueError):
        llm.call_llm("q", prompt_version="v1_stage1", provider="anthropic")


def test_call_llm_missing_openai_model_raises_before_any_network_call(monkeypatch):
    llm.get_settings().openai_model = None
    calls = []
    monkeypatch.setattr(llm, "_client_for", lambda provider: calls.append(provider))

    with pytest.raises(ValueError):
        llm.call_llm("q", prompt_version="v1_stage1")

    assert calls == []  # never got as far as building a client


def test_call_llm_model_override_bypasses_provider_default(monkeypatch):
    # style_check.py's judge passes model=settings.openai_judge_model so the
    # judge call never uses the main model, and never requires openai_model
    # to be set at all.
    llm.get_settings().openai_model = None
    fake_client = FakeClient("verdict")
    monkeypatch.setattr(llm, "_client_for", lambda provider: fake_client)

    result = llm.call_llm("q", prompt_version="v1_style_judge", model="gpt-judge")

    assert result.model == "gpt-judge"
    assert fake_client.completions.calls[0]["model"] == "gpt-judge"


def test_call_llm_model_override_changes_the_cache_key(monkeypatch):
    fake_client = FakeClient("answer")
    monkeypatch.setattr(llm, "_client_for", lambda provider: fake_client)

    llm.call_llm("q", prompt_version="v1_style_judge", model="gpt-a")
    llm.call_llm("q", prompt_version="v1_style_judge", model="gpt-b")

    assert len(fake_client.completions.calls) == 2  # different cache entries


# --- stats -----------------------------------------------------------------


def test_reset_stats_zeroes_everything(monkeypatch):
    fake_client = FakeClient("answer")
    monkeypatch.setattr(llm, "_client_for", lambda provider: fake_client)
    llm.call_llm("q", prompt_version="v1_stage1")

    llm.reset_stats()

    assert llm.get_stats() == {
        "calls": 0,
        "hits": 0,
        "misses": 0,
        "prompt_tokens": 0,
        "completion_tokens": 0,
    }
