"""
What: POST /register, /login, /refresh, /logout and GET /me. Together these
      turn the placeholder ALLOW_ANONYMOUS-only auth in core/auth.py into
      real login, while leaving that dev-user path in place for local
      dev/eval runs (CLAUDE.md: ALLOW_ANONYMOUS is for "local dev and the
      eval runner only").

Why login returns identical errors for "no such email" and "wrong
      password": returning a different error for each would let this
      endpoint be used to discover which children have accounts.

Why refresh tokens rotate (old one revoked, new one issued) on every use,
      and a *reused* refresh token revokes the whole family: without
      rotation there's a single long-lived bearer token that, if it ever
      leaked (e.g. a shared school computer), would work until it expired.
      With rotation, presenting an already-revoked token is a strong signal
      that this token was copied out from under its owner, so every other
      active token for that user is revoked too rather than only the one
      being replayed.
"""

from __future__ import annotations

from datetime import datetime, timezone

from fastapi import APIRouter, Depends, Request, Response, status
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.auth import get_current_user
from app.core.config import get_settings
from app.core.db import get_db
from app.core.errors import AppError
from app.core.rate_limit import clear as rate_limit_clear
from app.core.rate_limit import is_blocked as rate_limit_is_blocked
from app.core.rate_limit import record_failure as rate_limit_record_failure
from app.core.security import (
    create_access_token,
    create_refresh_token,
    decode_token,
    hash_password,
    hash_refresh_token,
    verify_password,
)
from app.models.refresh_token import RefreshToken
from app.models.user import User, UserRole
from app.schemas.user import LoginRequest, RegisterRequest, TokenResponse, UserRead

router = APIRouter(prefix="/auth", tags=["auth"])

REFRESH_COOKIE_NAME = "refresh_token"
REFRESH_COOKIE_PATH = "/api/v1/auth"


async def _issue_token_pair(db: AsyncSession, user: User) -> tuple[str, str]:
    """Persists a new refresh_tokens row and returns (access_token, refresh_token_raw)."""
    access_token = create_access_token(user.id, user.role.value)
    refresh_token_raw, token_hash, expires_at = create_refresh_token(user.id)
    db.add(RefreshToken(user_id=user.id, token_hash=token_hash, expires_at=expires_at))
    await db.commit()
    return access_token, refresh_token_raw


def _set_refresh_cookie(response: Response, token: str) -> None:
    settings = get_settings()
    response.set_cookie(
        key=REFRESH_COOKIE_NAME,
        value=token,
        httponly=True,
        samesite="lax",
        secure=settings.cookie_secure,
        max_age=settings.refresh_token_days * 86400,
        path=REFRESH_COOKIE_PATH,
    )


def _clear_refresh_cookie(response: Response) -> None:
    response.delete_cookie(key=REFRESH_COOKIE_NAME, path=REFRESH_COOKIE_PATH)


def _token_response(access_token: str, user: User) -> TokenResponse:
    settings = get_settings()
    return TokenResponse(
        access_token=access_token,
        expires_in=settings.access_token_minutes * 60,
        user=UserRead.model_validate(user),
    )


@router.post("/register", response_model=TokenResponse, status_code=status.HTTP_201_CREATED)
async def register(body: RegisterRequest, response: Response, db: AsyncSession = Depends(get_db)) -> TokenResponse:
    existing = await db.scalar(select(User).where(User.email == body.email))
    if existing is not None:
        raise AppError("EMAIL_TAKEN", "An account with this email already exists.", status_code=409)

    user = User(
        email=body.email,
        password_hash=hash_password(body.password),
        display_name=body.display_name,
        role=UserRole.STUDENT,
        grade=body.grade,
        is_active=True,
    )
    db.add(user)
    await db.commit()
    await db.refresh(user)

    access_token, refresh_token_raw = await _issue_token_pair(db, user)
    _set_refresh_cookie(response, refresh_token_raw)
    return _token_response(access_token, user)


@router.post("/login", response_model=TokenResponse)
async def login(body: LoginRequest, request: Request, response: Response, db: AsyncSession = Depends(get_db)) -> TokenResponse:
    settings = get_settings()
    client_ip = request.client.host if request.client else "unknown"
    rate_key = f"{body.email.lower()}:{client_ip}"
    now = datetime.now(timezone.utc)

    if rate_limit_is_blocked(
        rate_key,
        limit=settings.login_rate_limit_attempts,
        window_seconds=settings.login_rate_limit_window_seconds,
        now=now,
    ):
        raise AppError("RATE_LIMITED", "Too many login attempts. Try again later.", status_code=429)

    user = await db.scalar(select(User).where(User.email == body.email))
    if user is None or not user.is_active or not verify_password(body.password, user.password_hash):
        rate_limit_record_failure(rate_key, window_seconds=settings.login_rate_limit_window_seconds, now=now)
        raise AppError("INVALID_CREDENTIALS", "Incorrect email or password.", status_code=401)

    rate_limit_clear(rate_key)
    user.last_login_at = now
    await db.commit()

    access_token, refresh_token_raw = await _issue_token_pair(db, user)
    _set_refresh_cookie(response, refresh_token_raw)
    return _token_response(access_token, user)


@router.post("/refresh", response_model=TokenResponse)
async def refresh(request: Request, response: Response, db: AsyncSession = Depends(get_db)) -> TokenResponse:
    presented = request.cookies.get(REFRESH_COOKIE_NAME)
    if presented is None:
        raise AppError("REFRESH_TOKEN_MISSING", "No refresh token cookie was sent.", status_code=401)

    payload = decode_token(presented, expected_type="refresh")
    token_hash = hash_refresh_token(presented)
    row = await db.scalar(select(RefreshToken).where(RefreshToken.token_hash == token_hash))

    if row is None:
        raise AppError("TOKEN_INVALID", "Refresh token is not recognised.", status_code=401)

    now = datetime.now(timezone.utc)

    if row.revoked_at is not None:
        # This token was already rotated away once — being presented again
        # means it was copied somewhere it shouldn't have been. Revoke the
        # whole family rather than just this one token.
        active = (
            await db.scalars(
                select(RefreshToken).where(RefreshToken.user_id == row.user_id, RefreshToken.revoked_at.is_(None))
            )
        ).all()
        for token_row in active:
            token_row.revoked_at = now
        await db.commit()
        raise AppError("REFRESH_TOKEN_REUSED", "This refresh token was already used. All sessions were signed out.", status_code=401)

    if row.expires_at <= now:
        raise AppError("TOKEN_EXPIRED", "Refresh token has expired.", status_code=401)

    user = await db.get(User, payload.user_id)
    if user is None or not user.is_active:
        raise AppError("TOKEN_INVALID", "Token does not match an active user.", status_code=401)

    row.revoked_at = now
    access_token, refresh_token_raw = await _issue_token_pair(db, user)
    _set_refresh_cookie(response, refresh_token_raw)
    return _token_response(access_token, user)


@router.post("/logout", status_code=status.HTTP_204_NO_CONTENT)
async def logout(request: Request, response: Response, db: AsyncSession = Depends(get_db)) -> None:
    presented = request.cookies.get(REFRESH_COOKIE_NAME)
    if presented is not None:
        token_hash = hash_refresh_token(presented)
        row = await db.scalar(select(RefreshToken).where(RefreshToken.token_hash == token_hash))
        if row is not None and row.revoked_at is None:
            row.revoked_at = datetime.now(timezone.utc)
            await db.commit()

    _clear_refresh_cookie(response)


@router.get("/me", response_model=UserRead)
async def me(user: User = Depends(get_current_user)) -> User:
    return user
