"""
What: the two numbers CLAUDE.md's Phase 2 asks eval/runner.py to print —
      retrieval hit rate and RAGAS faithfulness — plus JSONL read/write and
      the adapter that makes RAGAS's own LLM calls go through
      pipeline/llm.py's cache.

Why RAGAS needs its own adapter here, not its default OpenAI client:
      CLAUDE.md is explicit that every LLM call must go through
      pipeline/llm.py so eval re-runs hit the disk cache instead of billing
      again. RAGAS's Faithfulness metric calls an LLM twice per question
      (once to break the answer into atomic statements, once to check each
      statement against the retrieved context) through a small, swappable
      InstructorBaseRagasLLM interface: generate(prompt, response_model) /
      agenerate(prompt, response_model). CachedRagasLLM below implements
      that interface by forwarding to call_llm() and validating the JSON
      that comes back against response_model — RAGAS's own prompt templates
      already embed the full JSON-schema instructions and few-shot examples
      the model needs (confirmed by inspecting Faithfulness's rendered
      prompts directly), so this adapter has nothing to add to the prompt.

Known packaging bug worked around below: ragas==0.4.3 unconditionally
      imports langchain_community.chat_models.vertexai at import time — a
      module that no longer exists in the current langchain-community
      (Vertex AI support moved to a separate package this project doesn't
      install). Pinning an old-enough langchain-community to keep that
      module forces langchain-core back below what langgraph==1.2.11
      requires, so that's not a usable fix. Since this project never uses
      Vertex AI, a tiny stub module satisfies ragas's import without
      touching any installed package version.
"""

from __future__ import annotations

import asyncio
import json
import math
import sys
import types
from pathlib import Path

# --- work around ragas==0.4.3's broken import, before importing ragas -----

if "langchain_community.chat_models.vertexai" not in sys.modules:
    _stub = types.ModuleType("langchain_community.chat_models.vertexai")

    class _UnusedChatVertexAI:  # never instantiated — this project has no Vertex AI config
        pass

    _stub.ChatVertexAI = _UnusedChatVertexAI
    sys.modules["langchain_community.chat_models.vertexai"] = _stub

from ragas.llms.base import InstructorBaseRagasLLM
from ragas.metrics.collections import Faithfulness

from app.pipeline.llm import call_llm
from app.pipeline.style_check import check_fk_grade, check_sentence_length

RAGAS_PROMPT_VERSION = "ragas_faithfulness_v1"


# --- JSONL -----------------------------------------------------------------


def write_jsonl(path: Path, records: list[dict]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8") as f:
        for record in records:
            f.write(json.dumps(record, ensure_ascii=False) + "\n")


def read_jsonl(path: Path) -> list[dict]:
    with path.open(encoding="utf-8") as f:
        return [json.loads(line) for line in f if line.strip()]


# --- retrieval hit rate ------------------------------------------------


def retrieval_hit_rate(records: list[dict]) -> tuple[float | None, int]:
    """Fraction of questions whose expected_lesson_id appears among the
    lesson_ids actually retrieved. Rows without an expected_lesson_id (no
    ground truth yet) are excluded from both the numerator and the
    denominator — returns (None, 0) if no row has ground truth, so a
    partially-labelled question set can't silently report a misleadingly
    confident number."""
    labelled = [r for r in records if r.get("expected_lesson_id")]
    if not labelled:
        return None, 0
    hits = sum(1 for r in labelled if r["expected_lesson_id"] in r.get("retrieved_lesson_ids", []))
    return hits / len(labelled), len(labelled)


# --- refusal, hallucination (project-guidelines.md 8.3) -------------------


def refusal_accuracy(records: list[dict]) -> tuple[float | None, int]:
    """Fraction of off_book-type questions (questions.jsonl's ground truth
    for "not in the book") the system correctly did NOT answer directly —
    any status other than 'answered' counts as a correct refusal, since a
    child is equally unprotected by a wrong-but-plausible answer regardless
    of which check caught it. None if the question set has no off_book rows
    (mirrors retrieval_hit_rate's "no ground truth, don't report a number"
    shape). Config A has no off-book gate at all, so this is expected to
    read 0.00 for it — that gap is the point of the ablation."""
    off_book = [r for r in records if r.get("type") == "off_book"]
    if not off_book:
        return None, 0
    correct = sum(1 for r in off_book if r["status"] != "answered")
    return correct / len(off_book), len(off_book)


def false_refusal_rate(records: list[dict]) -> tuple[float | None, int]:
    """Fraction of genuinely answerable questions (type != off_book) the
    system incorrectly failed to answer directly — any non-'answered'
    status on a row that DOES have ground truth counts as a false refusal.
    None if the question set has no answerable rows."""
    answerable = [r for r in records if r.get("type") != "off_book"]
    if not answerable:
        return None, 0
    refused = sum(1 for r in answerable if r["status"] != "answered")
    return refused / len(answerable), len(answerable)


def hallucination_rate(records: list[dict]) -> tuple[float | None, int]:
    """Mean fraction of a message's answer sentences NOT entailed by the
    retrieved chunks (1 - verification.supported_ratio), straight from the
    NLI verifier's own logs (project-guidelines.md 8.3: "% of answer
    sentences not entailed by retrieved chunks, from verifier logs") —
    distinct from refused_unverified counts, since a message can pass
    verification (or not have it run at all) while still containing some
    unsupported sentences below the pass threshold.

    Only rows carrying a verification report can be scored, so this is
    None for configs B and C, which never run verify_node by design (see
    graph.py's pipeline_config docstring) — there are no per-sentence
    entailment logs to average for them."""
    scored = [r for r in records if r.get("status") == "answered" and r.get("verification")]
    if not scored:
        return None, 0
    rates = [1 - r["verification"]["supported_ratio"] for r in scored]
    return sum(rates) / len(rates), len(scored)


def severe_hallucination_rate(records: list[dict]) -> tuple[float | None, int]:
    """Fraction of verified answered rows where supported_ratio is exactly
    0.0 -- every checked sentence failed entailment, not just some of them.

    Why this exists alongside hallucination_rate() (2026-09-13, Phase 6
    error analysis): style_guide.md's "heavy repetition" pattern
    (§3.2/§3.3) has stage 2 restate the same fact 2-3 times per answer, and
    verify_sentences() scores each restatement independently. A message
    stating exactly one true fact can land at supported_ratio=0.5 if one
    phrasing of it scores low on NLI noise while another scores high --
    hallucination_rate()'s per-sentence average then reads that as "half
    hallucinated." A full manual+automated audit of every answered row in a
    120-question dev run (cross-checked against questions.jsonl's
    reference_answer) found zero actual fabrications despite
    hallucination_rate() reading 0.24 on that run; every failing sentence
    was the same true claim in a noisier wrapper next to a cleanly-scoring
    restatement of it (see NOTES.md's 2026-09-13 entry for the audit).
    supported_ratio == 0.0 means NO phrasing of ANY claim in the message
    passed -- there's no clean restatement left to save it -- which is a
    much stronger, harder-to-explain-away signal that something in the
    message isn't actually grounded. Report both numbers together; neither
    one alone is the full picture."""
    scored = [r for r in records if r.get("status") == "answered" and r.get("verification")]
    if not scored:
        return None, 0
    severe = sum(1 for r in scored if r["verification"]["supported_ratio"] == 0.0)
    return severe / len(scored), len(scored)


def readability_summary(records: list[dict]) -> dict | None:
    """Flesch-Kincaid and sentence-length numbers computed directly from
    each answered row's answer text, using the same pure functions
    style_check.py scores a live answer with. Unlike style_summary() below
    (which only exists for rows that actually went through check_style,
    i.e. configs C and D), this works for every config — including A and B,
    where stage 2 and style_check never run — so the results table can
    report readability for all of A/B/C/D/D-open, not just two of them."""
    answered = [r for r in records if r.get("status") == "answered" and r.get("answer")]
    if not answered:
        return None
    fk_values: list[float] = []
    max_words_values: list[int] = []
    mean_words_values: list[float] = []
    for r in answered:
        fk = check_fk_grade(r["answer"])
        if fk is not None:
            fk_values.append(fk)
        max_words, mean_words = check_sentence_length(r["answer"])
        max_words_values.append(max_words)
        mean_words_values.append(mean_words)
    return {
        "n": len(answered),
        "mean_fk": round(sum(fk_values) / len(fk_values), 2) if fk_values else None,
        "fk_n": len(fk_values),
        "mean_max_sentence_words": round(sum(max_words_values) / len(max_words_values), 1),
        "mean_sentence_words": round(sum(mean_words_values) / len(mean_words_values), 1),
    }


# --- RAGAS faithfulness, via our own cache -------------------------------


def _parse_structured(text: str, response_model):
    """RAGAS's own prompt asks for pure JSON; strip a ```-fenced block if the
    model added one anyway despite the instruction not to."""
    cleaned = text.strip()
    if cleaned.startswith("```"):
        cleaned = cleaned.strip("`")
        if cleaned.lower().startswith("json"):
            cleaned = cleaned[4:]
        cleaned = cleaned.strip()
    return response_model.model_validate_json(cleaned)


class CachedRagasLLM(InstructorBaseRagasLLM):
    """Routes RAGAS's structured-output calls through call_llm() so a RAGAS
    eval run is disk-cached and cost-tracked exactly like every other LLM
    call in this project, instead of RAGAS's default of calling OpenAI
    directly."""

    def __init__(self, *, provider: str = "openai"):
        self.provider = provider

    def generate(self, prompt: str, response_model):
        result = call_llm(prompt, prompt_version=RAGAS_PROMPT_VERSION, provider=self.provider, temperature=0.0)
        return _parse_structured(result.text, response_model)

    async def agenerate(self, prompt: str, response_model):
        # call_llm() is blocking (network or disk); offload it so scoring
        # many questions doesn't block the runner's event loop.
        return await asyncio.to_thread(self.generate, prompt, response_model)


def style_summary(records: list[dict]) -> dict | None:
    """Mean style numbers over answered rows carrying a style report — the
    first numbers for project-guidelines.md section 8.3's "Simplicity" row
    (Flesch-Kincaid, mean sentence length, vocabulary coverage), plus the
    style-check pass rate itself. None if no row has a style report (e.g.
    every question was refused off-book)."""
    scored = [r for r in records if r.get("status") == "answered" and r.get("style")]
    if not scored:
        return None
    fk_values = [r["style"]["fk_grade"] for r in scored if r["style"].get("fk_grade") is not None]
    return {
        "n": len(scored),
        "pass_rate": sum(1 for r in scored if r["style"]["passed"]) / len(scored),
        "mean_fk": round(sum(fk_values) / len(fk_values), 2) if fk_values else None,
        "mean_max_sentence_words": round(sum(r["style"]["max_sentence_words"] for r in scored) / len(scored), 1),
        "mean_vocab_coverage": round(sum(r["style"]["vocab_coverage"] for r in scored) / len(scored), 3),
    }


async def ragas_faithfulness(records: list[dict], *, provider: str = "openai") -> tuple[float | None, int]:
    """Average RAGAS faithfulness over 'answered' rows only — a refusal has
    no claims to check for faithfulness. Each row needs 'question',
    'answer' and 'context_texts' (the actual chunk text stage 1 read, not
    just the cited lesson_ids). Returns (None, 0) if there is nothing to
    score."""
    answered = [r for r in records if r.get("status") == "answered" and r.get("answer") and r.get("context_texts")]
    if not answered:
        return None, 0

    metric = Faithfulness(llm=CachedRagasLLM(provider=provider))
    scores = []
    for r in answered:
        result = await metric.ascore(
            user_input=r["question"], response=r["answer"], retrieved_contexts=r["context_texts"]
        )
        if not math.isnan(result.value):
            scores.append(result.value)

    if not scores:
        return None, 0
    return sum(scores) / len(scores), len(scores)
