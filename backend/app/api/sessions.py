"""
What: POST /sessions creates a session (minimal — list, rename and
      soft-delete stay Phase 5); POST /sessions/{id}/messages sends one
      question through the pipeline and returns the assistant's message as
      the full evidence object CLAUDE.md's API conventions call for (answer,
      status, sources, verification, readability, latency_ms,
      config_version).
Why POST /sessions exists already, ahead of Phase 5's sessions CRUD: without
      it there is no session row for /messages to attach to, and eval/runner.py
      aside, that is the only way one gets created before Phase 5 builds the
      rest (list, title generation, soft-delete).
"""

from __future__ import annotations

import uuid

from fastapi import APIRouter, Depends, status
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.auth import get_current_user_id
from app.core.db import get_db
from app.core.errors import AppError
from app.models.book import Book, BookStatus
from app.models.message import Message, MessageRole
from app.models.session import Session
from app.pipeline.graph import run_pipeline
from app.schemas.message import MessageCreate, MessageRead
from app.schemas.session import SessionCreate, SessionRead

router = APIRouter(prefix="/sessions", tags=["sessions"])


@router.post("", response_model=SessionRead, status_code=status.HTTP_201_CREATED)
async def create_session(
    body: SessionCreate,
    user_id: uuid.UUID = Depends(get_current_user_id),
    db: AsyncSession = Depends(get_db),
) -> Session:
    book = await db.get(Book, body.book_id)
    if book is None:
        raise AppError("BOOK_NOT_FOUND", f"No book with id {body.book_id}.", status_code=404)

    session = Session(user_id=user_id, book_id=body.book_id, grade=body.grade)
    db.add(session)
    await db.commit()
    await db.refresh(session)
    return session


@router.post("/{session_id}/messages", response_model=MessageRead, status_code=status.HTTP_201_CREATED)
async def create_message(
    session_id: uuid.UUID,
    body: MessageCreate,
    user_id: uuid.UUID = Depends(get_current_user_id),
    db: AsyncSession = Depends(get_db),
) -> Message:
    session = await db.get(Session, session_id)
    if session is None or session.user_id != user_id or session.deleted_at is not None:
        raise AppError("SESSION_NOT_FOUND", f"No session with id {session_id}.", status_code=404)

    book = await db.get(Book, session.book_id)
    if book is None or book.status != BookStatus.READY:
        raise AppError("BOOK_NOT_READY", "The book for this session is not ready yet.", status_code=409)

    db.add(Message(session_id=session_id, role=MessageRole.USER, content=body.content))
    await db.commit()

    result = await run_pipeline(body.content, grade=session.grade, book_id=session.book_id)

    assistant_message = Message(
        session_id=session_id,
        role=MessageRole.ASSISTANT,
        content=result.answer,
        status=result.status,
        sources=result.sources,
        verification=None,
        readability=None,
        config_version=result.config_version,
        latency_ms=result.latency_ms,
    )
    db.add(assistant_message)
    await db.commit()
    await db.refresh(assistant_message)
    return assistant_message
