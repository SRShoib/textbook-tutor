"""initial schema: users, books, chunks, sessions, messages

Revision ID: 0001
Revises:
Create Date: 2026-09-11

"""
from typing import Sequence, Union

import pgvector.sqlalchemy
import sqlalchemy as sa
from alembic import op

# revision identifiers, used by Alembic.
revision: str = "0001"
down_revision: Union[str, Sequence[str], None] = None
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None

EMBEDDING_DIM = 1024


def upgrade() -> None:
    op.execute("CREATE EXTENSION IF NOT EXISTS vector")

    op.create_table(
        "users",
        sa.Column("id", sa.dialects.postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column("email", sa.String(), nullable=False, unique=True),
        sa.Column("password_hash", sa.String(), nullable=False),
        sa.Column("display_name", sa.String(), nullable=False),
        sa.Column(
            "role",
            sa.Enum("student", "teacher", "admin", name="user_role"),
            nullable=False,
        ),
        sa.Column("grade", sa.Integer(), nullable=False),
        sa.Column("is_active", sa.Boolean(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
        sa.Column("last_login_at", sa.DateTime(timezone=True), nullable=True),
    )

    op.create_table(
        "books",
        sa.Column("id", sa.dialects.postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column("title", sa.String(), nullable=False),
        sa.Column("grade", sa.Integer(), nullable=False),
        sa.Column(
            "status",
            sa.Enum("processing", "ready", "failed", name="book_status"),
            nullable=False,
        ),
        sa.Column("chunk_count", sa.Integer(), nullable=False),
        sa.Column("file_hash", sa.String(), nullable=False, unique=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
    )

    op.create_table(
        "chunks",
        sa.Column("id", sa.dialects.postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column("book_id", sa.dialects.postgresql.UUID(as_uuid=True), sa.ForeignKey("books.id"), nullable=False),
        sa.Column("lesson_id", sa.String(), nullable=False),
        sa.Column("unit", sa.Integer(), nullable=False),
        sa.Column("lesson_no", sa.Integer(), nullable=False),
        sa.Column("lesson_title", sa.String(), nullable=False),
        sa.Column("page", sa.Integer(), nullable=False),
        sa.Column(
            "type",
            sa.Enum("passage", "vocabulary", "exercise", name="chunk_type"),
            nullable=False,
        ),
        sa.Column("text", sa.Text(), nullable=False),
        sa.Column("embedding", pgvector.sqlalchemy.Vector(EMBEDDING_DIM), nullable=False),
        sa.Column("sparse", sa.dialects.postgresql.JSONB(), nullable=False),
    )
    op.create_index("ix_chunks_lesson_id", "chunks", ["lesson_id"])

    op.create_table(
        "sessions",
        sa.Column("id", sa.dialects.postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column("user_id", sa.dialects.postgresql.UUID(as_uuid=True), sa.ForeignKey("users.id"), nullable=False),
        sa.Column("book_id", sa.dialects.postgresql.UUID(as_uuid=True), sa.ForeignKey("books.id"), nullable=False),
        sa.Column("grade", sa.Integer(), nullable=False),
        sa.Column("title", sa.String(), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
        sa.Column("deleted_at", sa.DateTime(timezone=True), nullable=True),
    )

    op.create_table(
        "messages",
        sa.Column("id", sa.dialects.postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column(
            "session_id", sa.dialects.postgresql.UUID(as_uuid=True), sa.ForeignKey("sessions.id"), nullable=False
        ),
        sa.Column("role", sa.Enum("user", "assistant", name="message_role"), nullable=False),
        sa.Column("content", sa.String(), nullable=False),
        sa.Column(
            "status",
            sa.Enum(
                "answered",
                "low_confidence",
                "refused_off_book",
                "refused_unverified",
                name="message_status",
            ),
            nullable=True,
        ),
        sa.Column("sources", sa.dialects.postgresql.JSONB(), nullable=True),
        sa.Column("verification", sa.dialects.postgresql.JSONB(), nullable=True),
        sa.Column("readability", sa.dialects.postgresql.JSONB(), nullable=True),
        sa.Column("config_version", sa.String(), nullable=True),
        sa.Column("latency_ms", sa.Integer(), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
    )


def downgrade() -> None:
    op.drop_table("messages")
    op.drop_table("sessions")
    op.drop_table("chunks")
    op.drop_table("books")
    op.drop_table("users")
    op.execute("DROP TYPE IF EXISTS message_status")
    op.execute("DROP TYPE IF EXISTS message_role")
    op.execute("DROP TYPE IF EXISTS chunk_type")
    op.execute("DROP TYPE IF EXISTS book_status")
    op.execute("DROP TYPE IF EXISTS user_role")
