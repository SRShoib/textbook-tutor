"""
What: the automatic checks a stage-2 answer must pass before a student sees
      it — sentence length, vocabulary coverage against the book's own
      words, Flesch-Kincaid on the English portion, and an LLM judge — per
      CLAUDE.md's Phase 3 pipeline flow. graph.py retries stage 2 (feeding
      back what failed) up to settings.style_max_retries times.

Why the judge only runs after the numeric checks pass: if the answer is
      already too long, uses words the child hasn't met in the book, or
      reads above the grade's Flesch-Kincaid target, that failure alone is
      reason enough to regenerate, and it's free (no LLM call). The judge is
      the one paid check here (a second LLM call per attempt), so gating it
      behind the cheap checks roughly halves judge calls across a
      150-question x 5-config eval run.

Why the judge model is configurable separately (settings.openai_judge_model,
      threaded through call_llm's new `model` parameter): an answer should
      not be graded by the same model that wrote it if that can be avoided
      cheaply — that's the whole point of a separate judge step rather than
      trusting stage 2's own output. Falls back to the main model if unset,
      so a dev machine without OPENAI_JUDGE_MODEL configured doesn't crash.

Why the sentence/word splitting here duplicates
      tools/style_guide/measure_style.py instead of importing it: tools/ is
      a standalone script directory, not an importable package under
      backend/, and pipeline/ must not depend on it (CLAUDE.md). The
      threshold this module checks against (14 words, style_guide.md
      section 2) was PRODUCED by measure_style.py's splitter — using a
      different splitter here could silently invalidate that measured
      number. test_style_check.py loads measure_style.py by path and
      asserts both segment an identical fixture identically, so the two
      can't drift apart unnoticed.

Why vocabulary coverage is checked against chunks.text plus the five real
      few-shot teacher_answers, not just the book: the book's own running
      text is mostly narrative prose, but a teacher explaining also uses
      ordinary explaining words ("means", "because", "for example") that
      may not appear inside the story itself. Folding in the few-shot
      answers' vocabulary keeps the check aligned with the same voice
      stage 2 is asked to imitate.

Why is_scaffolding_sentence()/content_sentences() live here, not just in
      verify.py (Phase 6 fix, 2026-09-13, found via dev-split error
      analysis): verify.py originally excluded pedagogical scaffolding
      (greeting, lesson citation, "I'll repeat that", comprehension checks —
      see the pattern list below) from its own entailment check, but the
      SAME scaffolding was still being counted here, toward
      sentence-length/vocab/FK. The dominant cause turned out to be the
      question-echo pattern specifically: "Now, to answer your question,
      '<the entire original question restated>'" is frequently 15-20 words
      on its own, regardless of how simple the actual answer is — 54 of 62
      dev-split "too hard" failures had this exact sentence as their
      longest. A citation/greeting/repeat-marker sentence being wordy isn't
      a grade-adaptation failure any more than it's a hallucination — it's
      fixed template text, not something stage 2 chose to write at length —
      so check_style() now scores content_sentences() (scaffolding
      excluded), the same standard verify.py already holds itself to.
      is_scaffolding_sentence() moved here (the lower-level module
      verify.py already imports sentences() from) so both can use it
      without a circular import; verify.py re-imports it under the same
      name so nothing there had to change.
"""

from __future__ import annotations

import asyncio
import re
import uuid
from dataclasses import dataclass, field

from pydantic import BaseModel
from sqlalchemy import select

from app.core.config import get_settings
from app.core.db import AsyncSessionLocal
from app.models.chunk import Chunk
from app.pipeline.generate import load_fewshot, load_prompt
from app.pipeline.llm import call_llm

STYLE_JUDGE_PROMPT_VERSION = "v1_style_judge"

# Copied from tools/style_guide/measure_style.py (BANGLA_RE, SENT_SPLIT_RE,
# WORD_RE, strip_bangla_keep_punct) — see the module docstring on why this
# is a copy, not an import, and how it's kept from drifting.
_BANGLA_RE = re.compile(r"[ঀ-৿]")
# Splits after terminal punctuation, and also after terminal punctuation
# immediately followed by a closing quote mark (Phase 6 fix, found via
# dev-split error analysis): stage 2's answers echo the student's question
# in quotes before answering it — 'You asked, "...?" The answer is, X.' —
# and the un-augmented lookbehind never matched at the '?"' boundary, so
# that whole sentence stayed one long, noisy blob that verify.py's NLI
# check then failed even when X was correct. Keeping this in sync with
# measure_style.py's copy is what test_style_check.py's cross-validation
# test enforces.
_SENT_SPLIT_RE = re.compile(r'(?<=[.!?।])\s+|(?<=[.!?।]["”’\'])\s+')
_WORD_RE = re.compile(r"[A-Za-zঀ-৿']+")


def strip_bangla_keep_punct(text: str) -> str:
    return re.sub(r"[ঀ-৿]+", " ", text)


def sentences(text: str) -> list[str]:
    return [s.strip() for s in _SENT_SPLIT_RE.split(text) if s.strip()]


def words(text: str) -> list[str]:
    return _WORD_RE.findall(text)


# Each pattern below is evidenced by one named style_guide.md structural
# rule, not a general-purpose filler detector — see verify.py's original
# module docstring (moved here 2026-09-13) for the full evidence trail:
# §3.1 opening move (greeting, lesson citation), §3.2/§3.3 core explanation
# (heavy repetition: question echo, "I'll repeat that"), §3.2 comprehension
# check, §3.5 closing move.
_SCAFFOLDING_PATTERNS = [
    re.compile(r"^(dear students|hello,?\s*(dear\s+)?students|hi,?\s*(dear\s+)?students)\b", re.IGNORECASE),
    re.compile(r"^(hello|hi)[!.]?\s*(dear students)?[,!.]?\s*$", re.IGNORECASE),
    re.compile(r"i hope you('re| are)( all)? (doing well|fine|well)", re.IGNORECASE),
    re.compile(
        r"^(now,?\s*)?let('s| us)\s+(begin|start|get started( with)?|get ready for|focus on)\s+(our\s+)?lesson\b",
        re.IGNORECASE,
    ),
    re.compile(r"\bunit\s+\d+,?\s*lesson\s+\d+\b", re.IGNORECASE),
    re.compile(r"\bon page\s+\d+\b", re.IGNORECASE),
    re.compile(r"^we are in class\s+\d+\b.*\b(learning about|studying)\b", re.IGNORECASE),
    re.compile(r'^you asked\b.*\?["”’]?\s*$', re.IGNORECASE),
    re.compile(r"^(now,?\s*)?(let'?s|to) answer (the|your) question\b.*\?[\"”’]?\s*$", re.IGNORECASE),
    re.compile(r"^(i'?ll|i will|let me)\s+repeat\b", re.IGNORECASE),
    re.compile(r"^(do you|does everyone)\s+understand\??\s*$", re.IGNORECASE),
    re.compile(r"^does that make sense\??\s*$", re.IGNORECASE),
    re.compile(r"^(now,?\s*)?can (anyone|you) tell me\b", re.IGNORECASE),
    re.compile(r"^now,?\s*tell me\b", re.IGNORECASE),
    re.compile(r"^(so,?\s*)?what (have|did) we (learn|learned) today\b", re.IGNORECASE),
    re.compile(r"^(great job|well done|good job)\b", re.IGNORECASE),
]


def is_scaffolding_sentence(sentence: str) -> bool:
    """True for the fixed pedagogical framing style_guide.md's structural
    rules put around an answer (greeting, lesson citation, repeat marker,
    comprehension check, closing) — see module docstring for why these are
    excluded from both verification and the readability checks below
    rather than scored as if they were content."""
    return any(pattern.search(sentence) for pattern in _SCAFFOLDING_PATTERNS)


def content_sentences(text: str) -> list[str]:
    """sentences(text) with scaffolding sentences dropped — what
    check_sentence_length/check_vocab_coverage/check_fk_grade should score,
    since a child's reading burden is set by the explanation, not by the
    fixed greeting/citation/repeat-marker text wrapped around it."""
    return [s for s in sentences(text) if not is_scaffolding_sentence(s)]


# --- individual checks ---------------------------------------------------


def check_sentence_length(answer: str) -> tuple[int, float]:
    """(max_words, mean_words) over the answer's sentences. (0, 0.0) for an
    empty or unpunctuated-to-nothing answer."""
    lens = [len(words(s)) for s in sentences(answer)]
    lens = [n for n in lens if n > 0]
    if not lens:
        return 0, 0.0
    return max(lens), round(sum(lens) / len(lens), 1)


_vocab_cache: dict[tuple[uuid.UUID, int], set[str]] = {}


async def book_vocabulary(book_id: uuid.UUID, grade: int) -> set[str]:
    """Lowercased word set from every chunk of this book (header line
    dropped — 'Unit N, Lesson M: Title' is not vocabulary the child read)
    plus the real few-shot teacher_answers for this grade. Cached per
    (book_id, grade) for the life of the process; a book is never
    re-ingested with different content mid-process in this project."""
    key = (book_id, grade)
    if key in _vocab_cache:
        return _vocab_cache[key]

    async with AsyncSessionLocal() as session:
        result = await session.execute(select(Chunk.text).where(Chunk.book_id == book_id))
        texts = result.scalars().all()

    vocab: set[str] = set()
    for text in texts:
        body = text.split("\n", 1)[1] if "\n" in text else text
        vocab.update(w.lower() for w in words(body))
    for ex in load_fewshot(grade):
        vocab.update(w.lower() for w in words(ex.teacher_answer))

    _vocab_cache[key] = vocab
    return vocab


def check_vocab_coverage(answer: str, vocab: set[str]) -> tuple[float, list[str]]:
    """(coverage_ratio, out-of-vocabulary words in first-seen order).
    Bangla is stripped first — vocab coverage is an English-vocabulary
    check, same scope as the Flesch-Kincaid check below."""
    answer_words = [w.lower() for w in words(strip_bangla_keep_punct(answer))]
    if not answer_words:
        return 1.0, []
    seen: list[str] = []
    for w in answer_words:
        if w not in vocab and w not in seen:
            seen.append(w)
    coverage = 1 - sum(1 for w in answer_words if w not in vocab) / len(answer_words)
    return round(coverage, 3), seen


def check_fk_grade(answer: str) -> float | None:
    """Flesch-Kincaid on the Bangla-stripped text. None (not a failure) if
    there isn't enough English text to score reliably — same 30-word floor
    and valid-range sanity check as measure_style.py, so a short answer
    doesn't produce a meaningless FK number."""
    english = strip_bangla_keep_punct(answer)
    if len(english.split()) < 30:
        return None
    try:
        import textstat
    except ImportError:
        return None
    try:
        score = round(textstat.flesch_kincaid_grade(english), 2)
    except Exception:
        return None
    if score < -5 or score > 25:
        return None
    return score


class _JudgeVerdict(BaseModel):
    suitable: bool
    reason: str


def _parse_judge_verdict(text: str) -> _JudgeVerdict:
    """Same fence-stripping shape as eval/metrics.py's _parse_structured —
    the model is told to return pure JSON but occasionally wraps it anyway."""
    cleaned = text.strip()
    if cleaned.startswith("```"):
        cleaned = cleaned.strip("`")
        if cleaned.lower().startswith("json"):
            cleaned = cleaned[4:]
        cleaned = cleaned.strip()
    return _JudgeVerdict.model_validate_json(cleaned)


def render_judge_prompt(answer: str, *, grade: int) -> str:
    template = load_prompt(STYLE_JUDGE_PROMPT_VERSION)
    return template.format(grade=grade, answer=answer)


def run_judge(answer: str, *, grade: int, provider: str = "openai") -> _JudgeVerdict:
    settings = get_settings()
    prompt = render_judge_prompt(answer, grade=grade)
    judge_model = settings.openai_judge_model if provider == "openai" else None
    result = call_llm(
        prompt,
        prompt_version=STYLE_JUDGE_PROMPT_VERSION,
        provider=provider,
        model=judge_model or None,
        temperature=0.0,
    )
    return _parse_judge_verdict(result.text)


@dataclass(frozen=True)
class StyleReport:
    passed: bool
    max_sentence_words: int
    mean_sentence_words: float
    fk_grade: float | None
    vocab_coverage: float
    oov_words: list[str] = field(default_factory=list)
    judge_ran: bool = False
    judge_suitable: bool | None = None
    judge_reason: str | None = None
    failures: list[str] = field(default_factory=list)


async def check_style(
    answer: str,
    *,
    grade: int,
    book_id: uuid.UUID,
    provider: str = "openai",
) -> StyleReport:
    settings = get_settings()
    failures: list[str] = []

    # Scored on the explanation only — scaffolding (greeting, citation,
    # repeat marker, comprehension check) dropped first. See module
    # docstring: a wordy question-echo sentence isn't a grade-adaptation
    # failure, it's fixed template text.
    content = " ".join(content_sentences(answer))

    max_words, mean_words = check_sentence_length(content)
    if max_words > settings.style_max_sentence_words:
        failures.append(
            f"a sentence is {max_words} words long, over the "
            f"{settings.style_max_sentence_words}-word limit for this grade"
        )

    vocab = await book_vocabulary(book_id, grade)
    coverage, oov = check_vocab_coverage(content, vocab)
    if coverage < settings.style_vocab_coverage_min:
        preview = ", ".join(oov[:8])
        failures.append(
            f"only {coverage:.0%} of the words are in the book's vocabulary "
            f"(need {settings.style_vocab_coverage_min:.0%}); unfamiliar words: {preview}"
        )

    fk = check_fk_grade(content)
    if fk is not None and fk > settings.style_fk_max:
        failures.append(
            f"reading level (Flesch-Kincaid {fk}) is above the "
            f"{settings.style_fk_max} target for this grade"
        )

    judge_ran = False
    judge_suitable: bool | None = None
    judge_reason: str | None = None
    if not failures:
        judge_ran = True
        verdict = await asyncio.to_thread(run_judge, answer, grade=grade, provider=provider)
        judge_suitable = verdict.suitable
        judge_reason = verdict.reason
        if not verdict.suitable:
            failures.append(f"judge: {verdict.reason}")

    return StyleReport(
        passed=not failures,
        max_sentence_words=max_words,
        mean_sentence_words=mean_words,
        fk_grade=fk,
        vocab_coverage=coverage,
        oov_words=oov,
        judge_ran=judge_ran,
        judge_suitable=judge_suitable,
        judge_reason=judge_reason,
        failures=failures,
    )
