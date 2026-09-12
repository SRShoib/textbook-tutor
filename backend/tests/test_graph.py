"""Tests for pipeline/graph.py. route_after_retrieve and build_sources are
tested as plain functions; run_pipeline is tested end-to-end through the
compiled LangGraph with hybrid_search and generate_stage1 monkeypatched, so
no DB, embedding model or LLM is ever touched.
CLAUDE.md: test pipeline/ functions, skip route tests."""

import uuid

import pytest

from app.models.chunk import Chunk, ChunkType
from app.pipeline import graph
from app.pipeline.generate import Stage1Result
from app.pipeline.llm import LLMResult
from app.pipeline.retrieve import RetrievalResult, ScoredChunk


def make_chunk(**overrides) -> Chunk:
    defaults = dict(
        id=uuid.uuid4(),
        book_id=None,
        lesson_id="u1-s1",
        unit=1,
        lesson_no=1,
        lesson_title="Our School",
        page=5,
        type=ChunkType.PASSAGE,
        text="Unit 1, Lesson 1: Our School\nSome passage text.",
        embedding=[0.0] * 1024,
        sparse={},
    )
    defaults.update(overrides)
    return Chunk(**defaults)


def make_retrieval(best_score: float) -> RetrievalResult:
    chunk = make_chunk()
    scored = ScoredChunk(chunk=chunk, dense_score=best_score, sparse_score=0.1, dense_rank=1, sparse_rank=1, rrf_score=0.02)
    return RetrievalResult(top_chunks=[scored], context_chunks=[chunk], best_score=best_score)


@pytest.fixture(autouse=True)
def restore_threshold():
    from app.core.config import get_settings

    settings = get_settings()
    original = settings.offbook_score_threshold
    yield
    settings.offbook_score_threshold = original


# --- route_after_retrieve ---------------------------------------------------


def test_route_below_threshold_refuses():
    from app.core.config import get_settings

    get_settings().offbook_score_threshold = 0.35
    state = {"retrieval": make_retrieval(best_score=0.2)}
    assert graph.route_after_retrieve(state) == "refuse"


def test_route_above_threshold_generates():
    from app.core.config import get_settings

    get_settings().offbook_score_threshold = 0.35
    state = {"retrieval": make_retrieval(best_score=0.9)}
    assert graph.route_after_retrieve(state) == "generate"


def test_route_exactly_at_threshold_generates():
    # best_score < threshold refuses; equal to the threshold clears the gate.
    from app.core.config import get_settings

    get_settings().offbook_score_threshold = 0.35
    state = {"retrieval": make_retrieval(best_score=0.35)}
    assert graph.route_after_retrieve(state) == "generate"


# --- build_sources -----------------------------------------------------


def test_build_sources_reflects_top_chunks_not_expanded_context():
    top_chunk = make_chunk(lesson_id="u1-s1", page=5)
    extra_expanded_chunk = make_chunk(lesson_id="u1-s1", page=6)
    scored = ScoredChunk(chunk=top_chunk, dense_score=0.9, sparse_score=0.2, dense_rank=1, sparse_rank=2, rrf_score=0.03)
    retrieval = RetrievalResult(top_chunks=[scored], context_chunks=[top_chunk, extra_expanded_chunk], best_score=0.9)

    sources = graph.build_sources(retrieval)

    assert len(sources) == 1  # one entry per top hit, not per expanded chunk
    assert sources[0]["lesson_id"] == "u1-s1"
    assert sources[0]["page"] == 5
    assert sources[0]["dense_score"] == 0.9


# --- run_pipeline (end-to-end through the compiled graph) ------------------


@pytest.mark.asyncio
async def test_run_pipeline_off_book_skips_generation(monkeypatch):
    from app.core.config import get_settings

    get_settings().offbook_score_threshold = 0.35
    retrieval = make_retrieval(best_score=0.1)

    async def fake_hybrid_search(book_id, query, top_k=None):
        return retrieval

    calls = []

    def fake_generate_stage1(question, context_chunks, *, provider="openai"):
        calls.append(question)
        raise AssertionError("generate_stage1 must not be called on the off-book branch")

    monkeypatch.setattr(graph, "hybrid_search", fake_hybrid_search)
    monkeypatch.setattr(graph, "generate_stage1", fake_generate_stage1)

    result = await graph.run_pipeline("Who is the president of France?", grade=5, book_id=uuid.uuid4())

    assert result.status == "refused_off_book"
    assert result.answer == graph.OFF_BOOK_REFUSAL
    assert result.llm is None
    assert result.sources  # still cites what was retrieved, even though refused
    assert calls == []


@pytest.mark.asyncio
async def test_run_pipeline_in_book_generates_and_returns_evidence(monkeypatch):
    from app.core.config import get_settings

    get_settings().offbook_score_threshold = 0.35
    get_settings().config_version = "v1"
    retrieval = make_retrieval(best_score=0.9)

    async def fake_hybrid_search(book_id, query, top_k=None):
        return retrieval

    def fake_generate_stage1(question, context_chunks, *, provider="openai"):
        llm_result = LLMResult(
            text="Sumon went to school.", model="gpt-test", provider=provider,
            prompt_version="v1_stage1", cached=False, prompt_tokens=10, completion_tokens=5,
        )
        return Stage1Result(answer=llm_result.text, llm=llm_result)

    monkeypatch.setattr(graph, "hybrid_search", fake_hybrid_search)
    monkeypatch.setattr(graph, "generate_stage1", fake_generate_stage1)

    result = await graph.run_pipeline("What did Sumon do?", grade=5, book_id=uuid.uuid4())

    assert result.status == "answered"
    assert result.answer == "Sumon went to school."
    assert result.llm.cached is False
    assert result.config_version == "v1"
    assert result.best_score == 0.9
    assert len(result.sources) == 1
    assert result.latency_ms >= 0
    assert result.context_texts == [retrieval.context_chunks[0].text]
