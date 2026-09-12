"""Tests for pipeline/verify.py. The NLI cross-encoder is monkeypatched via
get_nli_model — no real model is loaded in this file.
CLAUDE.md: test pipeline/ functions, skip route tests."""

import uuid

import pytest

from app.core.config import get_settings
from app.models.chunk import Chunk, ChunkType
from app.pipeline import verify


def make_chunk(lesson_id: str, text: str) -> Chunk:
    return Chunk(
        id=uuid.uuid4(),
        book_id=None,
        lesson_id=lesson_id,
        unit=1,
        lesson_no=1,
        lesson_title="Our School",
        page=5,
        type=ChunkType.PASSAGE,
        text=text,
        embedding=[0.0] * 1024,
        sparse={},
    )


class FakeCrossEncoder:
    """score_map: {(chunk_text, sentence): entailment_probability}. Any pair
    not in the map defaults to 0.0 entailment. Mirrors the real model's
    [contradiction, entailment, neutral] label order."""

    def __init__(self, score_map: dict[tuple[str, str], float]):
        self.score_map = score_map
        self.calls: list[list[tuple[str, str]]] = []

    def predict(self, pairs, apply_softmax=True):
        self.calls.append(list(pairs))
        rows = []
        for premise, hypothesis in pairs:
            p = self.score_map.get((premise, hypothesis), 0.0)
            rows.append([1.0 - p, p, 0.0])
        return rows


@pytest.fixture(autouse=True)
def restore_verify_settings():
    settings = get_settings()
    original = {
        "verify_entailment_threshold": settings.verify_entailment_threshold,
        "verify_supported_ratio": settings.verify_supported_ratio,
    }
    yield
    for k, v in original.items():
        setattr(settings, k, v)
    verify._nli_model = None


# --- _premise_sentences -----------------------------------------------------


def test_premise_sentences_strips_header_and_splits_multi_sentence_chunks():
    chunk = make_chunk(
        "u2-s1",
        "Unit 2, Lesson 1: Our School Garden\n"
        "Our school has a lovely garden. We use a set of gardening tools "
        "like spades, rakes, and sickles. We also grow flowers.",
    )
    pairs = verify._premise_sentences([chunk])
    assert pairs == [
        ("u2-s1", "Our school has a lovely garden."),
        ("u2-s1", "We use a set of gardening tools like spades, rakes, and sickles."),
        ("u2-s1", "We also grow flowers."),
    ]


# --- verify_sentences -------------------------------------------------------


def test_verify_sentences_finds_a_fact_buried_in_a_multi_sentence_chunk(monkeypatch):
    # The exact shape of the bug found in the Phase 4 live check: a fact
    # that is only one sentence among several in its chunk must still score
    # as entailed, not get diluted by scoring the whole chunk as one premise.
    chunk = make_chunk(
        "u2-s1",
        "Unit 2, Lesson 1: Our School Garden\n"
        "Our school has a lovely garden. We use a set of gardening tools "
        "like spades, rakes, and sickles. We also grow flowers.",
    )
    hypothesis = "They use tools like spades, rakes, and sickles to clean the school garden."
    fake = FakeCrossEncoder(
        {
            ("Our school has a lovely garden.", hypothesis): 0.01,
            ("We use a set of gardening tools like spades, rakes, and sickles.", hypothesis): 0.95,
            ("We also grow flowers.", hypothesis): 0.02,
        }
    )
    monkeypatch.setattr(verify, "get_nli_model", lambda: fake)
    get_settings().verify_entailment_threshold = 0.5

    results = verify.verify_sentences([hypothesis], [chunk])

    assert results[0].entailment_score == pytest.approx(0.95)
    assert results[0].supported is True
    assert results[0].best_lesson_id == "u2-s1"
    # 3 premise sentences (header dropped) x 1 hypothesis
    assert len(fake.calls[0]) == 3


def test_verify_sentences_keeps_max_entailment_across_chunks(monkeypatch):
    chunk_a = make_chunk("u1-s1", "Sumon lives in a village.")
    chunk_b = make_chunk("u1-s2", "Sumon goes to school every day.")
    fake = FakeCrossEncoder(
        {
            (chunk_a.text, "Sumon goes to school."): 0.2,
            (chunk_b.text, "Sumon goes to school."): 0.9,
        }
    )
    monkeypatch.setattr(verify, "get_nli_model", lambda: fake)
    get_settings().verify_entailment_threshold = 0.5

    results = verify.verify_sentences(["Sumon goes to school."], [chunk_a, chunk_b])

    assert len(results) == 1
    assert results[0].entailment_score == pytest.approx(0.9)
    assert results[0].supported is True
    assert results[0].best_lesson_id == "u1-s2"


def test_verify_sentences_marks_below_threshold_as_unsupported(monkeypatch):
    chunk = make_chunk("u1-s1", "Sumon lives in a village.")
    fake = FakeCrossEncoder({(chunk.text, "Sumon owns a car."): 0.1})
    monkeypatch.setattr(verify, "get_nli_model", lambda: fake)
    get_settings().verify_entailment_threshold = 0.5

    results = verify.verify_sentences(["Sumon owns a car."], [chunk])

    assert results[0].supported is False
    assert results[0].entailment_score == pytest.approx(0.1)


def test_verify_sentences_batches_all_pairs_in_one_predict_call(monkeypatch):
    chunk_a = make_chunk("u1-s1", "Text A.")
    chunk_b = make_chunk("u1-s2", "Text B.")
    fake = FakeCrossEncoder({})
    monkeypatch.setattr(verify, "get_nli_model", lambda: fake)

    verify.verify_sentences(["Sentence one.", "Sentence two."], [chunk_a, chunk_b])

    assert len(fake.calls) == 1
    assert len(fake.calls[0]) == 4  # 2 sentences x 2 chunks


# --- verify_answer -----------------------------------------------------------


@pytest.mark.asyncio
async def test_verify_answer_passes_when_supported_ratio_meets_threshold(monkeypatch):
    chunk = make_chunk("u1-s1", "Sumon goes to school every day.")
    fake = FakeCrossEncoder(
        {
            (chunk.text, "Sumon goes to school."): 0.95,
            (chunk.text, "He studies English."): 0.95,
        }
    )
    monkeypatch.setattr(verify, "get_nli_model", lambda: fake)
    get_settings().verify_entailment_threshold = 0.5
    get_settings().verify_supported_ratio = 0.8

    report = await verify.verify_answer("Sumon goes to school. He studies English.", [chunk])

    assert report.passed is True
    assert report.supported_ratio == pytest.approx(1.0)
    assert len(report.sentences) == 2


@pytest.mark.asyncio
async def test_verify_answer_fails_when_ratio_below_threshold(monkeypatch):
    chunk = make_chunk("u1-s1", "Sumon goes to school every day.")
    fake = FakeCrossEncoder(
        {
            (chunk.text, "Sumon goes to school."): 0.95,
            (chunk.text, "Sumon has a pet dragon."): 0.05,
        }
    )
    monkeypatch.setattr(verify, "get_nli_model", lambda: fake)
    get_settings().verify_entailment_threshold = 0.5
    get_settings().verify_supported_ratio = 0.8

    report = await verify.verify_answer(
        "Sumon goes to school. Sumon has a pet dragon.", [chunk]
    )

    assert report.passed is False
    assert report.supported_ratio == pytest.approx(0.5)
    unsupported = [s.sentence for s in report.sentences if not s.supported]
    assert unsupported == ["Sumon has a pet dragon."]


@pytest.mark.asyncio
async def test_verify_answer_empty_answer_passes_trivially(monkeypatch):
    def fail_if_called():
        raise AssertionError("no sentences to check — the model must not be loaded")

    monkeypatch.setattr(verify, "get_nli_model", fail_if_called)

    report = await verify.verify_answer("", [make_chunk("u1-s1", "Some text.")])

    assert report.passed is True
    assert report.supported_ratio == 1.0
    assert report.sentences == []


# --- is_scaffolding_sentence (Phase 6 fix) ------------------------------


@pytest.mark.parametrize(
    "sentence",
    [
        "Dear students, today we are in Class 5, Social Studies, Unit 3, Lesson 2, on page 45.",
        "Hello, dear students! I hope you are all doing well today.",
        "Let's begin our lesson.",
        "Let's get ready for our lesson.",
        "Let's get started with our lesson.",
        "Now, let's focus on our lesson.",
        "We are in Class 5, and today we will be learning about geography.",
        "Does everyone understand?",
        "We are in Unit 5, Lesson 2, on page 45.",
        "I'll repeat that.",
        "I will repeat that.",
        'You asked, "What is the capital city of Indonesia?"',
        "Now, to answer the question: How many upazilas does Kishoreganj district have?",
        "Do you understand?",
        "Now, can anyone tell me why it is important to be quiet when someone is sleeping?",
        "Great job, everyone!",
        "So, what have we learned today?",
    ],
)
def test_is_scaffolding_sentence_matches_documented_patterns(sentence):
    assert verify.is_scaffolding_sentence(sentence) is True


@pytest.mark.parametrize(
    "sentence",
    [
        "Rina likes to read science fiction books.",
        "The capital city of Indonesia is Jakarta.",
        "The Sundarbans is located in Khulna, Satkhira and Bagerhat districts.",
        "An imperative sentence usually starts with a verb.",
        # The model sometimes glues the fact directly onto a
        # question-answering lead-in instead of a separate sentence — this
        # must NOT be dropped, or the only copy of the fact disappears.
        "To answer your question, Kishoreganj district has 13 upazilas.",
    ],
)
def test_is_scaffolding_sentence_leaves_factual_sentences_alone(sentence):
    assert verify.is_scaffolding_sentence(sentence) is False


# --- verify_answer excludes scaffolding from scoring (Phase 6 fix) -----


@pytest.mark.asyncio
async def test_verify_answer_excludes_scaffolding_from_supported_ratio(monkeypatch):
    # The exact shape of the dev-split bug: a correct fact wrapped in
    # unscoreable framing must not be dragged down by that framing.
    chunk = make_chunk("u6-s2", "The capital city of Indonesia is Jakarta.")
    fake = FakeCrossEncoder({(chunk.text, "The capital city of Indonesia is Jakarta."): 0.99})
    monkeypatch.setattr(verify, "get_nli_model", lambda: fake)
    get_settings().verify_entailment_threshold = 0.5
    get_settings().verify_supported_ratio = 0.8

    answer = (
        "Dear students, today we are in Class 5, English, Unit 6, Lesson 2, on page 36. "
        "The capital city of Indonesia is Jakarta. "
        "I'll repeat that. Do you understand?"
    )
    report = await verify.verify_answer(answer, [chunk])

    assert report.passed is True
    assert report.supported_ratio == pytest.approx(1.0)
    assert [s.sentence for s in report.sentences] == ["The capital city of Indonesia is Jakarta."]
    assert len(report.skipped_sentences) == 3  # greeting+citation, repeat marker, comprehension check


@pytest.mark.asyncio
async def test_verify_answer_all_scaffolding_passes_trivially(monkeypatch):
    def fail_if_called(*args, **kwargs):
        raise AssertionError("no real sentences to check — the model must not be loaded")

    monkeypatch.setattr(verify, "get_nli_model", fail_if_called)

    report = await verify.verify_answer(
        "Dear students, hello! Do you understand?", [make_chunk("u1-s1", "Some text.")]
    )

    assert report.passed is True
    assert report.sentences == []
    assert len(report.skipped_sentences) == 2
