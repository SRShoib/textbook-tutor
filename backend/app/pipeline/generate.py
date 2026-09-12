"""
What: stage 1 of the two-stage generation CLAUDE.md specifies — a plain
      factual answer grounded only in the retrieved+expanded lesson text,
      via prompts/v1_stage1.txt.

Why two stages, and why grade never appears here: stage 1 is checked for
      correctness, stage 2 (Phase 3) rewrites it in a Class-N teacher voice
      and must never introduce a fact stage 1 didn't produce. Keeping the
      grade variable out of v1_stage1.txt entirely is what makes that
      "stage 2 added no new facts" check meaningful later — stage 1's output
      cannot already be grade-flavoured.
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

from app.models.chunk import Chunk
from app.pipeline.llm import LLMResult, call_llm

STAGE1_PROMPT_VERSION = "v1_stage1"

# backend/app/pipeline/generate.py -> parents[2] is backend/, where
# prompts/ lives (matches the repo layout in CLAUDE.md).
_PROMPTS_DIR = Path(__file__).resolve().parents[2] / "prompts"


def load_prompt(version: str) -> str:
    return (_PROMPTS_DIR / f"{version}.txt").read_text(encoding="utf-8")


def format_context(chunks: list[Chunk]) -> str:
    """Numbers each chunk for the model to tell separate passages apart.
    Every chunk's stored text already starts with its own
    'Unit N, Lesson M: Title' header (see ingest.py's chunk_header()), so
    this only needs to join them, not re-derive any structure."""
    return "\n\n".join(f"[{i}] {chunk.text}" for i, chunk in enumerate(chunks, start=1))


def render_stage1_prompt(question: str, context: str) -> str:
    template = load_prompt(STAGE1_PROMPT_VERSION)
    return template.format(question=question, context=context)


@dataclass(frozen=True)
class Stage1Result:
    answer: str
    llm: LLMResult


def generate_stage1(question: str, context_chunks: list[Chunk], *, provider: str = "openai") -> Stage1Result:
    context = format_context(context_chunks)
    prompt = render_stage1_prompt(question, context)
    result = call_llm(prompt, prompt_version=STAGE1_PROMPT_VERSION, provider=provider)
    return Stage1Result(answer=result.text, llm=result)
