import enum
import uuid

from pgvector.sqlalchemy import Vector
from sqlalchemy import ForeignKey, Index, Integer, String, Text
from sqlalchemy.dialects.postgresql import JSONB, UUID
from sqlalchemy.orm import Mapped, mapped_column

from app.models.base import Base, str_enum

# bge-m3 dense embedding dimension.
EMBEDDING_DIM = 1024


class ChunkType(str, enum.Enum):
    PASSAGE = "passage"
    VOCABULARY = "vocabulary"
    EXERCISE = "exercise"


class Chunk(Base):
    __tablename__ = "chunks"
    __table_args__ = (Index("ix_chunks_lesson_id", "lesson_id"),)

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    book_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), ForeignKey("books.id"), nullable=False)
    # Groups chunks belonging to the same lesson for parent-lesson expansion at
    # retrieval time. Not a foreign key: this book has no separate lessons table,
    # a lesson is a (unit, stage) pair within one book, e.g. "u2-s3".
    lesson_id: Mapped[str] = mapped_column(String, nullable=False)
    unit: Mapped[int] = mapped_column(Integer, nullable=False)
    lesson_no: Mapped[int] = mapped_column(Integer, nullable=False)
    lesson_title: Mapped[str] = mapped_column(String, nullable=False)
    page: Mapped[int] = mapped_column(Integer, nullable=False)
    type: Mapped[ChunkType] = mapped_column(str_enum(ChunkType, "chunk_type"), nullable=False)
    text: Mapped[str] = mapped_column(Text, nullable=False)
    embedding: Mapped[list[float]] = mapped_column(Vector(EMBEDDING_DIM), nullable=False)
    # bge-m3 sparse (lexical) weights: {token_id: weight}, JSON-safe (string keys).
    sparse: Mapped[dict] = mapped_column(JSONB, nullable=False)
