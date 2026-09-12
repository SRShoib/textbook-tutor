"""Tests for pipeline/retrieve.py's pure scoring/fusion/expansion functions.
CLAUDE.md: test pipeline/ functions, skip route tests. hybrid_search() itself
(DB + embedding model) is intentionally left untested here, same as
ingest_book() in test_ingest.py — it is thin orchestration over functions
that are each tested directly below, and gets verified by eye against a real
book per the plan's Verification section."""

import uuid

import pytest

from app.models.chunk import Chunk, ChunkType
from app.pipeline import retrieve


def make_chunk(*, lesson_id: str, unit: int, lesson_no: int, page: int, text: str = "") -> Chunk:
    return Chunk(
        id=uuid.uuid4(),
        book_id=None,
        lesson_id=lesson_id,
        unit=unit,
        lesson_no=lesson_no,
        lesson_title="Title",
        page=page,
        type=ChunkType.PASSAGE,
        text=text,
        embedding=[0.0] * 1024,
        sparse={},
    )


def make_scored(chunk: Chunk) -> retrieve.ScoredChunk:
    return retrieve.ScoredChunk(
        chunk=chunk, dense_score=0.0, sparse_score=0.0, dense_rank=1, sparse_rank=1, rrf_score=0.0
    )


# --- dense_score -------------------------------------------------------


def test_dense_score_identical_vectors_is_one():
    assert retrieve.dense_score([0.6, 0.8], [0.6, 0.8]) == pytest.approx(1.0)


def test_dense_score_orthogonal_vectors_is_zero():
    assert retrieve.dense_score([1.0, 0.0], [0.0, 1.0]) == pytest.approx(0.0)


# --- sparse_score --------------------------------------------------------


def test_sparse_score_dot_product_over_shared_tokens_only():
    query = {"t1": 0.5, "t2": 0.3}
    chunk = {"t1": 0.4, "t3": 0.9}
    assert retrieve.sparse_score(query, chunk) == pytest.approx(0.5 * 0.4)


def test_sparse_score_symmetric_regardless_of_which_dict_is_larger():
    small = {"t1": 0.5}
    large = {"t1": 0.4, "t2": 0.1, "t3": 0.2}
    assert retrieve.sparse_score(small, large) == retrieve.sparse_score(large, small)


def test_sparse_score_no_overlap_is_zero():
    assert retrieve.sparse_score({"t1": 0.5}, {"t2": 0.5}) == 0.0


# --- rank_desc -----------------------------------------------------------


def test_rank_desc_orders_by_descending_score():
    ranks = retrieve.rank_desc({"a": 0.9, "b": 0.5, "c": 0.7})
    assert ranks == {"a": 1, "c": 2, "b": 3}


def test_rank_desc_breaks_ties_deterministically_by_id():
    ranks = retrieve.rank_desc({"c": 0.9, "a": 0.9, "b": 0.1})
    assert ranks["a"] == 1  # "a" < "c" alphabetically
    assert ranks["c"] == 2
    assert ranks["b"] == 3


# --- reciprocal_rank_fusion ------------------------------------------------


def test_reciprocal_rank_fusion_matches_hand_computed_formula():
    dense_ranks = {"a": 1, "b": 2, "c": 3}
    sparse_ranks = {"a": 3, "b": 1, "c": 2}
    result = retrieve.reciprocal_rank_fusion(dense_ranks, sparse_ranks, k=60)
    for cid in ("a", "b", "c"):
        expected = 1.0 / (60 + dense_ranks[cid]) + 1.0 / (60 + sparse_ranks[cid])
        assert result[cid] == pytest.approx(expected)


def test_reciprocal_rank_fusion_defaults_to_module_rrf_k():
    dense_ranks = {"a": 1}
    sparse_ranks = {"a": 1}
    result = retrieve.reciprocal_rank_fusion(dense_ranks, sparse_ranks)
    assert result["a"] == pytest.approx(2.0 / (retrieve.RRF_K + 1))


def test_reciprocal_rank_fusion_rewards_agreement_between_rankers():
    # "a" ranks 1st in both lists; "b" ranks 1st in one and last in the other.
    # RRF should prefer consistent agreement over a single strong signal.
    dense_ranks = {"a": 1, "b": 1000}
    sparse_ranks = {"a": 1, "b": 1}
    # give "b" a chance to win on sparse alone despite dense rank 1000
    result = retrieve.reciprocal_rank_fusion(dense_ranks, sparse_ranks)
    assert result["a"] > result["b"]


# --- expand_to_parent_lessons ---------------------------------------------


def test_expand_to_parent_lessons_pulls_in_sibling_chunks_same_lesson():
    c1 = make_chunk(lesson_id="u1-s1", unit=1, lesson_no=1, page=6)
    c2 = make_chunk(lesson_id="u1-s1", unit=1, lesson_no=1, page=5)  # earlier page, later in list
    other_lesson = make_chunk(lesson_id="u1-s2", unit=1, lesson_no=2, page=7)
    all_chunks = [c1, c2, other_lesson]

    expanded = retrieve.expand_to_parent_lessons([make_scored(c1)], all_chunks)

    assert expanded == [c2, c1]  # both u1-s1 chunks, ordered by page ascending


def test_expand_to_parent_lessons_excludes_other_lessons():
    wanted = make_chunk(lesson_id="u1-s1", unit=1, lesson_no=1, page=5)
    unwanted = make_chunk(lesson_id="u2-s1", unit=2, lesson_no=1, page=10)

    expanded = retrieve.expand_to_parent_lessons([make_scored(wanted)], [wanted, unwanted])

    assert expanded == [wanted]


def test_expand_to_parent_lessons_orders_multiple_lessons_by_unit_then_lesson_no():
    a = make_chunk(lesson_id="u2-s1", unit=2, lesson_no=1, page=10)
    b = make_chunk(lesson_id="u1-s2", unit=1, lesson_no=2, page=8)
    c = make_chunk(lesson_id="u1-s1", unit=1, lesson_no=1, page=5)
    all_chunks = [a, b, c]

    expanded = retrieve.expand_to_parent_lessons([make_scored(a), make_scored(b), make_scored(c)], all_chunks)

    assert expanded == [c, b, a]
