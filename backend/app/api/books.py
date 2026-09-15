"""
What: POST /books accepts a PDF, stores it, and kicks off ingest_book() as a
      background task; GET "" lists every book for the admin dashboard;
      GET /books/{id} is the status-polling endpoint; DELETE /books/{id}
      removes a book and its chunks.
Why: ingest.py can take a while (PDF parsing + bge-m3 embedding of ~150
     chunks), so CLAUDE.md's API convention is 202 + poll rather than making
     the client wait on one long request.
Why POST/GET ""/DELETE require an admin but GET /{id} doesn't: books have no
     user_id column -- once ingested, a book is a shared resource every
     matching-grade student's session points at, not private data. Managing
     that shared resource (uploading, listing all of them, deleting one) is
     an admin action (2026-09-15 decision, CLAUDE.md); reading a single
     book's status by id is not -- a student's chat screen may want to show
     the title/status of the book their own session already points at.
"""

import uuid
from pathlib import Path

from fastapi import APIRouter, BackgroundTasks, Depends, File, Form, UploadFile, status
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.auth import get_current_user_id, require_admin
from app.core.db import get_db
from app.core.errors import AppError
from app.models.book import Book, BookStatus
from app.models.chunk import Chunk
from app.models.session import Session
from app.models.user import User
from app.pipeline.ingest import file_sha256, ingest_book
from app.schemas.book import BookRead

router = APIRouter(prefix="/books", tags=["books"])

# Reuses data/textbook/, which .gitignore already excludes *.pdf from, rather
# than introducing a second data directory.
_UPLOAD_DIR = Path(__file__).resolve().parents[3] / "data" / "textbook"


@router.post("", response_model=BookRead, status_code=status.HTTP_202_ACCEPTED)
async def create_book(
    background_tasks: BackgroundTasks,
    file: UploadFile = File(...),
    title: str = Form(...),
    grade: int = Form(...),
    admin: User = Depends(require_admin),
    db: AsyncSession = Depends(get_db),
) -> Book:
    if file.content_type != "application/pdf":
        raise AppError("UNSUPPORTED_FILE_TYPE", "Only PDF files are accepted.")

    data = await file.read()
    file_hash = file_sha256(data)

    existing = await db.scalar(select(Book).where(Book.file_hash == file_hash))
    if existing is not None:
        return existing

    book = Book(title=title, grade=grade, status=BookStatus.PROCESSING, chunk_count=0, file_hash=file_hash)
    db.add(book)
    await db.commit()
    await db.refresh(book)

    _UPLOAD_DIR.mkdir(parents=True, exist_ok=True)
    pdf_path = _UPLOAD_DIR / f"{book.id}.pdf"
    pdf_path.write_bytes(data)

    background_tasks.add_task(ingest_book, book.id, pdf_path)

    return book


@router.get("", response_model=list[BookRead])
async def list_books(
    admin: User = Depends(require_admin),
    db: AsyncSession = Depends(get_db),
) -> list[Book]:
    result = await db.execute(select(Book).order_by(Book.created_at.desc()))
    return list(result.scalars().all())


@router.get("/{book_id}", response_model=BookRead)
async def get_book(
    book_id: uuid.UUID,
    user_id: uuid.UUID = Depends(get_current_user_id),
    db: AsyncSession = Depends(get_db),
) -> Book:
    book = await db.get(Book, book_id)
    if book is None:
        raise AppError("BOOK_NOT_FOUND", f"No book with id {book_id}.", status_code=404)
    return book


@router.delete("/{book_id}", status_code=status.HTTP_204_NO_CONTENT)
async def delete_book(
    book_id: uuid.UUID,
    admin: User = Depends(require_admin),
    db: AsyncSession = Depends(get_db),
) -> None:
    book = await db.get(Book, book_id)
    if book is None:
        raise AppError("BOOK_NOT_FOUND", f"No book with id {book_id}.", status_code=404)

    # Includes soft-deleted sessions: sessions.book_id is a plain FK with no
    # ON DELETE rule, and CLAUDE.md's soft-delete rule means a soft-deleted
    # session row still exists and still points at this book -- hard-deleting
    # it out from under one would raise a ForeignKeyViolationError anyway.
    # Since the newest ready book for a grade always wins (2026-09-15
    # decision), replacing a book never requires deleting the old one; this
    # only blocks deleting a book real chats actually used.
    session_count = await db.scalar(select(func.count()).select_from(Session).where(Session.book_id == book_id))
    if session_count:
        raise AppError(
            "BOOK_HAS_SESSIONS",
            "This book has chat sessions and can't be deleted. Upload a replacement instead -- "
            "the newest ready book for a grade is used automatically.",
            status_code=409,
        )

    await db.execute(Chunk.__table__.delete().where(Chunk.book_id == book_id))
    await db.delete(book)
    await db.commit()

    pdf_path = _UPLOAD_DIR / f"{book_id}.pdf"
    pdf_path.unlink(missing_ok=True)
