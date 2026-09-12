"""
What: turns a follow-up question into a standalone search query before
      retrieval runs, using the session's recent history. Skips the LLM
      call when the question already looks standalone — CLAUDE.md's
      pipeline flow says "rewrite (skip if already standalone)".

Why a cheap heuristic decides whether to call the LLM at all, instead of
      always rewriting: history is empty on a session's first turn (nothing
      to resolve against), and most questions never reference the
      conversation ("What is a noun?" doesn't need history to make sense).
      needs_rewrite() is deliberately biased toward True rather than
      accurate — a false positive costs one cached LLM call; a false
      negative sends an unresolved "it" or "give me more examples" straight
      to retrieval, which then returns nothing useful. Cheap-and-eager beats
      precise-and-risky here.

Why history is loaded here, in its own DB session, rather than passed in by
      the caller: retrieve.py's hybrid_search() follows the same shape (its
      own AsyncSessionLocal) — pipeline/ opens what it needs rather than
      receiving a session from the caller, which is what keeps it callable
      identically from the API, the eval runner, and a notebook without any
      of them wiring a shared session through (CLAUDE.md: pipeline/ has no
      FastAPI imports).
"""

from __future__ import annotations

import re
import uuid
from dataclasses import dataclass

from sqlalchemy import select

from app.core.db import AsyncSessionLocal
from app.models.message import Message, MessageRole
from app.pipeline.generate import load_prompt
from app.pipeline.llm import LLMResult, call_llm

REWRITE_PROMPT_VERSION = "v1_rewrite"

_WORD_RE = re.compile(r"[A-Za-z']+")

# Left unbound without history: "it", "that book" etc. only make sense with
# the prior turn in view.
_PRONOUN_OR_DEICTIC = {"it", "they", "them", "this", "that", "these", "those", "he", "she", "his", "her", "one", "ones"}
# A follow-up leaning on the previous turn rather than standing alone.
_SINGLE_WORD_OPENERS = {"and", "more", "another", "again", "why", "but", "so"}
_PHRASE_OPENERS = ("what about", "how about", "give me more", "tell me more")


def needs_rewrite(question: str, history: list[Message]) -> bool:
    """Pure, no LLM call — see the module docstring on why this is
    deliberately biased toward True."""
    if not history:
        return False

    stripped = question.strip().lower()
    words = _WORD_RE.findall(stripped)
    if not words:
        return False

    if len(words) < 5:
        return True
    if any(stripped.startswith(opener) for opener in _PHRASE_OPENERS):
        return True
    if words[0] in _SINGLE_WORD_OPENERS:
        return True
    if any(w in _PRONOUN_OR_DEICTIC for w in words):
        return True
    return False


async def load_history(session_id: uuid.UUID, limit: int) -> list[Message]:
    """Last `limit` messages (both roles) in chronological order."""
    async with AsyncSessionLocal() as session:
        result = await session.execute(
            select(Message).where(Message.session_id == session_id).order_by(Message.created_at.desc()).limit(limit)
        )
        rows = list(result.scalars().all())
    return list(reversed(rows))


def format_history(history: list[Message]) -> str:
    lines = []
    for m in history:
        speaker = "Student" if m.role == MessageRole.USER else "Teacher"
        lines.append(f"{speaker}: {m.content}")
    return "\n".join(lines)


def render_rewrite_prompt(question: str, history: list[Message]) -> str:
    template = load_prompt(REWRITE_PROMPT_VERSION)
    return template.format(history=format_history(history), question=question)


@dataclass(frozen=True)
class RewriteResult:
    query: str
    rewritten: bool
    llm: LLMResult | None


def rewrite_question(question: str, history: list[Message], *, provider: str = "openai") -> RewriteResult:
    if not needs_rewrite(question, history):
        return RewriteResult(query=question, rewritten=False, llm=None)
    prompt = render_rewrite_prompt(question, history)
    result = call_llm(prompt, prompt_version=REWRITE_PROMPT_VERSION, provider=provider)
    return RewriteResult(query=result.text.strip(), rewritten=True, llm=result)
