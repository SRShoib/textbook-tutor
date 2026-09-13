"""
What: session CRUD (create, list, get-one, rename, soft-delete) plus
      POST /sessions/{id}/messages, which sends one question through the
      pipeline and returns the assistant's message as the full evidence
      object CLAUDE.md's API conventions call for (answer, status, sources,
      verification, readability, latency_ms, config_version), and
      POST /sessions/{id}/messages/stream, the SSE version of the same
      thing (Phase 7 module 5) -- see that module's plan/NOTES.md entry for
      why "streaming" here means real retrieval-progress events followed by
      a simulated typing effect over the already-fully-checked final
      answer, not a live relay of in-progress LLM tokens: stage 2 can
      legitimately run more than once (style_check/verify retry edges in
      pipeline/graph.py) before the graph settles, so anything shown before
      that point could be text the pipeline is about to discard.
Why create_message() also sets session.title and bumps session.updated_at:
      a session has no human-readable title until its first question comes
      in (see pipeline/title.py), and the row is otherwise never touched
      again after creation -- without an explicit bump here, "sort sessions
      by updated_at" would silently mean "by creation time," not "by last
      activity," which is what a sidebar actually wants. Both routes share
      _touch_session() for this so they can't quietly diverge.
"""

from __future__ import annotations

import asyncio
import json
import uuid
from collections.abc import AsyncIterator
from dataclasses import asdict
from datetime import datetime, timezone

from fastapi import APIRouter, Depends, status
from fastapi.responses import StreamingResponse
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.auth import get_current_user, get_current_user_id
from app.core.db import get_db
from app.core.errors import AppError
from app.models.book import Book, BookStatus
from app.models.message import Message, MessageRole
from app.models.session import Session
from app.models.user import User
from app.pipeline.graph import run_pipeline, run_pipeline_stream
from app.pipeline.title import generate_title
from app.schemas.message import MessageCreate, MessageRead
from app.schemas.session import SessionCreate, SessionRead, SessionUpdate

router = APIRouter(prefix="/sessions", tags=["sessions"])

# Cosmetic pacing for the simulated typing effect -- not a research
# threshold (like OFF_BOOK_REFUSAL in graph.py, a plain UI string), so it
# lives here as a constant rather than in config.py.
_TOKEN_STREAM_DELAY_SECONDS = 0.03


async def _get_owned_session(db: AsyncSession, session_id: uuid.UUID, user_id: uuid.UUID) -> Session:
    """Shared 404 shape for every route scoped to one session: wrong id,
    someone else's session, and a soft-deleted session all look identical
    from the outside -- CLAUDE.md's soft-delete rule means a deleted row
    still exists, so this must check deleted_at, not just existence."""
    session = await db.get(Session, session_id)
    if session is None or session.user_id != user_id or session.deleted_at is not None:
        raise AppError("SESSION_NOT_FOUND", f"No session with id {session_id}.", status_code=404)
    return session


@router.post("", response_model=SessionRead, status_code=status.HTTP_201_CREATED)
async def create_session(
    body: SessionCreate,
    user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
) -> Session:
    book = await db.get(Book, body.book_id)
    if book is None:
        raise AppError("BOOK_NOT_FOUND", f"No book with id {body.book_id}.", status_code=404)

    grade = body.grade if body.grade is not None else user.grade
    session = Session(user_id=user.id, book_id=body.book_id, grade=grade)
    db.add(session)
    await db.commit()
    await db.refresh(session)
    return session


@router.get("", response_model=list[SessionRead])
async def list_sessions(
    user_id: uuid.UUID = Depends(get_current_user_id),
    db: AsyncSession = Depends(get_db),
) -> list[Session]:
    result = await db.execute(
        select(Session)
        .where(Session.user_id == user_id, Session.deleted_at.is_(None))
        .order_by(Session.updated_at.desc())
    )
    return list(result.scalars().all())


@router.get("/{session_id}", response_model=SessionRead)
async def get_session(
    session_id: uuid.UUID,
    user_id: uuid.UUID = Depends(get_current_user_id),
    db: AsyncSession = Depends(get_db),
) -> Session:
    return await _get_owned_session(db, session_id, user_id)


@router.get("/{session_id}/messages", response_model=list[MessageRead])
async def get_session_messages(
    session_id: uuid.UUID,
    user_id: uuid.UUID = Depends(get_current_user_id),
    db: AsyncSession = Depends(get_db),
) -> list[Message]:
    await _get_owned_session(db, session_id, user_id)
    result = await db.execute(
        select(Message).where(Message.session_id == session_id).order_by(Message.created_at.asc())
    )
    return list(result.scalars().all())


@router.patch("/{session_id}", response_model=SessionRead)
async def rename_session(
    session_id: uuid.UUID,
    body: SessionUpdate,
    user_id: uuid.UUID = Depends(get_current_user_id),
    db: AsyncSession = Depends(get_db),
) -> Session:
    session = await _get_owned_session(db, session_id, user_id)
    session.title = body.title
    await db.commit()
    await db.refresh(session)
    return session


@router.delete("/{session_id}", status_code=status.HTTP_204_NO_CONTENT)
async def delete_session(
    session_id: uuid.UUID,
    user_id: uuid.UUID = Depends(get_current_user_id),
    db: AsyncSession = Depends(get_db),
) -> None:
    session = await _get_owned_session(db, session_id, user_id)
    session.deleted_at = datetime.now(timezone.utc)
    await db.commit()


async def _require_ready_book(db: AsyncSession, session: Session) -> None:
    book = await db.get(Book, session.book_id)
    if book is None or book.status != BookStatus.READY:
        raise AppError("BOOK_NOT_READY", "The book for this session is not ready yet.", status_code=409)


async def _touch_session(db: AsyncSession, session: Session, question: str) -> None:
    """Runs after the user's message is added, before the pipeline: titles
    the session from its first question if it doesn't have one yet
    (self-healing -- a failed attempt just tries again next message, see
    pipeline/title.py), and bumps updated_at so "most recently active"
    sorting (the sidebar) reflects this turn. Caller commits."""
    if session.title is None:
        session.title = await asyncio.to_thread(generate_title, question)
    session.updated_at = datetime.now(timezone.utc)


@router.post("/{session_id}/messages", response_model=MessageRead, status_code=status.HTTP_201_CREATED)
async def create_message(
    session_id: uuid.UUID,
    body: MessageCreate,
    user_id: uuid.UUID = Depends(get_current_user_id),
    db: AsyncSession = Depends(get_db),
) -> Message:
    session = await _get_owned_session(db, session_id, user_id)
    await _require_ready_book(db, session)

    db.add(Message(session_id=session_id, role=MessageRole.USER, content=body.content))
    await _touch_session(db, session, body.content)
    await db.commit()

    result = await run_pipeline(body.content, grade=session.grade, book_id=session.book_id, session_id=session_id)

    assistant_message = Message(
        session_id=session_id,
        role=MessageRole.ASSISTANT,
        content=result.answer,
        status=result.status,
        sources=result.sources,
        verification=asdict(result.verification) if result.verification is not None else None,
        readability=asdict(result.style) if result.style is not None else None,
        config_version=result.config_version,
        latency_ms=result.latency_ms,
    )
    db.add(assistant_message)
    await db.commit()
    await db.refresh(assistant_message)

    response = MessageRead.model_validate(assistant_message)
    return response.model_copy(update={"search_query": result.search_query})


def _sse(kind: str, data: dict) -> str:
    return f"event: {kind}\ndata: {json.dumps(data)}\n\n"


@router.post("/{session_id}/messages/stream")
async def create_message_stream(
    session_id: uuid.UUID,
    body: MessageCreate,
    user_id: uuid.UUID = Depends(get_current_user_id),
    db: AsyncSession = Depends(get_db),
) -> StreamingResponse:
    # Same upfront checks as create_message(), run *before* any streaming
    # starts -- a 404/409 here is a normal JSON error response, not a
    # broken event-stream (the client hasn't received any bytes yet).
    session = await _get_owned_session(db, session_id, user_id)
    await _require_ready_book(db, session)

    db.add(Message(session_id=session_id, role=MessageRole.USER, content=body.content))
    await _touch_session(db, session, body.content)
    await db.commit()

    async def event_stream() -> AsyncIterator[str]:
        try:
            async for event in run_pipeline_stream(
                body.content, grade=session.grade, book_id=session.book_id, session_id=session_id
            ):
                if event.kind == "retrieving":
                    yield _sse("retrieving", event.data)
                elif event.kind == "sources":
                    yield _sse("sources", event.data)
                elif event.kind == "result":
                    result = event.data["result"]

                    assistant_message = Message(
                        session_id=session_id,
                        role=MessageRole.ASSISTANT,
                        content=result.answer,
                        status=result.status,
                        sources=result.sources,
                        verification=asdict(result.verification) if result.verification is not None else None,
                        readability=asdict(result.style) if result.style is not None else None,
                        config_version=result.config_version,
                        latency_ms=result.latency_ms,
                    )
                    db.add(assistant_message)
                    await db.commit()
                    await db.refresh(assistant_message)

                    # The answer is already durably saved above -- everything
                    # from here on is presentation only. A client that
                    # disconnects mid-typing-effect loses nothing.
                    for word in result.answer.split(" "):
                        yield _sse("token", {"text": word + " "})
                        await asyncio.sleep(_TOKEN_STREAM_DELAY_SECONDS)

                    if result.verification is not None:
                        yield _sse("verification", asdict(result.verification))

                    message_read = MessageRead.model_validate(assistant_message).model_copy(
                        update={"search_query": result.search_query}
                    )
                    yield _sse("done", json.loads(message_read.model_dump_json()))
        except Exception as exc:  # noqa: BLE001 -- must reach the client as an SSE event, not a bare 500
            yield _sse("error", {"code": "STREAM_FAILED", "message": str(exc)})

    return StreamingResponse(event_stream(), media_type="text/event-stream")
