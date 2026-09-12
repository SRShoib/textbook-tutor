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
