"""
What: wires rewrite.py, retrieve.py, generate.py's two stages and
      style_check.py into one LangGraph, per CLAUDE.md's fixed pipeline
      flow:
          rewrite -> retrieve -> off-book gate -> stage 1 -> stage 2
          -> style_check -> (pass: done | fail, retries left: back to
             stage 2 with the reason | fail, out of retries: done anyway)
      run_pipeline() is the one entry point the API route and eval/runner.py
      both call.

Why the gate is a graph edge, not an if-statement inside generate.py:
      the gate must be able to skip stage 1 entirely — asking an LLM to
      answer from chunks that didn't clear the similarity threshold would
      spend a paid call on a question that gets refused anyway. A LangGraph
      conditional edge keeps that skip explicit, and keeps
      route_after_retrieve() testable as a plain function, independent of
      LangGraph. route_after_style_check() is the same shape for the
      style-check retry loop.

Why a style-check failure still ends in status=answered, not a new status:
      messages.status's four values are fixed by CLAUDE.md, and
      'low_confidence' is reserved for Phase 4's verifier (a different
      failure mode — unsupported by the book, not merely hard to read). A
      correct answer that is still a little too long for the grade should
      still reach the child; the failing StyleReport is stored on the
      message (in messages.readability) so error analysis can count these
      separately from a clean pass.

Why session_id is optional: the eval runner runs each question as an
      isolated turn with no session history to rewrite against — passing
      session_id=None makes rewrite_node skip straight past the LLM call
      (see rewrite.needs_rewrite: empty history never rewrites), so eval
      runs stay single-turn without a special-cased code path here.

Only three of the four messages.status values are reachable this phase:
      'answered' and 'refused_off_book' (Phase 2), plus 'low_confidence'
      still waits on Phase 4's verifier, which also owns 'refused_unverified'.
"""

from __future__ import annotations

import asyncio
import time
import uuid
from dataclasses import dataclass
from typing import Literal, TypedDict

from langgraph.graph import END, START, StateGraph

from app.core.config import get_settings
from app.models.message import MessageStatus
from app.pipeline.generate import generate_stage1, generate_stage2
from app.pipeline.llm import LLMResult
from app.pipeline.retrieve import RetrievalResult, hybrid_search
from app.pipeline.rewrite import load_history, rewrite_question
from app.pipeline.style_check import StyleReport, check_style

# No LLM call happens on this branch, so this is UI copy, not a prompt —
# it doesn't belong in prompts/ (CLAUDE.md reserves that for text sent to an
# LLM).
OFF_BOOK_REFUSAL = "I couldn't find anything about that in your book, so I can't answer it yet."


class GraphState(TypedDict, total=False):
    question: str
    grade: int
    book_id: uuid.UUID
    session_id: uuid.UUID | None
    provider: str
    search_query: str
    rewritten: bool
    is_first_turn: bool
    retrieval: RetrievalResult
    status: MessageStatus
    stage1_answer: str
    stage1_llm: LLMResult | None
    answer: str
    final_llm: LLMResult | None
    style: StyleReport
    style_attempts: int


async def rewrite_node(state: GraphState) -> dict:
    question = state["question"]
    session_id = state.get("session_id")
    if session_id is None:
        # Eval runner turns have no session history — each question is
        # independent, so it's treated as a first turn (full opening move).
        return {"search_query": question, "rewritten": False, "is_first_turn": True}

    history = await load_history(session_id, get_settings().history_max_messages)
    result = await asyncio.to_thread(
        rewrite_question, question, history, provider=state.get("provider", "openai")
    )
    return {"search_query": result.query, "rewritten": result.rewritten, "is_first_turn": not history}


async def retrieve_node(state: GraphState) -> dict:
    retrieval = await hybrid_search(state["book_id"], state["search_query"])
    return {"retrieval": retrieval}


async def stage1_node(state: GraphState) -> dict:
    # generate_stage1 -> call_llm is a blocking network/disk call; offload it
    # the same way ingest_book() offloads build_chunks(), so one slow LLM
    # call doesn't freeze the event loop for every other request.
    result = await asyncio.to_thread(
        generate_stage1,
        state["search_query"],
        state["retrieval"].context_chunks,
        provider=state.get("provider", "openai"),
    )
    return {"stage1_answer": result.answer, "stage1_llm": result.llm}


async def stage2_node(state: GraphState) -> dict:
    style: StyleReport | None = state.get("style")
    feedback = "; ".join(style.failures) if style is not None else None
    result = await asyncio.to_thread(
        generate_stage2,
        state["search_query"],
        state["stage1_answer"],
        grade=state["grade"],
        is_first_turn=state.get("is_first_turn", True),
        provider=state.get("provider", "openai"),
        feedback=feedback,
    )
    return {"answer": result.answer, "final_llm": result.llm, "status": MessageStatus.ANSWERED}


async def style_check_node(state: GraphState) -> dict:
    attempts = state.get("style_attempts", 0) + 1
    report = await check_style(
        state["answer"],
        grade=state["grade"],
        book_id=state["book_id"],
        provider=state.get("provider", "openai"),
    )
    return {"style": report, "style_attempts": attempts}


async def refuse_node(state: GraphState) -> dict:
    return {"answer": OFF_BOOK_REFUSAL, "status": MessageStatus.REFUSED_OFF_BOOK, "final_llm": None}


def route_after_retrieve(state: GraphState) -> Literal["generate", "refuse"]:
    """Pure routing decision, testable without LangGraph: below the
    off-book threshold, skip generation entirely."""
    threshold = get_settings().offbook_score_threshold
    if state["retrieval"].best_score < threshold:
        return "refuse"
    return "generate"


def route_after_style_check(state: GraphState) -> Literal["retry", "end"]:
    """Pure routing decision: retry stage 2 (feeding back what failed) up to
    style_max_retries times AFTER the first attempt — so style_max_retries=2
    allows 3 attempts total — then stop trying. See the module docstring on
    why a still-failing answer ends as 'answered', not a new status."""
    report = state["style"]
    if report.passed:
        return "end"
    if state["style_attempts"] > get_settings().style_max_retries:
        return "end"
    return "retry"


def build_sources(retrieval: RetrievalResult) -> list[dict]:
    """The pre-expansion top hits, not the expanded context — what a message
    cites should point at the specific lessons that matched, not every
    lesson the LLM happened to read."""
    return [
        {
            "lesson_id": sc.chunk.lesson_id,
            "unit": sc.chunk.unit,
            "lesson_no": sc.chunk.lesson_no,
            "lesson_title": sc.chunk.lesson_title,
            "page": sc.chunk.page,
            "dense_score": sc.dense_score,
            "sparse_score": sc.sparse_score,
            "rrf_score": sc.rrf_score,
        }
        for sc in retrieval.top_chunks
    ]


_graph = StateGraph(GraphState)
_graph.add_node("rewrite", rewrite_node)
_graph.add_node("retrieve", retrieve_node)
_graph.add_node("stage1", stage1_node)
_graph.add_node("stage2", stage2_node)
_graph.add_node("style_check", style_check_node)
_graph.add_node("refuse", refuse_node)
_graph.add_edge(START, "rewrite")
_graph.add_edge("rewrite", "retrieve")
_graph.add_conditional_edges("retrieve", route_after_retrieve, {"generate": "stage1", "refuse": "refuse"})
_graph.add_edge("stage1", "stage2")
_graph.add_edge("stage2", "style_check")
_graph.add_conditional_edges("style_check", route_after_style_check, {"retry": "stage2", "end": END})
_graph.add_edge("refuse", END)
_compiled = _graph.compile()


@dataclass(frozen=True)
class PipelineResult:
    answer: str
    status: MessageStatus
    sources: list[dict]
    best_score: float
    latency_ms: int
    config_version: str
    # The LLM call that produced the displayed answer text: stage 2's on the
    # generate path, None on refusal. Stage 1's call is on `stage1_answer`
    # below, kept separate since it's a different (checked, unvoiced) text.
    llm: LLMResult | None
    # The actual (post-expansion) chunk text stage 1 read, not just the cited
    # sources — eval/metrics.py's RAGAS faithfulness check needs this; a
    # message row does not, so it isn't part of MessageRead.
    context_texts: list[str]
    search_query: str
    stage1_answer: str | None
    style: StyleReport | None
    style_attempts: int


async def run_pipeline(
    question: str,
    grade: int,
    book_id: uuid.UUID,
    *,
    session_id: uuid.UUID | None = None,
    provider: str = "openai",
) -> PipelineResult:
    start = time.monotonic()
    final_state = await _compiled.ainvoke(
        {
            "question": question,
            "grade": grade,
            "book_id": book_id,
            "session_id": session_id,
            "provider": provider,
        }
    )
    latency_ms = int((time.monotonic() - start) * 1000)

    retrieval: RetrievalResult = final_state["retrieval"]
    return PipelineResult(
        answer=final_state["answer"],
        status=final_state["status"],
        sources=build_sources(retrieval),
        best_score=retrieval.best_score,
        latency_ms=latency_ms,
        config_version=get_settings().config_version,
        llm=final_state.get("final_llm"),
        context_texts=[c.text for c in retrieval.context_chunks],
        search_query=final_state.get("search_query", question),
        stage1_answer=final_state.get("stage1_answer"),
        style=final_state.get("style"),
        style_attempts=final_state.get("style_attempts", 0),
    )
