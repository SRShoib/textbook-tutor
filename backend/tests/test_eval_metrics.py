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
