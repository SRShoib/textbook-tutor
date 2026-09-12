"""
What: hybrid dense+sparse retrieval over one book's chunks, fused with
      reciprocal rank fusion, then expanded to each hit's full parent lesson.

Why brute-force in Python, not pgvector's index or Postgres full-text search:
      pgvector 0.3.6 (pinned in requirements.txt) has no sparse vector type,
      so bge-m3's lexical weights (chunks.sparse) cannot be scored in SQL at
      all — they only exist as a JSONB dict of {token: weight}. Splitting
      scoring across two places (pgvector for dense, something else for
      sparse) would mean two different notions of "top-k" to keep in sync.
      Instead both dense and sparse scores are computed in Python over every
      chunk in the book. At today's ~135 chunks this is microseconds; if the
      corpus ever grows into the thousands, this is the point to add a
      pgvector ANN index for dense and a real inverted index for sparse.

Why the off-book gate reads raw dense cosine similarity, not the RRF score:
      OFFBOOK_SCORE_THRESHOLD (0.35) only means something on a 0-1 similarity
      scale. RRF's fused score is a sum of 1/(k+rank) terms with no such
      interpretation (it tops out around 0.033 for two rankers), so
      best_score is the single highest dense cosine similarity anywhere in
      the book — "is anything in this book even close to this question?" —
      independent of whichever chunk the fused ranking puts first.
"""

from __future__ import annotations

import asyncio
import uuid
from dataclasses import dataclass

from sqlalchemy import select

from app.core.config import get_settings
from app.core.db import AsyncSessionLocal
from app.models.chunk import Chunk
from app.pipeline.embeddings import embed_texts

RRF_K = 60  # standard constant, Cormack, Clarke & Buettcher (2009)


@dataclass(frozen=True)
class ScoredChunk:
    chunk: Chunk
    dense_score: float
    sparse_score: float
    dense_rank: int
    sparse_rank: int
    rrf_score: float


@dataclass(frozen=True)
class RetrievalResult:
    top_chunks: list[ScoredChunk]  # top-k by RRF, pre-expansion — what messages.sources cites
    context_chunks: list[Chunk]  # top hits' full parent lessons — what the LLM reads
    best_score: float  # highest dense cosine similarity in the book, for the off-book gate


# --- pure scoring functions --------------------------------------------------


def dense_score(query_vec: list[float], chunk_vec: list[float]) -> float:
    """Cosine similarity. bge-m3's dense vectors are L2-normalized by the
    model, so a plain dot product already equals cosine similarity."""
    return sum(q * c for q, c in zip(query_vec, chunk_vec))


def sparse_score(query_sparse: dict[str, float], chunk_sparse: dict[str, float]) -> float:
    """Dot product over shared tokens between two bge-m3 lexical-weight
    dicts. Iterates the smaller dict so the cost scales with whichever side
    has fewer terms."""
    if len(query_sparse) > len(chunk_sparse):
        query_sparse, chunk_sparse = chunk_sparse, query_sparse
    return sum(weight * chunk_sparse[token] for token, weight in query_sparse.items() if token in chunk_sparse)


def rank_desc(scores: dict) -> dict:
    """1-indexed rank of each id by descending score. Ties break on the id's
    own string form so ranking is deterministic regardless of dict/set
    iteration order."""
    ordered = sorted(scores.items(), key=lambda item: (-item[1], str(item[0])))
    return {chunk_id: rank for rank, (chunk_id, _) in enumerate(ordered, start=1)}


def reciprocal_rank_fusion(dense_ranks: dict, sparse_ranks: dict, k: int = RRF_K) -> dict:
    """RRF(id) = 1/(k + dense_rank) + 1/(k + sparse_rank). Both inputs are
    expected to rank every candidate id (see rank_desc) — the corpus is
    small enough that truncating either ranking before fusing would only add
    an arbitrary cutoff with nothing to justify it."""
    ids = set(dense_ranks) | set(sparse_ranks)
    return {chunk_id: 1.0 / (k + dense_ranks[chunk_id]) + 1.0 / (k + sparse_ranks[chunk_id]) for chunk_id in ids}


def expand_to_parent_lessons(top_chunks: list[ScoredChunk], all_chunks: list[Chunk]) -> list[Chunk]:
    """Every chunk sharing a lesson_id with a top hit joins the context,
    ordered unit -> lesson_no -> page so the LLM reads each lesson in the
    order it appears in the book. On this book (one chunk per lesson today,
    per NOTES.md) this is usually a no-op; it is what makes the 600-word
    chunk split safe if a future book needs it."""
    wanted_lesson_ids = {sc.chunk.lesson_id for sc in top_chunks}
    matched = [c for c in all_chunks if c.lesson_id in wanted_lesson_ids]
    return sorted(matched, key=lambda c: (c.unit, c.lesson_no, c.page))


# --- orchestration (touches the DB and the embedding model) ----------------


async def hybrid_search(book_id: uuid.UUID, query: str, top_k: int | None = None) -> RetrievalResult:
    settings = get_settings()
    k = top_k or settings.retrieval_top_k

    async with AsyncSessionLocal() as session:
        result = await session.execute(select(Chunk).where(Chunk.book_id == book_id))
        all_chunks = list(result.scalars().all())

    if not all_chunks:
        return RetrievalResult(top_chunks=[], context_chunks=[], best_score=0.0)

    # bge-m3's .encode() is a blocking torch call; run it off the event loop
    # the same way ingest_book() offloads build_chunks().
    dense_list, sparse_list = await asyncio.to_thread(embed_texts, [query])
    query_dense, query_sparse = dense_list[0], sparse_list[0]

    dense_scores = {c.id: dense_score(query_dense, c.embedding) for c in all_chunks}
    sparse_scores = {c.id: sparse_score(query_sparse, c.sparse) for c in all_chunks}
    dense_ranks = rank_desc(dense_scores)
    sparse_ranks = rank_desc(sparse_scores)
    rrf_scores = reciprocal_rank_fusion(dense_ranks, sparse_ranks)

    chunks_by_id = {c.id: c for c in all_chunks}
    top_ids = sorted(rrf_scores, key=lambda cid: (-rrf_scores[cid], str(cid)))[:k]
    top_chunks = [
        ScoredChunk(
            chunk=chunks_by_id[cid],
            dense_score=dense_scores[cid],
            sparse_score=sparse_scores[cid],
            dense_rank=dense_ranks[cid],
            sparse_rank=sparse_ranks[cid],
            rrf_score=rrf_scores[cid],
        )
        for cid in top_ids
    ]

    context_chunks = expand_to_parent_lessons(top_chunks, all_chunks)
    best_score = max(dense_scores.values())

    return RetrievalResult(top_chunks=top_chunks, context_chunks=context_chunks, best_score=best_score)
