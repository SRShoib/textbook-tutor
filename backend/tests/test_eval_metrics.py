"""Tests for eval/metrics.py: hit-rate math, JSONL round-trip, structured-
output parsing, and ragas_faithfulness's aggregation logic (with RAGAS's
Faithfulness metric itself monkeypatched — no real LLM call, no dependency
on RAGAS's internal prompt templates)."""

import math

import pytest
from pydantic import BaseModel

from eval import metrics


# --- retrieval_hit_rate -----------------------------------------------


def test_retrieval_hit_rate_counts_hits_and_misses():
    records = [
        {"expected_lesson_id": "u1-s1", "retrieved_lesson_ids": ["u1-s1", "u2-s1"]},  # hit
        {"expected_lesson_id": "u3-s1", "retrieved_lesson_ids": ["u1-s1", "u2-s1"]},  # miss
    ]
    rate, n = metrics.retrieval_hit_rate(records)
    assert rate == pytest.approx(0.5)
    assert n == 2


def test_retrieval_hit_rate_excludes_unlabelled_rows_from_denominator():
    records = [
        {"expected_lesson_id": "u1-s1", "retrieved_lesson_ids": ["u1-s1"]},  # hit, labelled
        {"expected_lesson_id": None, "retrieved_lesson_ids": []},  # no ground truth
        {"retrieved_lesson_ids": ["u1-s1"]},  # no expected_lesson_id key at all
    ]
    rate, n = metrics.retrieval_hit_rate(records)
    assert rate == pytest.approx(1.0)
    assert n == 1  # only the one labelled row counts


def test_retrieval_hit_rate_no_labelled_rows_returns_none():
    records = [{"retrieved_lesson_ids": ["u1-s1"]}]
    rate, n = metrics.retrieval_hit_rate(records)
    assert rate is None
    assert n == 0


# --- JSONL round-trip ----------------------------------------------------


def test_write_then_read_jsonl_round_trips(tmp_path):
    records = [{"a": 1, "b": "x"}, {"a": 2, "b": "y"}]
    path = tmp_path / "nested" / "out.jsonl"

    metrics.write_jsonl(path, records)
    result = metrics.read_jsonl(path)

    assert result == records


def test_read_jsonl_skips_blank_lines(tmp_path):
    path = tmp_path / "out.jsonl"
    path.write_text('{"a": 1}\n\n{"a": 2}\n', encoding="utf-8")

    result = metrics.read_jsonl(path)

    assert result == [{"a": 1}, {"a": 2}]


# --- _parse_structured -----------------------------------------------------


class _Statements(BaseModel):
    statements: list[str]


def test_parse_structured_plain_json():
    result = metrics._parse_structured('{"statements": ["a", "b"]}', _Statements)
    assert result.statements == ["a", "b"]


def test_parse_structured_strips_markdown_fence():
    text = '```json\n{"statements": ["a"]}\n```'
    result = metrics._parse_structured(text, _Statements)
    assert result.statements == ["a"]


def test_parse_structured_strips_bare_fence_without_json_tag():
    text = '```\n{"statements": ["a"]}\n```'
    result = metrics._parse_structured(text, _Statements)
    assert result.statements == ["a"]


# --- ragas_faithfulness aggregation (Faithfulness metric monkeypatched) ----


class _FakeMetricResult:
    def __init__(self, value):
        self.value = value


class _FakeFaithfulness:
    """Stands in for ragas.metrics.collections.Faithfulness: records what it
    was scored on and returns canned values, so the aggregation logic in
    ragas_faithfulness() is tested without touching RAGAS internals or an
    LLM."""

    def __init__(self, llm):
        self.llm = llm

    async def ascore(self, *, user_input, response, retrieved_contexts):
        if response == "nan-case":
            return _FakeMetricResult(float("nan"))
        return _FakeMetricResult(0.5 if response == "half" else 1.0)


@pytest.fixture(autouse=True)
def fake_faithfulness_metric(monkeypatch):
    monkeypatch.setattr(metrics, "Faithfulness", _FakeFaithfulness)


@pytest.mark.asyncio
async def test_ragas_faithfulness_averages_answered_rows_only():
    records = [
        {"status": "answered", "question": "q1", "answer": "full", "context_texts": ["ctx"]},
        {"status": "answered", "question": "q2", "answer": "half", "context_texts": ["ctx"]},
        {"status": "refused_off_book", "question": "q3", "answer": "no", "context_texts": []},
    ]
    score, n = await metrics.ragas_faithfulness(records)
    assert score == pytest.approx(0.75)  # (1.0 + 0.5) / 2
    assert n == 2


@pytest.mark.asyncio
async def test_ragas_faithfulness_excludes_nan_scores():
    records = [
        {"status": "answered", "question": "q1", "answer": "full", "context_texts": ["ctx"]},
        {"status": "answered", "question": "q2", "answer": "nan-case", "context_texts": ["ctx"]},
    ]
    score, n = await metrics.ragas_faithfulness(records)
    assert score == pytest.approx(1.0)
    assert n == 1


@pytest.mark.asyncio
async def test_ragas_faithfulness_no_answered_rows_returns_none():
    records = [{"status": "refused_off_book", "question": "q1", "answer": "no", "context_texts": []}]
    score, n = await metrics.ragas_faithfulness(records)
    assert score is None
    assert n == 0


# --- style_summary -----------------------------------------------------


def _style(passed, max_sentence_words, fk_grade, vocab_coverage):
    return {
        "passed": passed,
        "max_sentence_words": max_sentence_words,
        "mean_sentence_words": max_sentence_words,
        "fk_grade": fk_grade,
        "vocab_coverage": vocab_coverage,
        "oov_words": [],
        "judge_ran": True,
        "judge_suitable": passed,
        "judge_reason": "ok",
        "failures": [],
    }


def test_style_summary_averages_answered_rows_with_a_report():
    records = [
        {"status": "answered", "style": _style(True, 10, 3.0, 1.0)},
        {"status": "answered", "style": _style(False, 14, 4.0, 0.8)},
        {"status": "refused_off_book", "style": None},  # excluded: not answered
    ]
    summary = metrics.style_summary(records)
    assert summary["n"] == 2
    assert summary["pass_rate"] == pytest.approx(0.5)
    assert summary["mean_fk"] == pytest.approx(3.5)
    assert summary["mean_max_sentence_words"] == pytest.approx(12.0)
    assert summary["mean_vocab_coverage"] == pytest.approx(0.9)


def test_style_summary_handles_missing_fk_scores():
    # fk_grade is None when the answer is too short to score reliably —
    # shouldn't crash the average, just average over what's available.
    records = [{"status": "answered", "style": _style(True, 8, None, 1.0)}]
    summary = metrics.style_summary(records)
    assert summary["mean_fk"] is None


def test_style_summary_no_style_reports_returns_none():
    records = [{"status": "answered", "style": None}]
    assert metrics.style_summary(records) is None


# --- refusal_accuracy / false_refusal_rate (project-guidelines.md 8.3) -


def test_refusal_accuracy_counts_any_non_answered_status_as_correct():
    records = [
        {"type": "off_book", "status": "refused_off_book"},  # correct: refused
        {"type": "off_book", "status": "refused_unverified"},  # correct: refused, different check
        {"type": "off_book", "status": "answered"},  # wrong: hallucinated an answer
        {"type": "factual", "status": "answered"},  # not off_book, excluded
    ]
    rate, n = metrics.refusal_accuracy(records)
    assert rate == pytest.approx(2 / 3)
    assert n == 3


def test_refusal_accuracy_no_off_book_rows_returns_none():
    records = [{"type": "factual", "status": "answered"}]
    rate, n = metrics.refusal_accuracy(records)
    assert rate is None
    assert n == 0


def test_false_refusal_rate_counts_non_answered_answerable_rows():
    records = [
        {"type": "factual", "status": "answered"},  # correct
        {"type": "factual", "status": "refused_unverified"},  # false refusal
        {"type": "vocabulary", "status": "refused_off_book"},  # false refusal
        {"type": "off_book", "status": "refused_off_book"},  # not answerable, excluded
    ]
    rate, n = metrics.false_refusal_rate(records)
    assert rate == pytest.approx(2 / 3)
    assert n == 3


def test_false_refusal_rate_no_answerable_rows_returns_none():
    records = [{"type": "off_book", "status": "refused_off_book"}]
    rate, n = metrics.false_refusal_rate(records)
    assert rate is None
    assert n == 0


# --- hallucination_rate -------------------------------------------------


def test_hallucination_rate_averages_unsupported_fraction():
    records = [
        {"status": "answered", "verification": {"supported_ratio": 1.0}},  # 0% hallucinated
        {"status": "answered", "verification": {"supported_ratio": 0.5}},  # 50% hallucinated
        {"status": "answered", "verification": None},  # config B/C: no report, excluded
        {"status": "refused_unverified", "verification": {"supported_ratio": 0.0}},  # not answered, excluded
    ]
    rate, n = metrics.hallucination_rate(records)
    assert rate == pytest.approx(0.25)  # mean(0.0, 0.5)
    assert n == 2


def test_hallucination_rate_no_verification_reports_returns_none():
    # This is the expected shape for a config-B/C run: verify_node never
    # ran, so no row carries a verification report at all.
    records = [{"status": "answered", "verification": None}]
    rate, n = metrics.hallucination_rate(records)
    assert rate is None
    assert n == 0


# --- severe_hallucination_rate (2026-09-13, companion to hallucination_rate) -


def test_severe_hallucination_rate_counts_only_zero_ratio_rows():
    records = [
        {"status": "answered", "verification": {"supported_ratio": 0.0}},  # severe: nothing supported
        {"status": "answered", "verification": {"supported_ratio": 0.5}},  # partial, not severe
        {"status": "answered", "verification": {"supported_ratio": 1.0}},  # clean pass
        {"status": "answered", "verification": None},  # config B/C: excluded
        {"status": "refused_unverified", "verification": {"supported_ratio": 0.0}},  # not answered, excluded
    ]
    rate, n = metrics.severe_hallucination_rate(records)
    assert rate == pytest.approx(1 / 3)
    assert n == 3


def test_severe_hallucination_rate_no_verification_reports_returns_none():
    records = [{"status": "answered", "verification": None}]
    rate, n = metrics.severe_hallucination_rate(records)
    assert rate is None
    assert n == 0


# --- readability_summary -------------------------------------------------


def test_readability_summary_scores_answer_text_directly():
    # Real English text long enough (30+ words) for check_fk_grade to
    # return a number rather than None — readability_summary must work
    # from the raw answer string alone, with no style report involved.
    long_answer = " ".join(["This is a simple sentence about a boy and his school."] * 5)
    records = [
        {"status": "answered", "answer": long_answer},
        {"status": "answered", "answer": "Too short to score."},
        {"status": "refused_off_book", "answer": "I couldn't find anything about that."},
    ]
    summary = metrics.readability_summary(records)
    assert summary["n"] == 2  # both answered rows count toward sentence length
    assert summary["fk_n"] == 1  # only the long one is long enough for an FK score
    assert summary["mean_fk"] is not None
    assert summary["mean_sentence_words"] > 0
    assert summary["mean_max_sentence_words"] > 0


def test_readability_summary_no_answered_rows_returns_none():
    records = [{"status": "refused_off_book", "answer": "no"}]
    assert metrics.readability_summary(records) is None
