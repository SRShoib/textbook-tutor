"""FastAPI entrypoint: error handlers, routers, and /health."""

from fastapi import FastAPI
from sqlalchemy import text

from app.api.books import router as books_router
from app.api.sessions import router as sessions_router
from app.core.db import engine
from app.core.errors import register_error_handlers

app = FastAPI(title="Textbook Tutor API")
register_error_handlers(app)
app.include_router(books_router, prefix="/api/v1")
app.include_router(sessions_router, prefix="/api/v1")


@app.get("/health")
async def health() -> dict:
    async with engine.connect() as conn:
        await conn.execute(text("SELECT 1"))
    return {"status": "ok"}
