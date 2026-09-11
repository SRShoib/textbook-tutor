"""
What: POST /books accepts a PDF, stores it, and kicks off ingest_book() as a
      background task; GET /books/{id} is the status-polling endpoint.
Why: ingest.py can take a while (PDF parsing + bge-m3 embedding of ~150
     chunks), so CLAUDE.md's API convention is 202 + poll rather than making
     the client wait on one long request.
"""

import uuid
from pathlib import Path

from fastapi import APIRouter, BackgroundTasks, Depends, File, Form, UploadFile, status
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.db import get_db
from app.core.errors import AppError
from app.models.book import Book, BookStatus
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


@router.get("/{book_id}", response_model=BookRead)
async def get_book(book_id: uuid.UUID, db: AsyncSession = Depends(get_db)) -> Book:
    book = await db.get(Book, book_id)
    if book is None:
        raise AppError("BOOK_NOT_FOUND", f"No book with id {book_id}.", status_code=404)
    return book
