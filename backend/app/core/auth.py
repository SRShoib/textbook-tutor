"""
What: get_current_user_id() resolves the requesting user for every
      /sessions and /messages route (CLAUDE.md: "All /sessions and
      /messages routes require auth and scope to current_user.id"). This
      phase only implements the ALLOW_ANONYMOUS branch — get-or-create a
      fixed dev user — because real JWT (register/login/refresh) is Phase 5.
      Without at least this much, there is no current_user.id to scope to.

Why get-or-create rather than a hardcoded UUID constant: sessions.user_id is
      a real foreign key, so a real row has to exist. Get-or-create means
      the dev user reappears automatically after a fresh database (e.g.
      docker compose down -v) with no separate seed script to remember to
      run.
"""

from __future__ import annotations

import uuid

from fastapi import Depends
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import get_settings
from app.core.db import get_db
from app.core.errors import AppError
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


async def get_current_user_id(db: AsyncSession = Depends(get_db)) -> uuid.UUID:
    settings = get_settings()
    if not settings.allow_anonymous:
        # Real JWT auth doesn't exist yet — Phase 5.
        raise AppError("AUTH_REQUIRED", "Login is required.", status_code=401)

    user = await get_or_create_dev_user(db)
    return user.id
