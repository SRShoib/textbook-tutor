"""
What: one row per issued password-reset token. Storing a hash (not the
      token itself) mirrors refresh_tokens -- lets a used or expired token
      be rejected without ever persisting something that would let anyone
      reading the database reset a user's password themselves.
Why a used_at column instead of deleting the row on use: same reasoning as
      refresh_tokens.revoked_at -- a used token still existing (marked used)
      is what makes "this link was already used" a distinct, honest error
      from "this link never existed."
"""

import uuid
from datetime import datetime

from sqlalchemy import DateTime, ForeignKey, String, func
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import Mapped, mapped_column

from app.models.base import Base


class PasswordResetToken(Base):
    __tablename__ = "password_reset_tokens"

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    user_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), ForeignKey("users.id"), nullable=False, index=True)
    # sha256 of the token, not an argon2 hash -- see core/security.py's
    # create_password_reset_token() docstring for why a fast digest is fine
    # here (same reasoning as refresh tokens: the token is random and
    # unguessable, not a human-chosen password).
    token_hash: Mapped[str] = mapped_column(String, unique=True, nullable=False)
    expires_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    used_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
