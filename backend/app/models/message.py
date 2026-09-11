import enum
import uuid
from datetime import datetime

from sqlalchemy import DateTime, ForeignKey, Integer, String, func
from sqlalchemy.dialects.postgresql import JSONB, UUID
from sqlalchemy.orm import Mapped, mapped_column

from app.models.base import Base, str_enum


class MessageRole(str, enum.Enum):
    USER = "user"
    ASSISTANT = "assistant"


class MessageStatus(str, enum.Enum):
    ANSWERED = "answered"
    LOW_CONFIDENCE = "low_confidence"
    REFUSED_OFF_BOOK = "refused_off_book"
    REFUSED_UNVERIFIED = "refused_unverified"


class Message(Base):
    __tablename__ = "messages"

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    session_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), ForeignKey("sessions.id"), nullable=False)
    role: Mapped[MessageRole] = mapped_column(str_enum(MessageRole, "message_role"), nullable=False)
    content: Mapped[str] = mapped_column(String, nullable=False)
    status: Mapped[MessageStatus | None] = mapped_column(
        str_enum(MessageStatus, "message_status"), nullable=True
    )
    sources: Mapped[dict | None] = mapped_column(JSONB, nullable=True)
    verification: Mapped[dict | None] = mapped_column(JSONB, nullable=True)
    readability: Mapped[dict | None] = mapped_column(JSONB, nullable=True)
    config_version: Mapped[str | None] = mapped_column(String, nullable=True)
    latency_ms: Mapped[int | None] = mapped_column(Integer, nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
