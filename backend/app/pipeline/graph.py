"""
What: wires rewrite.py, retrieve.py, generate.py's two stages, style_check.py
      and verify.py into one LangGraph, per CLAUDE.md's fixed pipeline flow:
          rewrite -> retrieve -> off-book gate -> stage 1 -> stage 2
          -> style_check -> (fail, retries left: back to stage 2 with the
             reason | pass or out of retries: continue) -> verify
          -> (supported: done | partial, retries left: back to stage 2 with
             which sentences failed | partial, out of retries: refused)
      run_pipeline() is the one entry point the API route and eval/runner.py
      both call.

Why the gate is a graph edge, not an if-statement inside generate.py:
      the gate must be able to skip stage 1 entirely — asking an LLM to
      answer from chunks that didn't clear the similarity threshold would
      spend a paid call on a question that gets refused anyway. A LangGraph
      conditional edge keeps that skip explicit, and keeps
      route_after_retrieve() testable as a plain function, independent of
      LangGraph. route_after_style_check() and route_after_verify() are the
      same shape for their respective retry loops.

Why a style-check failure still ends in status=answered, not a new status:
      messages.status's four values are fixed by CLAUDE.md, and
      'low_confidence' is not produced by anything built yet. A correct
      answer that is still a little too long for the grade should still
      reach the child; the failing StyleReport is stored on the message (in
      messages.readability) so error analysis can count these separately
      from a clean pass. 'refused_unverified' is a different failure mode —
      unsupported by the book, not merely hard to read — and verify_node can
      still downgrade an already-answered, style-passing message to it.

Why verify's retry rejoins the stage2 -> style_check loop instead of a
      separate stage2 -> verify path: a regenerated answer that fixes an
      unsupported sentence can just as easily come out too long or too hard
      for the grade, so it should be re-style-checked before it's shown, not
      just re-verified. style_attempts and verify_attempts are counted
      separately (style_max_retries and verify_max_retries are independent
      config values), so the two loops can't silently steal budget from each
      other; the worst case is bounded at
      (style_max_retries + 1) * (verify_max_retries + 1) stage 2 calls.

Why stage2_node reads state["retry_feedback"] instead of deriving it from
      state["style"] itself (as it did before verify.py existed): once
      verify_node exists, a retry back to stage 2 can be triggered by either
      check, and by the time it runs, state still holds the *previous*
      (passing) style report from the loop iteration verify just failed on.
      Deriving feedback from state["style"] directly would silently re-send
      stale, already-fixed style feedback instead of the verifier's actual
      complaint. Both style_check_node and verify_node now write
      retry_feedback every time they run (None on a pass) so it always
      reflects whichever check most recently failed.

Why refused_unverified keeps the generated (ungrounded) answer text rather
      than a canned refusal string: unlike the off-book gate, this fires
      after generation, and the actual hallucinated text is exactly what
      Phase 6's error analysis needs to see. Only `status` marks it as
      refused; how a client displays a refused_unverified message is a
      Phase 7 decision.

Why session_id is optional: the eval runner runs each question as an
      isolated turn with no session history to rewrite against — passing
      session_id=None makes rewrite_node skip straight past the LLM call
      (see rewrite.needs_rewrite: empty history never rewrites), so eval
      runs stay single-turn without a special-cased code path here.

Why pipeline_config exists (Phase 6 ablation, project-guidelines.md 8.2):
      the thesis needs four configurations run over the same code, not four
      separate scripts — A. Plain LLM, B. RAG baseline, C. RAG + grade
      adaptation, D. Full system. Rather than branch on it inside every
      node, three conditional edges route around whole nodes so each
      config is structurally unable to use what it's meant to ablate (A
      cannot see the book, B cannot see the style guide, C cannot see the
      verifier), not just told not to:
        - route_from_start: A skips rewrite/retrieve/the off-book gate
          entirely and goes straight to a bare LLM call (plain_llm_node) —
          contribution 1 (grounding + refusal) does not exist for A.
        - route_after_stage1: B stops right after stage 1 — its factual,
          ungraded answer *is* the shown text (finish_stage1_node), so
          contribution 2 (grade voice + style_check) never runs.
        - route_after_style_check: C now falls through to END once the
          style loop resolves instead of always continuing to verify —
          contribution 3 (the NLI verifier) only runs for D.
      Default is "D" everywhere it isn't threaded through explicitly (the
      GraphState field, run_pipeline()'s keyword argument), so the live API
      and every session created before this existed keep running the exact
      full pipeline they always did; only eval/runner.py's new
      --pipeline-config flag ever passes A, B or C.

Why is_self_refusal() is checked right off stage 1, not left to verify.py
      (Phase 6 fix, found via dev-split error analysis): unlike
      pipeline_config's branches above, this applies unconditionally to
      B/C/D alike — see route_after_stage1's docstring for the mechanism
      and NOTES.md's Phase 4 entry for the entailment quirk that made it
      necessary. It also protects config B, which never reaches verify.py
      at all and would otherwise show "The passages do not contain any
      information about X" as if it were a normal answered message.
"""

from __future__ import annotations

import asyncio
import re
import time
import uuid
from dataclasses import dataclass
from typing import Literal, TypedDict

from langgraph.graph import END, START, StateGraph

from app.core.config import get_settings
from app.models.message import MessageStatus
from app.pipeline.generate import generate_plain_llm, generate_stage1, generate_stage2
from app.pipeline.llm import LLMResult
from app.pipeline.retrieve import RetrievalResult, hybrid_search
from app.pipeline.rewrite import load_history, rewrite_question
from app.pipeline.style_check import StyleReport, check_style
from app.pipeline.verify import VerificationReport, verify_answer

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
    # Phase 6 ablation switch: "A" | "B" | "C" | "D" (see module docstring).
    # Absent (via state.get(..., "D")) means the full pipeline, same as
    # every session that existed before this field did.
    pipeline_config: str
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
    verification: VerificationReport
    verify_attempts: int
    retry_feedback: str | None


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


async def plain_llm_node(state: GraphState) -> dict:
    # Config A: no retrieval, no off-book gate, no grade voice, no
    # verifier — a bare LLM call is the ablation floor the other three
    # contributions are measured against.
    result = await asyncio.to_thread(
        generate_plain_llm, state["question"], provider=state.get("provider", "openai")
    )
    return {"answer": result.answer, "final_llm": result.llm, "status": MessageStatus.ANSWERED}


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


def _format_citation(retrieval: RetrievalResult) -> str:
    """"Unit N, Lesson M, page P" off the single best-matching chunk
    (top_chunks is RRF-sorted, so [0] is it) — the true source stage 2 is
    told to name, instead of inventing one (see generate.py's docstring on
    source_citation for why a plain string here is metadata, not a fact)."""
    if not retrieval.top_chunks:
        return "the source lesson"
    chunk = retrieval.top_chunks[0].chunk
    return f"Unit {chunk.unit}, Lesson {chunk.lesson_no}, page {chunk.page}"


async def stage2_node(state: GraphState) -> dict:
    result = await asyncio.to_thread(
        generate_stage2,
        state["search_query"],
        state["stage1_answer"],
        grade=state["grade"],
        is_first_turn=state.get("is_first_turn", True),
        provider=state.get("provider", "openai"),
        feedback=state.get("retry_feedback"),
        source_citation=_format_citation(state["retrieval"]),
    )
    return {"answer": result.answer, "final_llm": result.llm, "status": MessageStatus.ANSWERED}


async def finish_stage1_node(state: GraphState) -> dict:
    # Config B: stop right after stage 1 — its factual, ungraded answer is
    # shown as-is, so no stage-2 rewrite and no style_check ever run.
    return {"answer": state["stage1_answer"], "final_llm": state["stage1_llm"], "status": MessageStatus.ANSWERED}


async def style_check_node(state: GraphState) -> dict:
    attempts = state.get("style_attempts", 0) + 1
    report = await check_style(
        state["answer"],
        grade=state["grade"],
        book_id=state["book_id"],
        provider=state.get("provider", "openai"),
    )
    feedback = "; ".join(report.failures) if not report.passed else None
    return {"style": report, "style_attempts": attempts, "retry_feedback": feedback}


async def verify_node(state: GraphState) -> dict:
    attempts = state.get("verify_attempts", 0) + 1
    report = await verify_answer(state["answer"], state["retrieval"].context_chunks)
    feedback = None
    if not report.passed:
        unsupported = [s.sentence for s in report.sentences if not s.supported]
        feedback = (
            "The book does not support the following sentence(s): "
            + " | ".join(unsupported)
            + ". Remove or correct them without adding any new facts."
        )
    return {"verification": report, "verify_attempts": attempts, "retry_feedback": feedback}


async def mark_unverified_node(state: GraphState) -> dict:
    return {"status": MessageStatus.REFUSED_UNVERIFIED}


async def refuse_node(state: GraphState) -> dict:
    return {"answer": OFF_BOOK_REFUSAL, "status": MessageStatus.REFUSED_OFF_BOOK, "final_llm": None}


def route_from_start(state: GraphState) -> Literal["plain_llm", "full"]:
    """Config A skips rewrite/retrieve/the off-book gate and everything
    after them entirely — it is a bare LLM call with no book grounding."""
    if state.get("pipeline_config", "D") == "A":
        return "plain_llm"
    return "full"


# Stage 1's own prompt (v1_stage1.txt) tells it: "If the passages do not
# contain enough information to answer the question, say so plainly rather
# than guessing" — and it reliably complies in one of two fixed phrasings
# ("The passages provided do not contain any information about X.", "...do
# not have any information about X."). Phase 6 dev-split error analysis
# found this sentence itself scores spuriously high on NLI entailment
# (0.97-0.99) against unrelated book content — a self-referential-refusal
# quirk of the cross-encoder, already flagged as unresolved in NOTES.md's
# Phase 4 entry — so letting it reach verify_answer let 13/120 dev messages
# end up status=answered while the actual text was a refusal. Catching it
# here, right off stage 1's raw output, is a second content-based safety
# net alongside the retrieval-score gate: independent of whether the
# off-book gate's threshold happened to let a weak match through, stage 1
# itself gets the final say on whether it actually found an answer.
_SELF_REFUSAL_RE = re.compile(
    r"passages?\s*(provided)?\s*do(es)?\s*not\s*(contain|have)\s*(enough|any)?\s*information",
    re.IGNORECASE,
)


def is_self_refusal(stage1_answer: str) -> bool:
    return bool(_SELF_REFUSAL_RE.search(stage1_answer))


def route_after_stage1(state: GraphState) -> Literal["self_refusal", "stop", "continue"]:
    """Stage 1 declining to answer overrides everything else, regardless of
    pipeline_config — routes straight to the same refuse_node the off-book
    gate uses, skipping stage 2/style_check/verify entirely rather than
    style-voicing and then (maybe) mis-verifying a refusal. Otherwise config
    B stops here (its factual answer is shown as-is, no grade-voice rewrite
    or style_check); C and D continue to stage 2."""
    if is_self_refusal(state["stage1_answer"]):
        return "self_refusal"
    if state.get("pipeline_config", "D") == "B":
        return "stop"
    return "continue"


def route_after_retrieve(state: GraphState) -> Literal["generate", "refuse"]:
    """Pure routing decision, testable without LangGraph: below the
    off-book threshold, skip generation entirely."""
    threshold = get_settings().offbook_score_threshold
    if state["retrieval"].best_score < threshold:
        return "refuse"
    return "generate"


def route_after_style_check(state: GraphState) -> Literal["retry", "verify", "end"]:
    """Pure routing decision: retry stage 2 (feeding back what failed) up to
    style_max_retries times AFTER the first attempt — so style_max_retries=2
    allows 3 attempts total — then stop trying. See the module docstring on
    why a still-failing answer ends as 'answered', not a new status.

    Once the retry loop resolves (pass, or out of retries), config D
    continues to the verifier; config C (grade adaptation without the
    verifier) goes straight to END instead — see the module docstring's
    pipeline_config section."""
    report = state["style"]
    if not report.passed and state["style_attempts"] <= get_settings().style_max_retries:
        return "retry"
    return "verify" if state.get("pipeline_config", "D") == "D" else "end"


def route_after_verify(state: GraphState) -> Literal["retry", "supported", "unverified"]:
    """Pure routing decision: below verify_supported_ratio, regenerate
    stage 2 (retry_feedback names which sentences the book doesn't support)
    up to verify_max_retries times AFTER the first check, then give up and
    refuse rather than show an answer the book doesn't back."""
    report = state["verification"]
    if report.passed:
        return "supported"
    if state["verify_attempts"] > get_settings().verify_max_retries:
        return "unverified"
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
_graph.add_node("plain_llm", plain_llm_node)
_graph.add_node("rewrite", rewrite_node)
_graph.add_node("retrieve", retrieve_node)
_graph.add_node("stage1", stage1_node)
_graph.add_node("finish_stage1", finish_stage1_node)
_graph.add_node("stage2", stage2_node)
_graph.add_node("style_check", style_check_node)
_graph.add_node("verify", verify_node)
_graph.add_node("mark_unverified", mark_unverified_node)
_graph.add_node("refuse", refuse_node)
_graph.add_conditional_edges(START, route_from_start, {"plain_llm": "plain_llm", "full": "rewrite"})
_graph.add_edge("plain_llm", END)
_graph.add_edge("rewrite", "retrieve")
_graph.add_conditional_edges("retrieve", route_after_retrieve, {"generate": "stage1", "refuse": "refuse"})
_graph.add_conditional_edges(
    "stage1", route_after_stage1, {"self_refusal": "refuse", "stop": "finish_stage1", "continue": "stage2"}
)
_graph.add_edge("finish_stage1", END)
_graph.add_edge("stage2", "style_check")
_graph.add_conditional_edges(
    "style_check", route_after_style_check, {"retry": "stage2", "verify": "verify", "end": END}
)
_graph.add_conditional_edges(
    "verify", route_after_verify, {"retry": "stage2", "supported": END, "unverified": "mark_unverified"}
)
_graph.add_edge("mark_unverified", END)
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
    verification: VerificationReport | None
    verify_attempts: int


async def run_pipeline(
    question: str,
    grade: int,
    book_id: uuid.UUID,
    *,
    session_id: uuid.UUID | None = None,
    provider: str = "openai",
    pipeline_config: str = "D",
) -> PipelineResult:
    start = time.monotonic()
    final_state = await _compiled.ainvoke(
        {
            "question": question,
            "grade": grade,
            "book_id": book_id,
            "session_id": session_id,
            "provider": provider,
            "pipeline_config": pipeline_config,
        }
    )
    latency_ms = int((time.monotonic() - start) * 1000)

    # Config A never runs retrieve_node, so `retrieval` is absent, not just
    # empty — every field that's normally read off it falls back to the
    # "no book was consulted" shape instead.
    retrieval: RetrievalResult | None = final_state.get("retrieval")
    return PipelineResult(
        answer=final_state["answer"],
        status=final_state["status"],
        sources=build_sources(retrieval) if retrieval is not None else [],
        best_score=retrieval.best_score if retrieval is not None else 0.0,
        latency_ms=latency_ms,
        config_version=get_settings().config_version,
        llm=final_state.get("final_llm"),
        context_texts=[c.text for c in retrieval.context_chunks] if retrieval is not None else [],
        search_query=final_state.get("search_query", question),
        stage1_answer=final_state.get("stage1_answer"),
        style=final_state.get("style"),
        style_attempts=final_state.get("style_attempts", 0),
        verification=final_state.get("verification"),
        verify_attempts=final_state.get("verify_attempts", 0),
    )
