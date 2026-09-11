import uuid
from datetime import datetime

from pydantic import BaseModel, ConfigDict

from app.models.book import BookStatus


class BookRead(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    title: str
    grade: int
    status: BookStatus
    chunk_count: int
    created_at: datetime
