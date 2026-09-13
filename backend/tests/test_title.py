"""Tests for pipeline/title.py's prompt rendering, the fallback truncation,
and generate_title() (LLM call monkeypatched -- no real network in this
file). CLAUDE.md: test pipeline/ functions, skip route tests."""

from app.pipeline import title


def test_render_title_prompt_substitutes_question():
    prompt = title.render_title_prompt("What is a noun?")
    assert "What is a noun?" in prompt


# --- _fallback_title -------------------------------------------------------


def test_fallback_title_truncates_long_question():
    result = title._fallback_title("What is the difference between a noun and a pronoun in English grammar?")
    assert result == "What is the difference between a…"


def test_fallback_title_keeps_short_question_untruncated():
    result = title._fallback_title("What is a noun?")
    assert result == "What is a noun?"


def test_fallback_title_handles_empty_question():
    assert title._fallback_title("   ") == "New conversation"


# --- generate_title (LLM call monkeypatched) --------------------------------


def test_generate_title_uses_llm_result(monkeypatch):
    captured = {}

    def fake_call_llm(prompt, *, prompt_version, provider="openai", **kwargs):
        captured["prompt_version"] = prompt_version
        captured["provider"] = provider
        from app.pipeline.llm import LLMResult

        return LLMResult(
            text='  "Nouns and naming words"  \n',
            model="gpt-test",
            provider=provider,
            prompt_version=prompt_version,
            cached=False,
            prompt_tokens=10,
            completion_tokens=4,
        )

    monkeypatch.setattr(title, "call_llm", fake_call_llm)

    result = title.generate_title("What is a noun?")

    assert result == "Nouns and naming words"  # stripped of whitespace and quotes
    assert captured["prompt_version"] == title.TITLE_PROMPT_VERSION
    assert captured["provider"] == "openai"


def test_generate_title_falls_back_on_llm_error(monkeypatch):
    def fake_call_llm(prompt, *, prompt_version, provider="openai", **kwargs):
        raise RuntimeError("simulated API failure")

    monkeypatch.setattr(title, "call_llm", fake_call_llm)

    result = title.generate_title("What is a noun?")

    assert result == "What is a noun?"  # _fallback_title, question is short


def test_generate_title_falls_back_on_empty_llm_response(monkeypatch):
    def fake_call_llm(prompt, *, prompt_version, provider="openai", **kwargs):
        from app.pipeline.llm import LLMResult

        return LLMResult(
            text="   ",
            model="gpt-test",
            provider=provider,
            prompt_version=prompt_version,
            cached=False,
            prompt_tokens=10,
            completion_tokens=0,
        )

    monkeypatch.setattr(title, "call_llm", fake_call_llm)

    result = title.generate_title("What is a noun?")

    assert result == "What is a noun?"
