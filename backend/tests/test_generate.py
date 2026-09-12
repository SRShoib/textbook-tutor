"""Tests for pipeline/generate.py's pure prompt-assembly functions, plus a
monkeypatched-LLM test for generate_stage1 itself (no real network call).
CLAUDE.md: test pipeline/ functions, skip route tests."""

import pytest

from app.models.chunk import Chunk, ChunkType
from app.pipeline import generate
from app.pipeline.llm import LLMResult


def make_chunk(text: str) -> Chunk:
    # A plain in-memory instance — no DB session needed, same as
    # test_ingest.py's fixtures.
    return Chunk(
        book_id=None,
        lesson_id="u1-s1",
        unit=1,
        lesson_no=1,
        lesson_title="Our School",
        page=7,
        type=ChunkType.PASSAGE,
        text=text,
        embedding=[0.0] * 1024,
        sparse={},
    )


# --- format_context ----------------------------------------------------


def test_format_context_numbers_each_chunk_in_order():
    chunks = [make_chunk("Unit 1, Lesson 1: Our School\nFirst passage."), make_chunk("Unit 1, Lesson 2: Our School\nSecond passage.")]
    result = generate.format_context(chunks)
    assert result == (
        "[1] Unit 1, Lesson 1: Our School\nFirst passage."
        "\n\n"
        "[2] Unit 1, Lesson 2: Our School\nSecond passage."
    )


def test_format_context_empty_list():
    assert generate.format_context([]) == ""


# --- render_stage1_prompt ------------------------------------------------


def test_render_stage1_prompt_substitutes_question_and_context():
    prompt = generate.render_stage1_prompt("What is the capital?", "[1] some passage")
    assert "What is the capital?" in prompt
    assert "[1] some passage" in prompt


def test_load_prompt_v1_stage1_has_no_grade_variable():
    # Stage 1 must not know about grade — see generate.py's module docstring
    # on why that matters for checking stage 2 later.
    template = generate.load_prompt(generate.STAGE1_PROMPT_VERSION)
    assert "{question}" in template
    assert "{context}" in template
    assert "{grade}" not in template


# --- generate_stage1 (LLM call is monkeypatched, no network) ---------------


def test_generate_stage1_returns_answer_and_llm_metadata(monkeypatch):
    captured = {}

    def fake_call_llm(prompt, *, prompt_version, provider="openai", **kwargs):
        captured["prompt"] = prompt
        captured["prompt_version"] = prompt_version
        captured["provider"] = provider
        return LLMResult(
            text="Sumon went to school on the first day.",
            model="gpt-test",
            provider=provider,
            prompt_version=prompt_version,
            cached=False,
            prompt_tokens=20,
            completion_tokens=8,
        )

    monkeypatch.setattr(generate, "call_llm", fake_call_llm)

    chunks = [make_chunk("Unit 1, Lesson 1: Our School\nSumon went to school on the first day.")]
    result = generate.generate_stage1("What did Sumon do?", chunks)

    assert result.answer == "Sumon went to school on the first day."
    assert result.llm.cached is False
    assert captured["prompt_version"] == generate.STAGE1_PROMPT_VERSION
    assert "What did Sumon do?" in captured["prompt"]
    assert "Sumon went to school" in captured["prompt"]
    assert captured["provider"] == "openai"


def test_generate_stage1_passes_through_provider(monkeypatch):
    captured = {}

    def fake_call_llm(prompt, *, prompt_version, provider="openai", **kwargs):
        captured["provider"] = provider
        return LLMResult(
            text="answer", model="qwen-test", provider=provider,
            prompt_version=prompt_version, cached=False, prompt_tokens=1, completion_tokens=1,
        )

    monkeypatch.setattr(generate, "call_llm", fake_call_llm)
    generate.generate_stage1("q", [make_chunk("some text")], provider="ollama")

    assert captured["provider"] == "ollama"
