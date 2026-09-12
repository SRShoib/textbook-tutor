"""
What: the eval CLI. Runs every question in a questions.jsonl through the
      same pipeline.graph.run_pipeline() the API uses, writes each Q/A pair
      to the messages table (config_version set, same as a live session)
      and to a timestamped JSONL file, then prints retrieval hit rate and
      RAGAS faithfulness.

Why it writes straight to the DB via SQLAlchemy instead of calling the API:
      CLAUDE.md requires "the API, the eval runner, and a notebook must all
      call the same pipeline functions" — this drives pipeline/graph.py
      directly, the same as api/sessions.py does, rather than going through
      HTTP for no reason.

Usage:
      python -m eval.runner --book-id <uuid> --config v1
                             [--questions PATH] [--split dev] [--limit N]
                             [--provider openai|ollama]
                             [--pipeline-config A|B|C|D]

Why --pipeline-config is separate from --config: --config only ever tags
      config_version, a free-text label stored on every message row and in
      the run manifest — CLAUDE.md fixes that column's meaning but not its
      value. --pipeline-config is new for Phase 6: it is the actual ablation
      switch (project-guidelines.md 8.2's A/B/C/D), threaded straight into
      graph.py's run_pipeline(), and it defaults to "D" (today's exact full
      pipeline) so a plain `--config v1` run behaves exactly as it always
      has. Passing --pipeline-config A|B|C sends the run through a
      different, narrower slice of the same graph (see graph.py's
      pipeline_config docstring) rather than switching code paths.
"""

from __future__ import annotations

import argparse
import asyncio
import subprocess
import time
import uuid
from dataclasses import asdict
from datetime import datetime, timezone
from pathlib import Path

from app.core.auth import get_or_create_dev_user
from app.core.config import get_settings
from app.core.db import AsyncSessionLocal
from app.models.book import Book
from app.models.message import Message, MessageRole
from app.models.session import Session
from app.pipeline.generate import STAGE1_PROMPT_VERSION, STAGE2_PROMPT_VERSION
from app.pipeline.graph import run_pipeline
from app.pipeline.llm import get_stats, reset_stats
from app.pipeline.rewrite import REWRITE_PROMPT_VERSION
from app.pipeline.style_check import STYLE_JUDGE_PROMPT_VERSION
from eval.metrics import (
    false_refusal_rate,
    hallucination_rate,
    ragas_faithfulness,
    read_jsonl,
    readability_summary,
    refusal_accuracy,
    retrieval_hit_rate,
    severe_hallucination_rate,
    style_summary,
    write_jsonl,
)

_DEFAULT_QUESTIONS = Path(__file__).resolve().parents[2] / "data" / "question_set" / "questions.jsonl"
_RUNS_DIR = Path(__file__).resolve().parent / "runs"


def _git_commit() -> str:
    """Read-only — never mutates the repo. Falls back to 'unknown' when
    there is no git repository yet (true of this project as of Phase 2)."""
    try:
        result = subprocess.run(
            ["git", "rev-parse", "HEAD"], capture_output=True, text=True, timeout=5, check=True
        )
        return result.stdout.strip()
    except Exception:
        return "unknown"


async def _create_eval_session(book: Book, config_version: str) -> Session:
    async with AsyncSessionLocal() as db:
        user = await get_or_create_dev_user(db)
        session = Session(
            user_id=user.id,
            book_id=book.id,
            grade=book.grade,
            title=f"eval: {config_version} {datetime.now(timezone.utc).isoformat()}",
        )
        db.add(session)
        await db.commit()
        await db.refresh(session)
        return session


async def _store_message_pair(session_id: uuid.UUID, question: str, config_version: str, result) -> None:
    async with AsyncSessionLocal() as db:
        db.add(Message(session_id=session_id, role=MessageRole.USER, content=question))
        db.add(
            Message(
                session_id=session_id,
                role=MessageRole.ASSISTANT,
                content=result.answer,
                status=result.status,
                sources=result.sources,
                readability=asdict(result.style) if result.style is not None else None,
                config_version=config_version,
                latency_ms=result.latency_ms,
            )
        )
        await db.commit()


async def run_eval(
    *,
    book_id: uuid.UUID,
    config_version: str,
    questions_path: Path,
    split: str | None,
    limit: int | None,
    provider: str,
    pipeline_config: str = "D",
) -> Path:
    settings = get_settings()
    reset_stats()

    questions = read_jsonl(questions_path)
    if split:
        questions = [q for q in questions if q.get("split") == split]
    if limit:
        questions = questions[:limit]
    if not questions:
        raise ValueError(f"No questions to run (path={questions_path}, split={split!r}).")

    async with AsyncSessionLocal() as db:
        book = await db.get(Book, book_id)
    if book is None:
        raise ValueError(f"No book with id {book_id}.")

    eval_session = await _create_eval_session(book, config_version)

    records = []
    wall_start = time.monotonic()
    for q in questions:
        result = await run_pipeline(
            q["question"],
            grade=q.get("grade", book.grade),
            book_id=book_id,
            provider=provider,
            pipeline_config=pipeline_config,
        )
        await _store_message_pair(eval_session.id, q["question"], config_version, result)
        records.append(
            {
                "id": q.get("id"),
                "question": q["question"],
                # Not stored on the messages row (CLAUDE.md's schema is
                # fixed and has no column for it) — the eval JSONL is where
                # a follow-up's rewrite is auditable. Equals `question`
                # whenever rewrite.needs_rewrite() skipped the LLM call
                # (every eval turn is single-turn today: session_id=None).
                "search_query": result.search_query,
                "answer": result.answer,
                # None on the off-book-gate refusal path (stage 1 never
                # ran). Kept for auditability — e.g. confirming a
                # self-refusal (graph.is_self_refusal) fired on stage 1's
                # own words, not on stage 2's rephrasing of them.
                "stage1_answer": result.stage1_answer,
                "status": result.status.value,
                "best_score": result.best_score,
                "latency_ms": result.latency_ms,
                "retrieved_lesson_ids": [s["lesson_id"] for s in result.sources],
                "context_texts": result.context_texts,
                # questions.jsonl's ground-truth column is named lesson_id;
                # kept as expected_lesson_id here since that's what a
                # *retrieved* lesson_id is being checked against — see
                # metrics.retrieval_hit_rate().
                "expected_lesson_id": q.get("lesson_id"),
                "reference_answer": q.get("reference_answer"),
                "type": q.get("type"),
                "style": asdict(result.style) if result.style is not None else None,
                "style_attempts": result.style_attempts,
                # Absent for configs B/C, which never run verify_node by
                # design (see graph.py's pipeline_config docstring) — that
                # is what makes hallucination_rate() report None for them
                # instead of a misleading 0%.
                "verification": asdict(result.verification) if result.verification is not None else None,
            }
        )
    wall_time_s = time.monotonic() - wall_start

    hit_rate, hit_rate_n = retrieval_hit_rate(records)
    faithfulness, faithfulness_n = await ragas_faithfulness(records, provider=provider)
    stats = get_stats()

    status_counts: dict[str, int] = {}
    for r in records:
        status_counts[r["status"]] = status_counts.get(r["status"], 0) + 1

    manifest = {
        "config_version": config_version,
        "pipeline_config": pipeline_config,
        "provider": provider,
        "model": settings.openai_model if provider == "openai" else settings.ollama_model,
        "embedding_model": settings.embedding_model,
        "prompt_versions": {
            "stage1": STAGE1_PROMPT_VERSION,
            "stage2": STAGE2_PROMPT_VERSION,
            "rewrite": REWRITE_PROMPT_VERSION,
            "style_judge": STYLE_JUDGE_PROMPT_VERSION,
        },
        "thresholds": {
            "offbook_score_threshold": settings.offbook_score_threshold,
            "retrieval_top_k": settings.retrieval_top_k,
            "style_max_retries": settings.style_max_retries,
            "style_max_sentence_words": settings.style_max_sentence_words,
            "style_fk_min": settings.style_fk_min,
            "style_fk_max": settings.style_fk_max,
            "style_vocab_coverage_min": settings.style_vocab_coverage_min,
        },
        "git_commit": _git_commit(),
        "book_id": str(book_id),
        "session_id": str(eval_session.id),
        "questions_path": str(questions_path),
        "split": split,
        "n_questions": len(questions),
        "run_at": datetime.now(timezone.utc).isoformat(),
    }

    timestamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    out_path = _RUNS_DIR / f"{config_version}_{timestamp}.jsonl"
    write_jsonl(out_path, [manifest, *records])

    print(f"Eval run: {config_version} ({provider}), pipeline_config={pipeline_config}, {len(questions)} questions")
    print(f"Status breakdown: {status_counts}")
    if hit_rate is not None:
        print(f"Retrieval hit rate @{settings.retrieval_top_k}: {hit_rate:.2f} (n={hit_rate_n})")
    else:
        print("Retrieval hit rate: no questions have an expected_lesson_id")
    if faithfulness is not None:
        print(f"RAGAS faithfulness: {faithfulness:.2f} (n={faithfulness_n})")
    else:
        print("RAGAS faithfulness: no answered questions to score")
    refusal_acc, refusal_acc_n = refusal_accuracy(records)
    if refusal_acc is not None:
        print(f"Refusal accuracy (off-book): {refusal_acc:.2f} (n={refusal_acc_n})")
    else:
        print("Refusal accuracy: no off_book questions in this run")
    false_refusal, false_refusal_n = false_refusal_rate(records)
    if false_refusal is not None:
        print(f"False refusal rate (answerable): {false_refusal:.2f} (n={false_refusal_n})")
    else:
        print("False refusal rate: no answerable questions in this run")
    halluc_rate, halluc_n = hallucination_rate(records)
    if halluc_rate is not None:
        print(f"Hallucination rate (unsupported sentences, mean): {halluc_rate:.2f} (n={halluc_n})")
    else:
        print("Hallucination rate: no verification reports in this run (pipeline_config B/C skip the verifier)")
    severe_rate, severe_n = severe_hallucination_rate(records)
    if severe_rate is not None:
        print(f"Hallucination rate (severe -- nothing in the message supported): {severe_rate:.2f} (n={severe_n})")
    style_stats = style_summary(records)
    if style_stats is not None:
        fk_display = style_stats["mean_fk"] if style_stats["mean_fk"] is not None else "n/a"
        print(
            f"Style: {style_stats['pass_rate']:.2f} pass rate (n={style_stats['n']}), "
            f"mean FK {fk_display}, mean max-sentence {style_stats['mean_max_sentence_words']} words, "
            f"mean vocab coverage {style_stats['mean_vocab_coverage']:.2f}"
        )
    else:
        print("Style: no answered questions with a style report to summarize (pipeline_config A/B skip style_check)")
    readability = readability_summary(records)
    if readability is not None:
        fk_display = readability["mean_fk"] if readability["mean_fk"] is not None else "n/a"
        print(
            f"Readability (answer text, any config): mean FK {fk_display} (n={readability['fk_n']}), "
            f"mean sentence length {readability['mean_sentence_words']} words, "
            f"mean max-sentence {readability['mean_max_sentence_words']} words (n={readability['n']})"
        )
    else:
        print("Readability: no answered questions to score")
    print(
        f"LLM calls: {stats['calls']} ({stats['hits']} cache hits, {stats['misses']} misses), "
        f"{stats['prompt_tokens']} prompt tokens, {stats['completion_tokens']} completion tokens"
    )
    print(f"Wall time: {wall_time_s:.1f}s")
    print(f"Results written to {out_path}")

    return out_path


def main() -> None:
    parser = argparse.ArgumentParser(description="Run the question set through the pipeline and score it.")
    parser.add_argument("--book-id", type=uuid.UUID, required=True)
    parser.add_argument("--config", dest="config_version", required=True, help="config_version to record on messages")
    parser.add_argument("--questions", type=Path, default=_DEFAULT_QUESTIONS)
    parser.add_argument("--split", default=None, help="e.g. dev or test; omit to run every row")
    parser.add_argument("--limit", type=int, default=None)
    parser.add_argument("--provider", default="openai", choices=["openai", "ollama"])
    parser.add_argument(
        "--pipeline-config",
        dest="pipeline_config",
        default="D",
        choices=["A", "B", "C", "D"],
        help="project-guidelines.md 8.2 ablation: A=plain LLM, B=RAG only, C=+grade voice, D=full system (default)",
    )
    args = parser.parse_args()

    asyncio.run(
        run_eval(
            book_id=args.book_id,
            config_version=args.config_version,
            questions_path=args.questions,
            split=args.split,
            limit=args.limit,
            provider=args.provider,
            pipeline_config=args.pipeline_config,
        )
    )


if __name__ == "__main__":
    main()
