"""
What: wires retrieve.py and generate.py into a two-node LangGraph with one
      conditional edge — the off-book gate. This is Phase 2's "bare
      pipeline": no rewrite, no grade voice, no style check, no verifier yet
      (Phases 3-4 add those as more nodes on this same graph, per CLAUDE.md's
      fixed pipeline flow). run_pipeline() is the one entry point the API
      route and eval/runner.py both call.

Why the gate is a graph edge, not an if-statement inside generate.py:
      CLAUDE.md's pipeline flow is "retrieve -> off-book gate -> stage 1 ->
      ...", and the gate must be able to skip stage 1 entirely — asking an
      LLM to answer from chunks that didn't clear the similarity threshold
      would spend a paid call on a question that gets refused anyway. Making
      it a LangGraph conditional edge keeps that skip explicit, and keeps
      route_after_retrieve() (the actual threshold check) testable as a
      plain function, independent of LangGraph.

Only two of the four messages.status values are reachable this phase:
      'answered' and 'refused_off_book'. 'low_confidence' and
      'refused_unverified' both depend on verify.py, which doesn't exist
      until Phase 4.
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
from app.pipeline.generate import generate_stage1
from app.pipeline.llm import LLMResult
from app.pipeline.retrieve import RetrievalResult, hybrid_search

# No LLM call happens on this branch, so this is UI copy, not a prompt —
# it doesn't belong in prompts/ (CLAUDE.md reserves that for text sent to an
# LLM). Phase 3 can replace it with a grade-appropriate refusal.
OFF_BOOK_REFUSAL = "I couldn't find anything about that in your book, so I can't answer it yet."


class GraphState(TypedDict, total=False):
    question: str
    grade: int
    book_id: uuid.UUID
    provider: str
    retrieval: RetrievalResult
    status: MessageStatus
    answer: str
    llm: LLMResult | None


async def retrieve_node(state: GraphState) -> dict:
    retrieval = await hybrid_search(state["book_id"], state["question"])
    return {"retrieval": retrieval}


async def generate_node(state: GraphState) -> dict:
    # generate_stage1 -> call_llm is a blocking network/disk call; offload it
    # the same way ingest_book() offloads build_chunks(), so one slow LLM
    # call doesn't freeze the event loop for every other request.
    result = await asyncio.to_thread(
        generate_stage1, state["question"], state["retrieval"].context_chunks, provider=state.get("provider", "openai")
    )
    return {"answer": result.answer, "status": MessageStatus.ANSWERED, "llm": result.llm}


async def refuse_node(state: GraphState) -> dict:
    return {"answer": OFF_BOOK_REFUSAL, "status": MessageStatus.REFUSED_OFF_BOOK, "llm": None}


def route_after_retrieve(state: GraphState) -> Literal["generate", "refuse"]:
    """Pure routing decision, testable without LangGraph: below the
    off-book threshold, skip generate entirely."""
    threshold = get_settings().offbook_score_threshold
    if state["retrieval"].best_score < threshold:
        return "refuse"
    return "generate"


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
_graph.add_node("retrieve", retrieve_node)
_graph.add_node("generate", generate_node)
_graph.add_node("refuse", refuse_node)
_graph.add_edge(START, "retrieve")
_graph.add_conditional_edges("retrieve", route_after_retrieve, {"generate": "generate", "refuse": "refuse"})
_graph.add_edge("generate", END)
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
    llm: LLMResult | None
    # The actual (post-expansion) chunk text stage 1 read, not just the cited
    # sources — eval/metrics.py's RAGAS faithfulness check needs this; a
    # message row does not, so it isn't part of MessageRead.
    context_texts: list[str]


async def run_pipeline(question: str, grade: int, book_id: uuid.UUID, *, provider: str = "openai") -> PipelineResult:
    start = time.monotonic()
    final_state = await _compiled.ainvoke(
        {"question": question, "grade": grade, "book_id": book_id, "provider": provider}
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
        llm=final_state.get("llm"),
        context_texts=[c.text for c in retrieval.context_chunks],
    )
