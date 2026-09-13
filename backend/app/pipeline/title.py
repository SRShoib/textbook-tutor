"""
What: a short (3-6 word) session title generated from the student's first
      question, so the sidebar (Phase 7) shows something readable instead of
      a session UUID. Same shape as rewrite.py: one prompt version, one
      call_llm() wrapper.

Why a fallback exists: a title is a UX nicety, not part of the answer the
      student came here for. If the LLM call fails for any reason (a network
      blip, a missing key in a dev environment), generate_title() must not
      raise -- it falls back to _fallback_title(), a pure truncation of the
      question, so session creation is never blocked by this.

Why this always uses provider="openai" regardless of pipeline_config: A/B/C/D
      (graph.py) ablate the answer pipeline being studied. Titling a session
      is bookkeeping, not part of that experiment.
"""

from __future__ import annotations

import re

from app.pipeline.generate import load_prompt
from app.pipeline.llm import call_llm

TITLE_PROMPT_VERSION = "v1_title"

_WORD_RE = re.compile(r"\S+")
_FALLBACK_WORD_COUNT = 6


def render_title_prompt(question: str) -> str:
    template = load_prompt(TITLE_PROMPT_VERSION)
    return template.format(question=question)


def _fallback_title(question: str) -> str:
    words = _WORD_RE.findall(question.strip())
    if not words:
        return "New conversation"
    title = " ".join(words[:_FALLBACK_WORD_COUNT])
    if len(words) > _FALLBACK_WORD_COUNT:
        title += "…"
    return title


def generate_title(question: str, *, provider: str = "openai") -> str:
    prompt = render_title_prompt(question)
    try:
        result = call_llm(prompt, prompt_version=TITLE_PROMPT_VERSION, provider=provider)
        title = result.text.strip().strip('"').strip()
        if not title:
            return _fallback_title(question)
        return title
    except Exception:
        return _fallback_title(question)
