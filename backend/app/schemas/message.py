import uuid
from datetime import datetime

from pydantic import BaseModel, ConfigDict

from app.models.message import MessageRole, MessageStatus


class MessageCreate(BaseModel):
    content: str


class MessageRead(BaseModel):
    """The full evidence object CLAUDE.md's API conventions call for:
    answer (content), status, sources, verification, readability,
    latency_ms, config_version. verification and readability are always
    null this phase — Phases 3 and 4 populate them."""

    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    session_id: uuid.UUID
    role: MessageRole
    content: str
    status: MessageStatus | None
    sources: list[dict] | None
    verification: dict | None
    readability: dict | None
    config_version: str | None
    latency_ms: int | None
    created_at: datetime
