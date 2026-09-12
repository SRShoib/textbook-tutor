"""
What: the hallucination-control check CLAUDE.md's Phase 4 requires — every
      sentence of the final (stage-2) answer is scored for entailment against
      the retrieved book text with cross-encoder/nli-deberta-v3-base, and the
      answer only counts as grounded if enough of its sentences clear
      settings.verify_entailment_threshold, measured as
      settings.verify_supported_ratio. graph.py's verify_node calls
      verify_answer() after style_check, and its conditional edge regenerates
      stage 2 (feeding back which sentences failed) up to
      settings.verify_max_retries times before giving up and marking the
      message refused_unverified.

Why the premise is a chunk sentence, not the whole chunk (changed after a
      live run — see NOTES.md's "Phase 4 live check" entry): the first
      version used whole chunks as the premise, reasoning this matched
      CLAUDE.md's "against retrieved chunks" wording most literally. A live
      run against the real book showed this was wrong in a way no threshold
      could fix: cross-encoder/nli-deberta-v3-base is trained on
      single-sentence-premise/single-sentence-hypothesis pairs (SNLI/
      MultiNLI). Handed a multi-sentence chunk as the premise, it
      systematically classifies a hypothesis that matches only one sentence
      within it as "neutral," not "entailment" — confirmed directly: a
      hand-built two-sentence premise containing a fact word-for-word scored
      neutral=0.993 against a hypothesis restating exactly that fact, while
      the same model scored a classic single-sentence SNLI pair
      ("A man is eating a pizza." -> "A man is eating food.") as
      entailment=0.987. Splitting both the chunk and the answer into
      sentences puts every scored pair back into the shape the model was
      trained on. `_premise_sentences()` also drops each chunk's leading
      "Unit N, Lesson M: Title" header line before splitting (same as
      style_check.book_vocabulary()) — it isn't a sentence and would only
      ever be a wasted candidate.

Why context_chunks, not top_chunks: context_chunks (retrieve.py's
      post-expansion parent-lesson set) is exactly the text generate_stage1
      was given to work from. Verifying against anything narrower would mean
      failing an answer for a fact that's genuinely in the book, just not in
      the pre-expansion top-k hit.

Why one batched model.predict() call instead of one per sentence: every
      (premise sentence, answer sentence) pair — across every chunk and the
      whole answer — is built up front and scored in a single call, then
      reduced to a per-answer-sentence max. Sentence-splitting both sides
      means more pairs than the original whole-chunk version (premise
      sentences per chunk, not one row per chunk), but it's still one GPU
      call, and still cheap for a book this size (~135 chunks) — same
      "batch the GPU call" shape as embeddings.embed_texts(), just with a
      cross-encoder instead of a bi-encoder.

Label order: sentence-transformers' nli-deberta*/nli-roberta* cross-encoders
      share the convention ['contradiction', 'entailment', 'neutral'] (per
      the model's own usage example on its Hugging Face model card) — there
      is no way to read this back out of the model config itself, so it is
      asserted here as a constant rather than derived.
"""

from __future__ import annotations

import asyncio
from dataclasses import dataclass, field

from app.core.config import get_settings
from app.models.chunk import Chunk
from app.pipeline.style_check import sentences

_LABELS = ["contradiction", "entailment", "neutral"]
_ENTAILMENT_INDEX = _LABELS.index("entailment")

_nli_model = None


def get_nli_model():
    global _nli_model
    if _nli_model is None:
        from sentence_transformers import CrossEncoder

        settings = get_settings()
        _nli_model = CrossEncoder(settings.nli_model, device=settings.device)
    return _nli_model


@dataclass(frozen=True)
class SentenceVerification:
    sentence: str
    entailment_score: float
    supported: bool
    best_lesson_id: str | None


@dataclass(frozen=True)
class VerificationReport:
    passed: bool
    supported_ratio: float
    sentences: list[SentenceVerification] = field(default_factory=list)


def _premise_sentences(chunks: list[Chunk]) -> list[tuple[str, str]]:
    """(lesson_id, sentence) for every sentence of every chunk, header line
    ("Unit N, Lesson M: Title", prepended at ingest time) dropped first —
    same split as style_check.sentences(), so premise and hypothesis
    sentences are drawn the same way."""
    pairs = []
    for chunk in chunks:
        body = chunk.text.split("\n", 1)[1] if "\n" in chunk.text else chunk.text
        for sentence in sentences(body):
            pairs.append((chunk.lesson_id, sentence))
    return pairs


def verify_sentences(sentence_list: list[str], chunks: list[Chunk]) -> list[SentenceVerification]:
    """Pure(ish) scoring step: every answer sentence against every premise
    sentence drawn from every chunk, one batched predict() call, max
    entailment probability kept per answer sentence. Blocking (GPU) —
    callers off-load it with asyncio.to_thread, same as
    style_check.run_judge."""
    settings = get_settings()
    model = get_nli_model()
    premises = _premise_sentences(chunks)
    n_premises = len(premises)
    pairs = [(premise_sentence, sentence) for sentence in sentence_list for _, premise_sentence in premises]
    scores = model.predict(pairs, apply_softmax=True)

    results = []
    for i, sentence in enumerate(sentence_list):
        row_scores = scores[i * n_premises : (i + 1) * n_premises]
        entailment_probs = [float(row[_ENTAILMENT_INDEX]) for row in row_scores]
        best_idx = max(range(n_premises), key=lambda j: entailment_probs[j])
        best_score = entailment_probs[best_idx]
        results.append(
            SentenceVerification(
                sentence=sentence,
                entailment_score=round(best_score, 4),
                supported=best_score >= settings.verify_entailment_threshold,
                best_lesson_id=premises[best_idx][0],
            )
        )
    return results


async def verify_answer(answer: str, context_chunks: list[Chunk]) -> VerificationReport:
    settings = get_settings()
    sentence_list = sentences(answer)
    if not sentence_list:
        # Nothing to disprove — same "empty input passes" shape as
        # style_check.check_vocab_coverage.
        return VerificationReport(passed=True, supported_ratio=1.0, sentences=[])

    results = await asyncio.to_thread(verify_sentences, sentence_list, context_chunks)
    supported_ratio = sum(1 for r in results if r.supported) / len(results)
    passed = supported_ratio >= settings.verify_supported_ratio
    return VerificationReport(passed=passed, supported_ratio=round(supported_ratio, 3), sentences=results)
