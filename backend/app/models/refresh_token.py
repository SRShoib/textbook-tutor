"""
What: one row per issued refresh token. Storing a hash (not the token itself)
      lets POST /auth/logout revoke a real session and lets POST /auth/refresh
      detect a stolen token being replayed after rotation.
Why a table instead of a stateless refresh JWT: CLAUDE.md's API list includes
      POST /auth/logout, which only means something if a refresh token can
      actually be invalidated server-side. A stateless refresh token can't be
      revoked before it expires.
"""

import uuid
from datetime import datetime

from sqlalchemy import DateTime, ForeignKey, String, func
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import Mapped, mapped_column

from app.models.base import Base


class RefreshToken(Base):
    __tablename__ = "refresh_tokens"

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    user_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), ForeignKey("users.id"), nullable=False, index=True)
    # sha256 of the token, not an argon2 hash — see core/security.py's
    # create_refresh_token() docstring for why a fast digest is fine here.
    token_hash: Mapped[str] = mapped_column(String, unique=True, nullable=False)
    expires_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    revoked_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
