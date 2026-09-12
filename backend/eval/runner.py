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
"""

from __future__ import annotations

import argparse
import asyncio
import subprocess
import time
import uuid
from datetime import datetime, timezone
from pathlib import Path

from app.core.auth import get_or_create_dev_user
from app.core.config import get_settings
from app.core.db import AsyncSessionLocal
from app.models.book import Book
from app.models.message import Message, MessageRole
from app.models.session import Session
from app.pipeline.graph import run_pipeline
from app.pipeline.llm import get_stats, reset_stats
from eval.metrics import ragas_faithfulness, read_jsonl, retrieval_hit_rate, write_jsonl

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
            q["question"], grade=q.get("grade", book.grade), book_id=book_id, provider=provider
        )
        await _store_message_pair(eval_session.id, q["question"], config_version, result)
        records.append(
            {
                "id": q.get("id"),
                "question": q["question"],
                "answer": result.answer,
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
        "provider": provider,
        "model": settings.openai_model if provider == "openai" else settings.ollama_model,
        "embedding_model": settings.embedding_model,
        "prompt_versions": {"stage1": "v1_stage1"},
        "thresholds": {
            "offbook_score_threshold": settings.offbook_score_threshold,
            "retrieval_top_k": settings.retrieval_top_k,
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

    print(f"Eval run: {config_version} ({provider}), {len(questions)} questions")
    print(f"Status breakdown: {status_counts}")
    if hit_rate is not None:
        print(f"Retrieval hit rate @{settings.retrieval_top_k}: {hit_rate:.2f} (n={hit_rate_n})")
    else:
        print("Retrieval hit rate: no questions have an expected_lesson_id")
    if faithfulness is not None:
        print(f"RAGAS faithfulness: {faithfulness:.2f} (n={faithfulness_n})")
    else:
        print("RAGAS faithfulness: no answered questions to score")
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
    args = parser.parse_args()

    asyncio.run(
        run_eval(
            book_id=args.book_id,
            config_version=args.config_version,
            questions_path=args.questions,
            split=args.split,
            limit=args.limit,
            provider=args.provider,
        )
    )


if __name__ == "__main__":
    main()
