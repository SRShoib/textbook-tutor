import uuid
from datetime import datetime

from pydantic import BaseModel, ConfigDict


class SessionCreate(BaseModel):
    book_id: uuid.UUID
    # Optional: falls back to the current user's registered grade
    # (CLAUDE.md: "On register, grade is stored on the user and becomes the
    # default for new sessions"). Still overridable per-session.
    grade: int | None = None


class SessionRead(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    book_id: uuid.UUID
    grade: int
    title: str | None
    created_at: datetime
