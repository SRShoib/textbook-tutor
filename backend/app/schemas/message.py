import uuid
from datetime import datetime

from pydantic import BaseModel, ConfigDict

from app.models.message import MessageRole, MessageStatus


class MessageCreate(BaseModel):
    content: str


class MessageRead(BaseModel):
    """The full evidence object CLAUDE.md's API conventions call for:
    answer (content), status, sources, verification, readability,
    latency_ms, config_version. verification carries verify.py's
    VerificationReport (Phase 4); readability carries style_check.py's
    StyleReport (Phase 3).

    search_query is not a database column (see CLAUDE.md's fixed schema) —
    it's the standalone query rewrite.py produced for this turn, attached
    only in the POST /messages response so the caller can see what a
    follow-up was rewritten to. A message loaded straight from the ORM
    (from_attributes) always has it as None."""

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
    search_query: str | None = None
