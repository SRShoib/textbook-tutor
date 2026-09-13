"""
What: session CRUD (create, list, get-one, rename, soft-delete) plus
      POST /sessions/{id}/messages, which sends one question through the
      pipeline and returns the assistant's message as the full evidence
      object CLAUDE.md's API conventions call for (answer, status, sources,
      verification, readability, latency_ms, config_version).
Why create_message() also sets session.title and bumps session.updated_at:
      a session has no human-readable title until its first question comes
      in (see pipeline/title.py), and the row is otherwise never touched
      again after creation -- without an explicit bump here, "sort sessions
      by updated_at" would silently mean "by creation time," not "by last
      activity," which is what a sidebar actually wants.
"""

from __future__ import annotations

import asyncio
import uuid
from dataclasses import asdict
from datetime import datetime, timezone

from fastapi import APIRouter, Depends, status
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.auth import get_current_user, get_current_user_id
from app.core.db import get_db
from app.core.errors import AppError
from app.models.book import Book, BookStatus
from app.models.message import Message, MessageRole
from app.models.session import Session
from app.models.user import User
from app.pipeline.graph import run_pipeline
from app.pipeline.title import generate_title
from app.schemas.message import MessageCreate, MessageRead
from app.schemas.session import SessionCreate, SessionRead, SessionUpdate

router = APIRouter(prefix="/sessions", tags=["sessions"])


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


@router.post("/{session_id}/messages", response_model=MessageRead, status_code=status.HTTP_201_CREATED)
async def create_message(
    session_id: uuid.UUID,
    body: MessageCreate,
    user_id: uuid.UUID = Depends(get_current_user_id),
    db: AsyncSession = Depends(get_db),
) -> Message:
    session = await _get_owned_session(db, session_id, user_id)

    book = await db.get(Book, session.book_id)
    if book is None or book.status != BookStatus.READY:
        raise AppError("BOOK_NOT_READY", "The book for this session is not ready yet.", status_code=409)

    db.add(Message(session_id=session_id, role=MessageRole.USER, content=body.content))

    if session.title is None:
        session.title = await asyncio.to_thread(generate_title, body.content)
    session.updated_at = datetime.now(timezone.utc)
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
