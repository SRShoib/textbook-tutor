"""Tests for pipeline/graph.py. route_after_retrieve, route_after_style_check
and build_sources are tested as plain functions; run_pipeline is tested
end-to-end through the compiled LangGraph with hybrid_search, generate_stage1,
generate_stage2, check_style, load_history and rewrite_question all
monkeypatched, so no DB, embedding model or LLM is ever touched.
CLAUDE.md: test pipeline/ functions, skip route tests."""

import uuid

import pytest

from app.models.chunk import Chunk, ChunkType
from app.pipeline import graph
from app.pipeline.generate import Stage1Result, Stage2Result
from app.pipeline.llm import LLMResult
from app.pipeline.retrieve import RetrievalResult, ScoredChunk
from app.pipeline.rewrite import RewriteResult
from app.pipeline.style_check import StyleReport


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


def make_style_report(passed: bool, failures=None) -> StyleReport:
    return StyleReport(
        passed=passed,
        max_sentence_words=8,
        mean_sentence_words=6.0,
        fk_grade=3.5,
        vocab_coverage=1.0,
        oov_words=[],
        judge_ran=True,
        judge_suitable=passed,
        judge_reason="fine" if passed else "too formal",
        failures=failures or [],
    )


def fake_llm_result(text: str, prompt_version: str) -> LLMResult:
    return LLMResult(
        text=text, model="gpt-test", provider="openai", prompt_version=prompt_version,
        cached=False, prompt_tokens=10, completion_tokens=5,
    )


@pytest.fixture(autouse=True)
def restore_settings():
    from app.core.config import get_settings

    settings = get_settings()
    original = {
        "offbook_score_threshold": settings.offbook_score_threshold,
        "config_version": settings.config_version,
        "style_max_retries": settings.style_max_retries,
    }
    yield
    for k, v in original.items():
        setattr(settings, k, v)


# --- route_after_retrieve ---------------------------------------------------


def test_route_below_threshold_refuses():
    from app.core.config import get_settings

    get_settings().offbook_score_threshold = 0.35
    state = {"retrieval": make_retrieval(best_score=0.2)}
    assert graph.route_after_retrieve(state) == "refuse"


def test_route_above_threshold_generates():
    from app.core.config import get_settings

    get_settings().offbook_score_threshold = 0.35
    state = {"retrieval": make_retrieval(best_score=0.5)}
    assert graph.route_after_retrieve(state) == "generate"


def test_route_exactly_at_threshold_generates():
    from app.core.config import get_settings

    get_settings().offbook_score_threshold = 0.35
    state = {"retrieval": make_retrieval(best_score=0.35)}
    assert graph.route_after_retrieve(state) == "generate"


# --- route_after_style_check -------------------------------------------


def test_route_after_style_check_passed_ends():
    state = {"style": make_style_report(passed=True), "style_attempts": 1}
    assert graph.route_after_style_check(state) == "end"


def test_route_after_style_check_failed_with_retries_left_retries():
    # style_max_retries=2 means 2 retries AFTER the first attempt (3 total).
    from app.core.config import get_settings

    get_settings().style_max_retries = 2
    state = {"style": make_style_report(passed=False, failures=["too long"]), "style_attempts": 2}
    assert graph.route_after_style_check(state) == "retry"


def test_route_after_style_check_failed_out_of_retries_ends():
    from app.core.config import get_settings

    get_settings().style_max_retries = 2
    state = {"style": make_style_report(passed=False, failures=["too long"]), "style_attempts": 3}
    assert graph.route_after_style_check(state) == "end"


# --- build_sources -------------------------------------------------------


def test_build_sources_reflects_top_chunks_not_expanded_context():
    retrieval = make_retrieval(best_score=0.9)
    sources = graph.build_sources(retrieval)
    assert len(sources) == 1
    assert sources[0]["lesson_id"] == "u1-s1"
    assert sources[0]["dense_score"] == 0.9


# --- run_pipeline (end-to-end through the compiled graph) ------------------


@pytest.mark.asyncio
async def test_run_pipeline_off_book_skips_generation(monkeypatch):
    from app.core.config import get_settings

    get_settings().offbook_score_threshold = 0.35
    retrieval = make_retrieval(best_score=0.1)

    async def fake_hybrid_search(book_id, query, top_k=None):
        return retrieval

    def fail_if_called(*args, **kwargs):
        raise AssertionError("generation must not be called on the off-book branch")

    monkeypatch.setattr(graph, "hybrid_search", fake_hybrid_search)
    monkeypatch.setattr(graph, "generate_stage1", fail_if_called)
    monkeypatch.setattr(graph, "generate_stage2", fail_if_called)
    monkeypatch.setattr(graph, "check_style", fail_if_called)

    result = await graph.run_pipeline("Who is the president of France?", grade=5, book_id=uuid.uuid4())

    assert result.status == "refused_off_book"
    assert result.answer == graph.OFF_BOOK_REFUSAL
    assert result.llm is None
    assert result.stage1_answer is None
    assert result.style is None
    assert result.sources  # still cites what was retrieved, even though refused


@pytest.mark.asyncio
async def test_run_pipeline_in_book_generates_and_passes_style_check(monkeypatch):
    from app.core.config import get_settings

    get_settings().offbook_score_threshold = 0.35
    get_settings().config_version = "v1"
    retrieval = make_retrieval(best_score=0.9)

    async def fake_hybrid_search(book_id, query, top_k=None):
        assert query == "What did Sumon do?"  # no session_id -> search_query == question
        return retrieval

    def fake_generate_stage1(question, context_chunks, *, provider="openai"):
        return Stage1Result(answer="Sumon went to school.", llm=fake_llm_result("Sumon went to school.", "v1_stage1"))

    def fake_generate_stage2(question, stage1_answer, *, grade, is_first_turn=True, provider="openai", feedback=None):
        assert feedback is None  # first attempt, no prior failure to feed back
        return Stage2Result(
            answer="Dear students, Sumon went to school.",
            llm=fake_llm_result("Dear students, Sumon went to school.", "v1_stage2"),
        )

    async def fake_check_style(answer, *, grade, book_id, provider="openai"):
        return make_style_report(passed=True)

    monkeypatch.setattr(graph, "hybrid_search", fake_hybrid_search)
    monkeypatch.setattr(graph, "generate_stage1", fake_generate_stage1)
    monkeypatch.setattr(graph, "generate_stage2", fake_generate_stage2)
    monkeypatch.setattr(graph, "check_style", fake_check_style)

    result = await graph.run_pipeline("What did Sumon do?", grade=5, book_id=uuid.uuid4())

    assert result.status == "answered"
    assert result.answer == "Dear students, Sumon went to school."
    assert result.stage1_answer == "Sumon went to school."
    assert result.llm.prompt_version == "v1_stage2"
    assert result.config_version == "v1"
    assert result.best_score == 0.9
    assert len(result.sources) == 1
    assert result.latency_ms >= 0
    assert result.context_texts == [retrieval.context_chunks[0].text]
    assert result.search_query == "What did Sumon do?"
    assert result.style.passed is True
    assert result.style_attempts == 1


@pytest.mark.asyncio
async def test_run_pipeline_retries_stage2_once_on_style_failure(monkeypatch):
    from app.core.config import get_settings

    get_settings().offbook_score_threshold = 0.35
    get_settings().style_max_retries = 2
    retrieval = make_retrieval(best_score=0.9)

    stage2_calls = []
    style_calls = []

    async def fake_hybrid_search(book_id, query, top_k=None):
        return retrieval

    def fake_generate_stage1(question, context_chunks, *, provider="openai"):
        return Stage1Result(answer="Sumon went to school.", llm=fake_llm_result("s1", "v1_stage1"))

    def fake_generate_stage2(question, stage1_answer, *, grade, is_first_turn=True, provider="openai", feedback=None):
        stage2_calls.append(feedback)
        text = "too long answer" if len(stage2_calls) == 1 else "Dear students, Sumon went to school."
        return Stage2Result(answer=text, llm=fake_llm_result(text, "v1_stage2"))

    async def fake_check_style(answer, *, grade, book_id, provider="openai"):
        style_calls.append(answer)
        if answer == "too long answer":
            return make_style_report(passed=False, failures=["a sentence is 20 words long"])
        return make_style_report(passed=True)

    monkeypatch.setattr(graph, "hybrid_search", fake_hybrid_search)
    monkeypatch.setattr(graph, "generate_stage1", fake_generate_stage1)
    monkeypatch.setattr(graph, "generate_stage2", fake_generate_stage2)
    monkeypatch.setattr(graph, "check_style", fake_check_style)

    result = await graph.run_pipeline("What did Sumon do?", grade=5, book_id=uuid.uuid4())

    assert len(stage2_calls) == 2
    assert stage2_calls[0] is None  # first attempt: no feedback yet
    assert stage2_calls[1] == "a sentence is 20 words long"  # retry: prior failure fed back
    assert result.status == "answered"
    assert result.style.passed is True
    assert result.style_attempts == 2


@pytest.mark.asyncio
async def test_run_pipeline_still_answered_after_exhausting_style_retries(monkeypatch):
    from app.core.config import get_settings

    get_settings().offbook_score_threshold = 0.35
    get_settings().style_max_retries = 2
    retrieval = make_retrieval(best_score=0.9)

    style_calls = []

    async def fake_hybrid_search(book_id, query, top_k=None):
        return retrieval

    def fake_generate_stage1(question, context_chunks, *, provider="openai"):
        return Stage1Result(answer="Sumon went to school.", llm=fake_llm_result("s1", "v1_stage1"))

    def fake_generate_stage2(question, stage1_answer, *, grade, is_first_turn=True, provider="openai", feedback=None):
        return Stage2Result(answer="still too long", llm=fake_llm_result("still too long", "v1_stage2"))

    async def fake_check_style(answer, *, grade, book_id, provider="openai"):
        style_calls.append(answer)
        return make_style_report(passed=False, failures=["still too long"])

    monkeypatch.setattr(graph, "hybrid_search", fake_hybrid_search)
    monkeypatch.setattr(graph, "generate_stage1", fake_generate_stage1)
    monkeypatch.setattr(graph, "generate_stage2", fake_generate_stage2)
    monkeypatch.setattr(graph, "check_style", fake_check_style)

    result = await graph.run_pipeline("What did Sumon do?", grade=5, book_id=uuid.uuid4())

    # 1 initial attempt + style_max_retries(2) retries after it = 3 total
    assert len(style_calls) == 3
    assert result.status == "answered"  # per CLAUDE.md: status is not a new
    # value on style failure; low_confidence stays reserved for Phase 4
    assert result.style.passed is False
    assert result.style_attempts == 3


# --- rewrite integration (session_id threads through to search_query) ------


@pytest.mark.asyncio
async def test_run_pipeline_without_session_id_skips_rewrite_entirely(monkeypatch):
    from app.core.config import get_settings

    get_settings().offbook_score_threshold = 0.35
    retrieval = make_retrieval(best_score=0.9)

    stage2_calls = []

    def fail_if_called(*args, **kwargs):
        raise AssertionError("rewrite must not run when session_id is None (eval runner path)")

    async def fake_hybrid_search(book_id, query, top_k=None):
        assert query == "give me more examples"  # unrewritten — no history to rewrite against
        return retrieval

    def fake_generate_stage2(q, s1, *, grade, is_first_turn=True, provider="openai", feedback=None):
        stage2_calls.append(is_first_turn)
        return Stage2Result(answer="b", llm=fake_llm_result("b", "v1_stage2"))

    monkeypatch.setattr(graph, "load_history", fail_if_called)
    monkeypatch.setattr(graph, "hybrid_search", fake_hybrid_search)
    monkeypatch.setattr(
        graph, "generate_stage1", lambda q, c, *, provider="openai": Stage1Result(answer="a", llm=fake_llm_result("a", "v1_stage1"))
    )
    monkeypatch.setattr(graph, "generate_stage2", fake_generate_stage2)

    async def fake_check_style(answer, *, grade, book_id, provider="openai"):
        return make_style_report(passed=True)

    monkeypatch.setattr(graph, "check_style", fake_check_style)

    result = await graph.run_pipeline("give me more examples", grade=5, book_id=uuid.uuid4(), session_id=None)

    assert result.search_query == "give me more examples"
    # No session history at all (eval-runner shape) is treated as a first
    # turn — every eval question is independent, not a follow-up.
    assert stage2_calls == [True]


@pytest.mark.asyncio
async def test_run_pipeline_with_session_id_rewrites_the_query(monkeypatch):
    from app.core.config import get_settings

    get_settings().offbook_score_threshold = 0.35
    retrieval = make_retrieval(best_score=0.9)
    session_id = uuid.uuid4()

    stage2_calls = []

    async def fake_load_history(sid, limit):
        assert sid == session_id
        return ["fake-history-item"]

    def fake_rewrite_question(question, history, *, provider="openai"):
        assert history == ["fake-history-item"]
        return RewriteResult(query="Give more examples of nouns.", rewritten=True, llm=None)

    async def fake_hybrid_search(book_id, query, top_k=None):
        assert query == "Give more examples of nouns."
        return retrieval

    def fake_generate_stage2(q, s1, *, grade, is_first_turn=True, provider="openai", feedback=None):
        stage2_calls.append(is_first_turn)
        return Stage2Result(answer="b", llm=fake_llm_result("b", "v1_stage2"))

    monkeypatch.setattr(graph, "load_history", fake_load_history)
    monkeypatch.setattr(graph, "rewrite_question", fake_rewrite_question)
    monkeypatch.setattr(graph, "hybrid_search", fake_hybrid_search)
    monkeypatch.setattr(
        graph, "generate_stage1", lambda q, c, *, provider="openai": Stage1Result(answer="a", llm=fake_llm_result("a", "v1_stage1"))
    )
    monkeypatch.setattr(graph, "generate_stage2", fake_generate_stage2)

    async def fake_check_style(answer, *, grade, book_id, provider="openai"):
        return make_style_report(passed=True)

    monkeypatch.setattr(graph, "check_style", fake_check_style)

    result = await graph.run_pipeline(
        "give me more examples", grade=5, book_id=uuid.uuid4(), session_id=session_id
    )

    assert result.search_query == "Give more examples of nouns."
    # Non-empty history -> a real follow-up, not a first turn: no repeated
    # greeting/song/lesson-framing (the bug caught in the live verification).
    assert stage2_calls == [False]


@pytest.mark.asyncio
async def test_run_pipeline_first_message_of_a_session_is_still_first_turn(monkeypatch):
    from app.core.config import get_settings

    get_settings().offbook_score_threshold = 0.35
    retrieval = make_retrieval(best_score=0.9)
    session_id = uuid.uuid4()
    stage2_calls = []

    async def fake_load_history(sid, limit):
        return []  # brand-new session: nothing to rewrite against yet

    async def fake_hybrid_search(book_id, query, top_k=None):
        return retrieval

    def fake_generate_stage2(q, s1, *, grade, is_first_turn=True, provider="openai", feedback=None):
        stage2_calls.append(is_first_turn)
        return Stage2Result(answer="b", llm=fake_llm_result("b", "v1_stage2"))

    monkeypatch.setattr(graph, "load_history", fake_load_history)
    monkeypatch.setattr(graph, "hybrid_search", fake_hybrid_search)
    monkeypatch.setattr(
        graph, "generate_stage1", lambda q, c, *, provider="openai": Stage1Result(answer="a", llm=fake_llm_result("a", "v1_stage1"))
    )
    monkeypatch.setattr(graph, "generate_stage2", fake_generate_stage2)

    async def fake_check_style(answer, *, grade, book_id, provider="openai"):
        return make_style_report(passed=True)

    monkeypatch.setattr(graph, "check_style", fake_check_style)

    await graph.run_pipeline("What is a noun?", grade=5, book_id=uuid.uuid4(), session_id=session_id)

    assert stage2_calls == [True]
