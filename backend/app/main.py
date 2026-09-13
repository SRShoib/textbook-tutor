"""FastAPI entrypoint: error handlers, routers, and /health."""

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from sqlalchemy import text

from app.api.auth import router as auth_router
from app.api.books import router as books_router
from app.api.sessions import router as sessions_router
from app.core.config import get_settings
from app.core.db import engine
from app.core.errors import register_error_handlers

app = FastAPI(title="Textbook Tutor API")
register_error_handlers(app)

# allow_credentials requires a single explicit origin (never "*") so the
# browser will actually attach the httpOnly refresh cookie on cross-port
# requests from the Next.js dev server -- see api/auth.py's cookie settings.
app.add_middleware(
    CORSMiddleware,
    allow_origins=[get_settings().frontend_origin],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

app.include_router(auth_router, prefix="/api/v1")
app.include_router(books_router, prefix="/api/v1")
app.include_router(sessions_router, prefix="/api/v1")


@app.get("/health")
async def health() -> dict:
    async with engine.connect() as conn:
        await conn.execute(text("SELECT 1"))
    return {"status": "ok"}
