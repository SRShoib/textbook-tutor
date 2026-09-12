"""Tests for pipeline/rewrite.py's needs_rewrite heuristic, prompt assembly,
and rewrite_question (LLM call monkeypatched — no real network or database
in this file; load_history touches the DB and is exercised through
test_graph.py's end-to-end monkeypatching instead).
CLAUDE.md: test pipeline/ functions, skip route tests."""

from app.models.message import Message, MessageRole
from app.pipeline import rewrite
from app.pipeline.llm import LLMResult


def make_message(role: MessageRole, content: str) -> Message:
    return Message(session_id=None, role=role, content=content)


SOME_HISTORY = [
    make_message(MessageRole.USER, "What is a noun?"),
    make_message(MessageRole.ASSISTANT, "A noun is a naming word."),
]


# --- needs_rewrite -------------------------------------------------------


def test_needs_rewrite_false_when_history_is_empty():
    # A session's first turn has nothing to resolve a pronoun/continuation
    # against, however short or pronoun-laden the question looks.
    assert rewrite.needs_rewrite("What is it?", []) is False


def test_needs_rewrite_true_for_short_follow_up():
    assert rewrite.needs_rewrite("give me more examples", SOME_HISTORY) is True


def test_needs_rewrite_true_for_pronoun_reference():
    assert rewrite.needs_rewrite("Can you explain it in a different way please?", SOME_HISTORY) is True


def test_needs_rewrite_true_for_phrase_opener():
    assert rewrite.needs_rewrite("What about verbs instead?", SOME_HISTORY) is True


def test_needs_rewrite_false_for_a_clear_standalone_question():
    assert (
        rewrite.needs_rewrite("How do you form the past tense of regular verbs in English?", SOME_HISTORY)
        is False
    )


def test_needs_rewrite_false_for_empty_question():
    assert rewrite.needs_rewrite("   ", SOME_HISTORY) is False


# --- format_history / render_rewrite_prompt -------------------------------


def test_format_history_labels_speakers():
    text = rewrite.format_history(SOME_HISTORY)
    assert text == "Student: What is a noun?\nTeacher: A noun is a naming word."


def test_render_rewrite_prompt_substitutes_history_and_question():
    prompt = rewrite.render_rewrite_prompt("give me more examples", SOME_HISTORY)
    assert "What is a noun?" in prompt
    assert "give me more examples" in prompt


# --- rewrite_question (LLM call monkeypatched) ----------------------------


def test_rewrite_question_skips_llm_when_standalone():
    calls = []

    def fake_call_llm(prompt, *, prompt_version, provider="openai", **kwargs):
        calls.append(prompt)
        raise AssertionError("call_llm must not be called for a standalone question")

    import app.pipeline.rewrite as rewrite_module

    original = rewrite_module.call_llm
    rewrite_module.call_llm = fake_call_llm
    try:
        result = rewrite.rewrite_question(
            "How do you form the past tense of regular verbs in English?", SOME_HISTORY
        )
    finally:
        rewrite_module.call_llm = original

    assert result.rewritten is False
    assert result.llm is None
    assert result.query == "How do you form the past tense of regular verbs in English?"
    assert calls == []


def test_rewrite_question_calls_llm_for_a_follow_up(monkeypatch):
    captured = {}

    def fake_call_llm(prompt, *, prompt_version, provider="openai", **kwargs):
        captured["prompt"] = prompt
        captured["prompt_version"] = prompt_version
        return LLMResult(
            text="  Give more examples of nouns.  \n",
            model="gpt-test",
            provider=provider,
            prompt_version=prompt_version,
            cached=False,
            prompt_tokens=15,
            completion_tokens=6,
        )

    monkeypatch.setattr(rewrite, "call_llm", fake_call_llm)

    result = rewrite.rewrite_question("give me more examples", SOME_HISTORY)

    assert result.rewritten is True
    assert result.query == "Give more examples of nouns."  # stripped
    assert captured["prompt_version"] == rewrite.REWRITE_PROMPT_VERSION
    assert "What is a noun?" in captured["prompt"]
