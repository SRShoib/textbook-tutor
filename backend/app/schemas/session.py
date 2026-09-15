import uuid
from datetime import datetime

from pydantic import BaseModel, ConfigDict, Field


class SessionUpdate(BaseModel):
    """PATCH /sessions/{id} -- manual rename only, for now."""

    title: str = Field(min_length=1, max_length=200)


class SessionRead(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    book_id: uuid.UUID
    grade: int
    title: str | None
    created_at: datetime
    # Bumped on every message, not just session edits (api/sessions.py's
    # create_message()) -- so the sidebar can sort by "most recently active"
    # rather than just "session row last touched."
    updated_at: datetime
