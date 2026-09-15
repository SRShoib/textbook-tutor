"""Tests for pipeline/generate.py's pure prompt-assembly functions, plus a
monkeypatched-LLM test for generate_stage1 itself (no real network call).
CLAUDE.md: test pipeline/ functions, skip route tests."""

import re

import pytest

from app.core.config import get_settings
from app.models.chunk import Chunk, ChunkType
from app.pipeline import generate
from app.pipeline.llm import LLMResult


@pytest.fixture(autouse=True)
def restore_bangla_mode():
    settings = get_settings()
    original = settings.bangla_mode
    yield
    settings.bangla_mode = original


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


# --- stage 2: style-guide loaders ---------------------------------------


@pytest.fixture(autouse=True)
def restore_eval_mode():
    from app.core.config import get_settings

    settings = get_settings()
    original = settings.eval_mode
    yield
    settings.eval_mode = original


def test_load_prompt_v1_stage2_has_grade_variable():
    # The mirror image of stage 1's guarantee: stage 2 IS grade-aware.
    template = generate.load_prompt(generate.STAGE2_PROMPT_VERSION)
    assert "{grade}" in template
    assert "{question}" in template
    assert "{stage1_answer}" in template
    assert "{style_rules}" in template
    assert "{fewshot}" in template


# --- bangla_mode="echo": stage2_prompt_version() + v2_stage2.txt ---------


def test_stage2_prompt_version_default_is_light():
    get_settings().bangla_mode = "light"
    assert generate.stage2_prompt_version() == generate.STAGE2_PROMPT_VERSION


def test_stage2_prompt_version_echo():
    get_settings().bangla_mode = "echo"
    assert generate.stage2_prompt_version() == generate.STAGE2_ECHO_PROMPT_VERSION


def test_v2_stage2_has_the_same_placeholders_as_v1():
    # Same call signature either way -- render_stage2_prompt() must not need
    # to know which template it loaded.
    v1_placeholders = set(re.findall(r"\{(\w+)\}", generate.load_prompt(generate.STAGE2_PROMPT_VERSION)))
    v2_placeholders = set(re.findall(r"\{(\w+)\}", generate.load_prompt(generate.STAGE2_ECHO_PROMPT_VERSION)))
    assert v1_placeholders == v2_placeholders


def test_render_stage2_prompt_light_mode_keeps_single_restatement_note():
    get_settings().bangla_mode = "light"
    prompt = generate.render_stage2_prompt("What is a noun?", "A noun is a naming word.", grade=5)
    assert "restate the student's question once" in prompt


def test_render_stage2_prompt_echo_mode_asks_for_a_bangla_echo_per_sentence():
    get_settings().bangla_mode = "echo"
    prompt = generate.render_stage2_prompt("What is a noun?", "A noun is a naming word.", grade=5)
    assert "after every sentence of your explanation" in prompt
    assert "Bangla script only" in prompt


def test_load_style_rules_drops_evidence_quotes_but_keeps_rules():
    rules = generate.load_style_rules()
    # The actual rule statements survive.
    assert "Never define-and-move-on" in rules
    assert "Address form" in rules  # section 4 tone table
    assert "Abstract dictionary definitions" in rules  # section 5 forbidden patterns
    # The quoted transcript evidence does not.
    assert "Evidence:" not in rules
    assert "Sekhane notun bektiti ke" not in rules
    assert "PBxbCgjFyQ8" not in rules


def test_load_style_rules_drops_document_authoring_meta_text():
    # "Fill each slot with what you actually observe" instructs whoever
    # edits style_guide.md, not the model answering a student.
    rules = generate.load_style_rules()
    assert "Fill each slot" not in rules


def test_load_grade_profile_grade5_has_measured_numbers():
    profile = generate.load_grade_profile(5)
    assert "14 words" in profile
    assert "3.0" in profile


def test_load_style_rules_includes_opening_move_by_default():
    rules = generate.load_style_rules()
    assert "Opening move" in rules
    assert "song" in rules.lower()  # the observed pattern, described as evidence


def test_load_style_rules_can_drop_opening_move():
    # style_guide.md's §3.1 describes how a teacher opens a whole LESSON,
    # once — applying it to every chatbot turn made stage 2 replay "let's
    # sing a song" on follow-up questions (caught in a live run). Dropping
    # it must not disturb the other structural rules.
    rules = generate.load_style_rules(include_opening_move=False)
    assert "Opening move" not in rules
    assert "song" not in rules.lower()
    assert "Never define-and-move-on" in rules  # §3.2 untouched
    assert "Address form" in rules  # §4 untouched


def test_load_grade_profile_returns_empty_for_unfilled_grades():
    # Only grade 5 is filled in style_guide.md today.
    assert generate.load_grade_profile(3) == ""
    assert generate.load_grade_profile(8) == ""


def test_load_fewshot_drops_textbook_passage_field():
    examples = generate.load_fewshot(5)
    assert len(examples) == 5
    for ex in examples:
        assert not hasattr(ex, "textbook_passage")
    formatted = generate.format_fewshot(examples)
    # This example's teacher_answer names a character absent from its own
    # textbook_passage (different textbook edition) — showing the passage
    # would model the exact fact-invention stage 2 must not do.
    assert "town hall language club" not in formatted
    assert "Andy Smith" in formatted


def test_load_fewshot_drops_synthetic_rows_in_eval_mode(tmp_path, monkeypatch):
    from app.core.config import get_settings

    fixture = tmp_path / "fewshot.jsonl"
    fixture.write_text(
        "\n".join(
            [
                '{"id": "real1", "grade": 5, "question": "q1", "textbook_passage": "p1", "teacher_answer": "a1", "source_id": "V-1", "provenance": "real"}',
                '{"id": "fake1", "grade": 5, "question": "q2", "textbook_passage": "p2", "teacher_answer": "a2", "source_id": "V-2", "provenance": "synthetic"}',
            ]
        ),
        encoding="utf-8",
    )
    monkeypatch.setattr(generate, "_FEWSHOT_PATH", fixture)

    get_settings().eval_mode = True
    rows = generate.load_fewshot(5)
    assert [r.provenance for r in rows] == ["real"]

    get_settings().eval_mode = False
    rows = generate.load_fewshot(5)
    assert len(rows) == 2


def test_load_fewshot_raises_when_filtering_empties_the_list(tmp_path, monkeypatch):
    from app.core.config import get_settings

    fixture = tmp_path / "fewshot.jsonl"
    fixture.write_text(
        '{"id": "fake1", "grade": 5, "question": "q", "textbook_passage": "p", "teacher_answer": "a", "source_id": "V-2", "provenance": "synthetic"}',
        encoding="utf-8",
    )
    monkeypatch.setattr(generate, "_FEWSHOT_PATH", fixture)
    get_settings().eval_mode = True

    with pytest.raises(ValueError):
        generate.load_fewshot(5)


def test_load_fewshot_falls_back_to_all_rows_when_grade_has_no_match():
    # All 5 rows are grade 5; asking for grade 3 should fall back rather
    # than return an empty (voiceless) list.
    rows = generate.load_fewshot(3)
    assert len(rows) == 5


# --- render_stage2_prompt / generate_stage2 -----------------------------


def test_render_stage2_prompt_never_contains_chunk_context():
    # Stage 2 must not be able to add a book fact stage 1 didn't produce;
    # the structural guarantee is that it never receives context_chunks.
    prompt = generate.render_stage2_prompt(
        "What is a noun?", "A noun is a naming word.", grade=5
    )
    assert "context_chunks" not in prompt
    assert "town hall language club" not in prompt  # a fewshot passage


def test_render_stage2_prompt_includes_feedback_on_retry():
    prompt = generate.render_stage2_prompt(
        "What is a noun?",
        "A noun is a naming word.",
        grade=5,
        feedback="too many long sentences",
    )
    assert "too many long sentences" in prompt


def test_render_stage2_prompt_without_feedback_has_no_placeholder_leak():
    prompt = generate.render_stage2_prompt(
        "What is a noun?", "A noun is a naming word.", grade=5
    )
    assert "{feedback}" not in prompt


def test_render_stage2_prompt_first_turn_includes_opening_move_and_forbids_song():
    prompt = generate.render_stage2_prompt(
        "What is a noun?", "A noun is a naming word.", grade=5, is_first_turn=True
    )
    assert "first message in this conversation" in prompt
    assert "never include a song" in prompt.lower()


def test_render_stage2_prompt_follow_up_turn_drops_opening_move_but_still_forbids_song():
    # The exact scenario caught in a live run: "give me more examples" right
    # after a first question must not re-open with a greeting or a song.
    prompt = generate.render_stage2_prompt(
        "give me more examples", "More nouns: book, pen.", grade=5, is_first_turn=False
    )
    assert "follow-up in an ongoing conversation" in prompt
    assert "Opening move" not in prompt
    assert "never include a song" in prompt.lower()


def test_render_stage2_prompt_defaults_to_first_turn():
    prompt = generate.render_stage2_prompt(
        "What is a noun?", "A noun is a naming word.", grade=5
    )
    assert "first message in this conversation" in prompt


def test_render_stage2_prompt_first_turn_names_the_real_source_citation():
    # Phase 6 fix: stage 2 must be told the true citation, not left to
    # invent one — a 120-question dev run showed it always fabricated one
    # of two fake citations when this wasn't threaded through.
    prompt = generate.render_stage2_prompt(
        "What is a noun?", "A noun is a naming word.", grade=5, source_citation="Unit 4, Lesson 5, page 22"
    )
    assert "Unit 4, Lesson 5, page 22" in prompt
    assert "use exactly that, do not guess or invent a different one" in prompt


def test_render_stage2_prompt_follow_up_turn_omits_source_citation():
    # No opening move on a follow-up at all, so naming a citation (real or
    # otherwise) would contradict "do not name the unit, lesson or page again".
    prompt = generate.render_stage2_prompt(
        "give me more examples", "More nouns: book, pen.", grade=5,
        is_first_turn=False, source_citation="Unit 4, Lesson 5, page 22",
    )
    assert "Unit 4, Lesson 5, page 22" not in prompt


def test_render_stage2_prompt_first_turn_falls_back_without_source_citation():
    # Defensive default only — route_after_retrieve guarantees a real
    # citation is always passed in practice (see graph.py's stage2_node).
    prompt = generate.render_stage2_prompt(
        "What is a noun?", "A noun is a naming word.", grade=5
    )
    assert "the source lesson" in prompt


def test_generate_stage2_returns_answer_and_llm_metadata(monkeypatch):
    captured = {}

    def fake_call_llm(prompt, *, prompt_version, provider="openai", **kwargs):
        captured["prompt"] = prompt
        captured["prompt_version"] = prompt_version
        return LLMResult(
            text="Dear students, a noun is a naming word.",
            model="gpt-test",
            provider=provider,
            prompt_version=prompt_version,
            cached=False,
            prompt_tokens=30,
            completion_tokens=10,
        )

    monkeypatch.setattr(generate, "call_llm", fake_call_llm)
    get_settings().bangla_mode = "light"

    result = generate.generate_stage2(
        "What is a noun?", "A noun is a naming word.", grade=5
    )

    assert result.answer == "Dear students, a noun is a naming word."
    assert captured["prompt_version"] == generate.STAGE2_PROMPT_VERSION
    assert "What is a noun?" in captured["prompt"]
    assert "A noun is a naming word." in captured["prompt"]


def test_generate_stage2_echo_mode_uses_the_echo_prompt_version(monkeypatch):
    captured = {}

    def fake_call_llm(prompt, *, prompt_version, provider="openai", **kwargs):
        captured["prompt"] = prompt
        captured["prompt_version"] = prompt_version
        return LLMResult(
            text="answer", model="gpt-test", provider=provider,
            prompt_version=prompt_version, cached=False, prompt_tokens=1, completion_tokens=1,
        )

    monkeypatch.setattr(generate, "call_llm", fake_call_llm)
    get_settings().bangla_mode = "echo"

    generate.generate_stage2("What is a noun?", "A noun is a naming word.", grade=5)

    assert captured["prompt_version"] == generate.STAGE2_ECHO_PROMPT_VERSION
    assert "after every sentence of your explanation" in captured["prompt"]


def test_generate_stage2_passes_through_is_first_turn(monkeypatch):
    captured = {}

    def fake_call_llm(prompt, *, prompt_version, provider="openai", **kwargs):
        captured["prompt"] = prompt
        return LLMResult(
            text="answer", model="gpt-test", provider=provider,
            prompt_version=prompt_version, cached=False, prompt_tokens=1, completion_tokens=1,
        )

    monkeypatch.setattr(generate, "call_llm", fake_call_llm)

    generate.generate_stage2(
        "give me more examples", "More nouns.", grade=5, is_first_turn=False
    )

    assert "follow-up in an ongoing conversation" in captured["prompt"]
    assert "Opening move" not in captured["prompt"]


def test_generate_stage2_passes_through_source_citation(monkeypatch):
    captured = {}

    def fake_call_llm(prompt, *, prompt_version, provider="openai", **kwargs):
        captured["prompt"] = prompt
        return LLMResult(
            text="answer", model="gpt-test", provider=provider,
            prompt_version=prompt_version, cached=False, prompt_tokens=1, completion_tokens=1,
        )

    monkeypatch.setattr(generate, "call_llm", fake_call_llm)

    generate.generate_stage2(
        "What is a noun?", "A noun is a naming word.", grade=5, source_citation="Unit 4, Lesson 5, page 22"
    )

    assert "Unit 4, Lesson 5, page 22" in captured["prompt"]
