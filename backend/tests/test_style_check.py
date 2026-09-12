"""Tests for pipeline/style_check.py's pure checks, the LLM judge (network
call monkeypatched), and check_style's orchestration (book_vocabulary and
run_judge monkeypatched — no real database or network in this file).
CLAUDE.md: test pipeline/ functions, skip route tests."""

import importlib.util
import sys
import uuid
from pathlib import Path

import pytest

from app.core.config import get_settings
from app.pipeline import style_check
from app.pipeline.generate import FewshotExample
from app.pipeline.llm import LLMResult

_REPO_ROOT = Path(__file__).resolve().parents[2]


@pytest.fixture(autouse=True)
def restore_style_settings():
    settings = get_settings()
    original = {
        "style_max_sentence_words": settings.style_max_sentence_words,
        "style_fk_min": settings.style_fk_min,
        "style_fk_max": settings.style_fk_max,
        "style_vocab_coverage_min": settings.style_vocab_coverage_min,
        "openai_judge_model": settings.openai_judge_model,
    }
    yield
    for k, v in original.items():
        setattr(settings, k, v)


# --- config numbers match the measured style guide ----------------------


def test_style_thresholds_match_style_guide_section_2():
    # style_guide.md section 2: "14 words" (p95), "3.0 - 4.5" (FK target).
    # These live in config.py, not parsed from the prose table there, but
    # this test is the paper trail linking the two.
    text = (_REPO_ROOT / "data" / "style_guide" / "style_guide.md").read_text(encoding="utf-8")
    settings = get_settings()
    assert f"{settings.style_max_sentence_words} words" in text
    assert "3.0" in text and "4.5" in text
    assert settings.style_fk_min == 3.0
    assert settings.style_fk_max == 4.5


# --- segmentation matches tools/style_guide/measure_style.py -------------


def _load_measure_style():
    path = _REPO_ROOT / "tools" / "style_guide" / "measure_style.py"
    spec = importlib.util.spec_from_file_location("measure_style", path)
    module = importlib.util.module_from_spec(spec)
    # FileStats is a @dataclass with string annotations; dataclasses resolves
    # those by looking the module up in sys.modules, so it must be
    # registered before exec_module runs the class body.
    sys.modules["measure_style"] = module
    spec.loader.exec_module(module)
    return module


_FIXTURE_TEXT = (
    "Dear students, how are you? I hope you all are fine and safe today. "
    "fan means that keep you cool in summer. Jaaki na tomake garome thanda rakhe. "
    "Now, dear students, what have we learned today?"
)


def test_sentence_and_word_splitting_matches_measure_style_py():
    measure_style = _load_measure_style()
    assert style_check.sentences(_FIXTURE_TEXT) == measure_style.sentences(_FIXTURE_TEXT)
    assert style_check.words(_FIXTURE_TEXT) == measure_style.words(_FIXTURE_TEXT)
    assert style_check.strip_bangla_keep_punct(_FIXTURE_TEXT) == measure_style.strip_bangla_keep_punct(
        _FIXTURE_TEXT
    )


# --- is_scaffolding_sentence / content_sentences (Phase 6 fix, moved from
# verify.py 2026-09-13 -- see module docstring on why the same scaffolding
# that inflated verify_answer() also inflated the checks below) -----------


def test_is_scaffolding_sentence_matches_documented_patterns():
    assert style_check.is_scaffolding_sentence("Dear students, today we are in Unit 3, Lesson 2.") is True
    assert style_check.is_scaffolding_sentence("I'll repeat that.") is True
    assert style_check.is_scaffolding_sentence("Do you understand?") is True


def test_is_scaffolding_sentence_leaves_factual_sentences_alone():
    assert style_check.is_scaffolding_sentence("Rina likes to read science fiction books.") is False


def test_content_sentences_drops_scaffolding_keeps_the_fact():
    answer = (
        "Dear students, I hope you are all doing well today! "
        "We are in Unit 6, Lesson 2, page 36. "
        'Now, to answer your question, "What is the capital city of Indonesia?" '
        "The capital city of Indonesia is Jakarta. "
        "I'll repeat that. "
        "Do you understand?"
    )
    assert style_check.content_sentences(answer) == ["The capital city of Indonesia is Jakarta."]


# --- check_sentence_length ------------------------------------------------


def test_check_sentence_length_reports_max_and_mean():
    answer = "A noun is a naming word. It names a person, place, or thing."
    max_words, mean_words = style_check.check_sentence_length(answer)
    assert max_words == 7  # "It names a person, place, or thing." (commas aren't word chars)
    assert mean_words == pytest.approx(6.5)


def test_check_sentence_length_empty_answer_is_zero():
    assert style_check.check_sentence_length("") == (0, 0.0)


# --- check_vocab_coverage -------------------------------------------------


def test_check_vocab_coverage_all_words_known():
    vocab = {"a", "noun", "is", "naming", "word"}
    coverage, oov = style_check.check_vocab_coverage("A noun is a naming word.", vocab)
    assert coverage == 1.0
    assert oov == []


def test_check_vocab_coverage_reports_unfamiliar_words_in_first_seen_order():
    vocab = {"a", "noun", "is"}
    coverage, oov = style_check.check_vocab_coverage("A noun is a zephyr, a quixotic zephyr.", vocab)
    assert oov == ["zephyr", "quixotic"]
    assert 0 < coverage < 1


def test_check_vocab_coverage_strips_bangla_before_counting():
    # Bangla-*script* text is stripped before word-matching. Romanised Bangla
    # ("Jaaki na tomake...", Latin letters) is NOT stripped — that's the same
    # Whisper-transliteration artifact style_guide.md section 2 documents,
    # and exactly why stage 2's prompt requires Bangla script, never Latin
    # transliteration (see generate.py's module docstring).
    vocab = {"fan", "means", "keep", "you", "cool", "in", "summer"}
    coverage, oov = style_check.check_vocab_coverage(
        "fan means keep you cool in summer. একটি উদাহরণ।", vocab
    )
    assert oov == []
    assert coverage == 1.0


# --- check_fk_grade --------------------------------------------------------


def test_check_fk_grade_none_below_30_words():
    assert style_check.check_fk_grade("A short answer.") is None


def test_check_fk_grade_scores_a_longer_english_answer():
    answer = (
        "A noun is a naming word. It names a person, a place, or a thing. "
        "For example, book, school, and Dhaka are all nouns. We use nouns "
        "every day when we talk about the world around us in class."
    )
    fk = style_check.check_fk_grade(answer)
    assert fk is not None
    assert -5 <= fk <= 25


# --- judge prompt + parsing ------------------------------------------------


def test_render_judge_prompt_substitutes_grade_and_answer():
    prompt = style_check.render_judge_prompt("A noun is a naming word.", grade=5)
    assert "Class 5" in prompt
    assert "A noun is a naming word." in prompt
    assert "{grade}" not in prompt
    assert '{"suitable"' in prompt  # doubled braces in the template survived .format()


def test_parse_judge_verdict_plain_json():
    verdict = style_check._parse_judge_verdict('{"suitable": true, "reason": "clear and simple"}')
    assert verdict.suitable is True
    assert verdict.reason == "clear and simple"


def test_parse_judge_verdict_strips_markdown_fence():
    verdict = style_check._parse_judge_verdict(
        '```json\n{"suitable": false, "reason": "too many hard words"}\n```'
    )
    assert verdict.suitable is False
    assert verdict.reason == "too many hard words"


def test_run_judge_uses_judge_model_when_configured(monkeypatch):
    captured = {}

    def fake_call_llm(prompt, *, prompt_version, provider="openai", model=None, **kwargs):
        captured["model"] = model
        captured["prompt_version"] = prompt_version
        return LLMResult(
            text='{"suitable": true, "reason": "fine"}',
            model=model or "gpt-main",
            provider=provider,
            prompt_version=prompt_version,
            cached=False,
            prompt_tokens=5,
            completion_tokens=5,
        )

    monkeypatch.setattr(style_check, "call_llm", fake_call_llm)
    get_settings().openai_judge_model = "gpt-judge"

    verdict = style_check.run_judge("A noun is a naming word.", grade=5)

    assert captured["model"] == "gpt-judge"
    assert captured["prompt_version"] == style_check.STYLE_JUDGE_PROMPT_VERSION
    assert verdict.suitable is True


def test_run_judge_falls_back_to_main_model_when_judge_model_unset(monkeypatch):
    captured = {}

    def fake_call_llm(prompt, *, prompt_version, provider="openai", model=None, **kwargs):
        captured["model"] = model
        return LLMResult(
            text='{"suitable": true, "reason": "fine"}', model=model or "gpt-main",
            provider=provider, prompt_version=prompt_version, cached=False,
            prompt_tokens=1, completion_tokens=1,
        )

    monkeypatch.setattr(style_check, "call_llm", fake_call_llm)
    get_settings().openai_judge_model = None

    style_check.run_judge("A noun is a naming word.", grade=5)

    assert captured["model"] is None  # call_llm falls back to _model_for(provider) itself


def test_run_judge_ollama_provider_ignores_openai_judge_model(monkeypatch):
    captured = {}

    def fake_call_llm(prompt, *, prompt_version, provider="openai", model=None, **kwargs):
        captured["model"] = model
        return LLMResult(
            text='{"suitable": true, "reason": "fine"}', model=model or "qwen-main",
            provider=provider, prompt_version=prompt_version, cached=False,
            prompt_tokens=1, completion_tokens=1,
        )

    monkeypatch.setattr(style_check, "call_llm", fake_call_llm)
    get_settings().openai_judge_model = "gpt-judge"

    style_check.run_judge("A noun is a naming word.", grade=5, provider="ollama")

    assert captured["model"] is None  # no ablation-specific judge model exists yet


# --- check_style orchestration (book_vocabulary + run_judge monkeypatched) -


FIXED_VOCAB = {
    "a", "noun", "is", "naming", "word", "it", "names", "person", "place", "or", "thing",
}


@pytest.mark.asyncio
async def test_check_style_passes_and_runs_judge_when_numeric_checks_pass(monkeypatch):
    async def fake_book_vocabulary(book_id, grade):
        return FIXED_VOCAB

    def fake_run_judge(answer, *, grade, provider="openai"):
        return style_check._JudgeVerdict(suitable=True, reason="clear")

    monkeypatch.setattr(style_check, "book_vocabulary", fake_book_vocabulary)
    monkeypatch.setattr(style_check, "run_judge", fake_run_judge)

    report = await style_check.check_style(
        "A noun is a naming word. It names a person, place, or thing.",
        grade=5,
        book_id=uuid.uuid4(),
    )

    assert report.passed is True
    assert report.judge_ran is True
    assert report.judge_suitable is True
    assert report.failures == []


@pytest.mark.asyncio
async def test_check_style_fails_on_sentence_length_and_skips_judge(monkeypatch):
    judge_calls = []

    async def fake_book_vocabulary(book_id, grade):
        return FIXED_VOCAB

    def fake_run_judge(answer, *, grade, provider="openai"):
        judge_calls.append(answer)
        return style_check._JudgeVerdict(suitable=True, reason="clear")

    monkeypatch.setattr(style_check, "book_vocabulary", fake_book_vocabulary)
    monkeypatch.setattr(style_check, "run_judge", fake_run_judge)
    get_settings().style_max_sentence_words = 3

    report = await style_check.check_style(
        "A noun is a naming word.", grade=5, book_id=uuid.uuid4()
    )

    assert report.passed is False
    assert report.judge_ran is False  # judge is skipped — numeric failure is enough
    assert judge_calls == []
    assert any("word limit" in f for f in report.failures)


@pytest.mark.asyncio
async def test_check_style_ignores_scaffolding_sentence_length(monkeypatch):
    # Phase 6 fix, 2026-09-13: a long question-echo/citation sentence must
    # not fail the length check on its own — only the actual explanation's
    # length counts. This is the dominant real-world failure this fixed:
    # 54/62 dev-split "too hard" failures were this exact shape.
    async def fake_book_vocabulary(book_id, grade):
        return FIXED_VOCAB | {
            "dear", "students", "hope", "you", "all", "doing", "well", "today",
            "now", "to", "answer", "your", "question", "what", "does", "mean",
        }

    def fake_run_judge(answer, *, grade, provider="openai"):
        return style_check._JudgeVerdict(suitable=True, reason="clear")

    monkeypatch.setattr(style_check, "book_vocabulary", fake_book_vocabulary)
    monkeypatch.setattr(style_check, "run_judge", fake_run_judge)
    get_settings().style_max_sentence_words = 14

    answer = (
        "Dear students, I hope you all are doing well today! "
        'Now, to answer your question, "What does a noun mean and how do we use it in a sentence?" '
        "A noun is a naming word."
    )
    report = await style_check.check_style(answer, grade=5, book_id=uuid.uuid4())

    assert report.passed is True
    assert report.max_sentence_words == 6  # "A noun is a naming word." only
    assert report.failures == []


@pytest.mark.asyncio
async def test_check_style_fails_on_vocab_coverage(monkeypatch):
    async def fake_book_vocabulary(book_id, grade):
        return {"a", "noun", "is"}  # "naming"/"word" etc. are unfamiliar

    monkeypatch.setattr(style_check, "book_vocabulary", fake_book_vocabulary)
    get_settings().style_vocab_coverage_min = 0.9

    report = await style_check.check_style(
        "A noun is a naming word.", grade=5, book_id=uuid.uuid4()
    )

    assert report.passed is False
    assert report.vocab_coverage < 0.9
    assert any("vocabulary" in f for f in report.failures)


@pytest.mark.asyncio
async def test_check_style_judge_rejection_fails_the_report(monkeypatch):
    async def fake_book_vocabulary(book_id, grade):
        return FIXED_VOCAB

    def fake_run_judge(answer, *, grade, provider="openai"):
        return style_check._JudgeVerdict(suitable=False, reason="too formal for a child")

    monkeypatch.setattr(style_check, "book_vocabulary", fake_book_vocabulary)
    monkeypatch.setattr(style_check, "run_judge", fake_run_judge)

    report = await style_check.check_style(
        "A noun is a naming word. It names a person, place, or thing.",
        grade=5,
        book_id=uuid.uuid4(),
    )

    assert report.passed is False
    assert report.judge_suitable is False
    assert "too formal for a child" in report.failures[0]


@pytest.mark.asyncio
async def test_book_vocabulary_drops_the_unit_lesson_header_line():
    # ingest.py prepends "Unit N, Lesson M: Title\n" to every chunk's text —
    # that header shouldn't count as vocabulary the child read.

    class FakeChunkRow:
        def __init__(self, text):
            self.text = text

    class FakeScalars:
        def __init__(self, rows):
            self._rows = rows

        def all(self):
            return self._rows

    class FakeResult:
        def __init__(self, rows):
            self._rows = rows

        def scalars(self):
            return FakeScalars(self._rows)

    class FakeSession:
        async def __aenter__(self):
            return self

        async def __aexit__(self, *exc):
            return False

        async def execute(self, query):
            return FakeResult(["Unit 1, Lesson 1: Our School\nSumon went to school."])

    import app.pipeline.style_check as sc_module

    sc_module._vocab_cache.clear()
    with pytest.MonkeyPatch.context() as mp:
        mp.setattr(sc_module, "AsyncSessionLocal", lambda: FakeSession())
        vocab = await sc_module.book_vocabulary(uuid.uuid4(), grade=3)

    assert "unit" not in vocab
    assert "lesson" not in vocab
    assert "sumon" in vocab
    assert "school" in vocab
