"""
What: get_current_user() resolves the requesting user for every /sessions
      and /messages route (CLAUDE.md: "All /sessions and /messages routes
      require auth and scope to current_user.id"), with this precedence:
        1. a valid `Authorization: Bearer <access token>` header — real login
        2. no header at all, and ALLOW_ANONYMOUS=true — the fixed dev user
        3. otherwise — 401 AUTH_REQUIRED

      Two deliberate choices in that ordering: a valid Bearer token wins
      over the dev fallback even when ALLOW_ANONYMOUS=true, so real login
      can be exercised locally without flipping env vars; and a header that
      is *present but invalid* is a 401 even under ALLOW_ANONYMOUS, rather
      than silently falling back to the dev user — a frontend bug that
      sends a bad token should surface as an error, not quietly write rows
      under someone else's account.

Why get-or-create rather than a hardcoded UUID constant for the dev user:
      sessions.user_id is a real foreign key, so a real row has to exist.
      Get-or-create means the dev user reappears automatically after a
      fresh database (e.g. docker compose down -v) with no separate seed
      script to remember to run.
"""

from __future__ import annotations

import uuid

from fastapi import Depends, Request
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import get_settings
from app.core.db import get_db
from app.core.errors import AppError
from app.core.security import decode_token
from app.models.user import User, UserRole

DEV_USER_EMAIL = "dev@textbook-tutor.local"


async def get_or_create_dev_user(db: AsyncSession) -> User:
    """Shared by get_current_user_id() below and eval/runner.py, which needs
    the same dev user for its own session rows but has no FastAPI request to
    hang a Depends() on."""
    user = await db.scalar(select(User).where(User.email == DEV_USER_EMAIL))
    if user is None:
        user = User(
            email=DEV_USER_EMAIL,
            # No login exists for this user yet; ALLOW_ANONYMOUS bypasses
            # password auth entirely rather than using a fake credential.
            password_hash="",
            display_name="Dev User",
            role=UserRole.STUDENT,
            grade=5,
            is_active=True,
        )
        db.add(user)
        await db.commit()
        await db.refresh(user)
    return user


async def get_current_user(request: Request, db: AsyncSession = Depends(get_db)) -> User:
    header = request.headers.get("Authorization")

    if header is not None:
        if not header.startswith("Bearer "):
            raise AppError("TOKEN_INVALID", "Expected a Bearer token.", status_code=401)
        token = header.removeprefix("Bearer ")
        payload = decode_token(token, expected_type="access")
        user = await db.get(User, payload.user_id)
        if user is None or not user.is_active:
            raise AppError("TOKEN_INVALID", "Token does not match an active user.", status_code=401)
        return user

    settings = get_settings()
    if settings.allow_anonymous:
        return await get_or_create_dev_user(db)

    raise AppError("AUTH_REQUIRED", "Login is required.", status_code=401)


async def get_current_user_id(user: User = Depends(get_current_user)) -> uuid.UUID:
    return user.id
