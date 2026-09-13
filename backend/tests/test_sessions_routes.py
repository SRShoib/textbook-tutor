"""Route tests for the sessions CRUD + title generation added in this
module (api/sessions.py) — a documented exception to CLAUDE.md's "skip
route tests unless asked" rule, same as test_auth_routes.py: ownership
scoping, soft-delete, and the auto-title/updated_at side effects on
create_message() are exactly the kind of behavior a pipeline-level unit
test can't reach.

Needs docker-compose's Postgres running and migrated (`alembic upgrade
head`) — every test here is marked "db"; `pytest -m "not db"` skips this
file entirely."""

from __future__ import annotations

import pytest

from app.models.book import Book, BookStatus
from app.pipeline.graph import PipelineResult
from app.models.message import MessageStatus

pytestmark = pytest.mark.db


async def _register(client, email: str, grade: int = 5) -> str:
    resp = await client.post(
        "/api/v1/auth/register",
        json={"email": email, "password": "hunter22", "display_name": "Test", "grade": grade},
    )
    assert resp.status_code == 201
    return resp.json()["access_token"]


async def _make_book(db_session, *, file_hash: str, grade: int = 5) -> Book:
    book = Book(title="Test Book", grade=grade, status=BookStatus.READY, chunk_count=1, file_hash=file_hash)
    db_session.add(book)
    await db_session.commit()
    await db_session.refresh(book)
    return book


def _auth(token: str) -> dict:
    return {"Authorization": f"Bearer {token}"}


def _fake_pipeline_result(answer: str = "A noun is a naming word.") -> PipelineResult:
    return PipelineResult(
        answer=answer,
        status=MessageStatus.ANSWERED,
        sources=[],
        best_score=0.9,
        latency_ms=10,
        config_version="v1",
        llm=None,
        context_texts=[],
        search_query=answer,
        stage1_answer=answer,
        style=None,
        style_attempts=0,
        verification=None,
        verify_attempts=0,
    )


@pytest.fixture(autouse=True)
def _mock_pipeline(monkeypatch):
    """Every test in this file only exercises session/message bookkeeping,
    never the real RAG pipeline (that needs a real book + OpenAI key,
    covered separately by test_graph.py and the eval runner)."""

    async def fake_run_pipeline(question, grade, book_id, **kwargs):
        return _fake_pipeline_result()

    monkeypatch.setattr("app.api.sessions.run_pipeline", fake_run_pipeline)


@pytest.fixture
def _mock_title(monkeypatch):
    """Returns a controllable fake generate_title -- tests set .value to
    change what the "LLM" would have titled the session."""

    state = {"value": "Nouns And Naming Words"}

    def fake_generate_title(question, **kwargs):
        return state["value"]

    monkeypatch.setattr("app.api.sessions.generate_title", fake_generate_title)
    return state


@pytest.mark.asyncio
async def test_list_sessions_scoped_to_caller(client, db_session):
    token_a = await _register(client, "list-a@example.com")
    token_b = await _register(client, "list-b@example.com")
    book = await _make_book(db_session, file_hash="hash-list")

    await client.post("/api/v1/sessions", json={"book_id": str(book.id)}, headers=_auth(token_a))
    await client.post("/api/v1/sessions", json={"book_id": str(book.id)}, headers=_auth(token_b))

    resp_a = await client.get("/api/v1/sessions", headers=_auth(token_a))
    assert resp_a.status_code == 200
    assert len(resp_a.json()) == 1

    resp_b = await client.get("/api/v1/sessions", headers=_auth(token_b))
    assert len(resp_b.json()) == 1
    assert resp_a.json()[0]["id"] != resp_b.json()[0]["id"]


@pytest.mark.asyncio
async def test_list_sessions_orders_by_most_recently_active(client, db_session, _mock_title):
    token = await _register(client, "order@example.com")
    book = await _make_book(db_session, file_hash="hash-order")

    first = await client.post("/api/v1/sessions", json={"book_id": str(book.id)}, headers=_auth(token))
    second = await client.post("/api/v1/sessions", json={"book_id": str(book.id)}, headers=_auth(token))
    first_id, second_id = first.json()["id"], second.json()["id"]

    # Send a message to the *first* (older) session -- it should now sort
    # ahead of the second, untouched one.
    await client.post(f"/api/v1/sessions/{first_id}/messages", json={"content": "What is a noun?"}, headers=_auth(token))

    listing = await client.get("/api/v1/sessions", headers=_auth(token))
    ids_in_order = [row["id"] for row in listing.json()]
    assert ids_in_order == [first_id, second_id]


@pytest.mark.asyncio
async def test_get_session_not_owned_returns_404(client, db_session):
    token_a = await _register(client, "own-a@example.com")
    token_b = await _register(client, "own-b@example.com")
    book = await _make_book(db_session, file_hash="hash-own")

    created = await client.post("/api/v1/sessions", json={"book_id": str(book.id)}, headers=_auth(token_a))
    session_id = created.json()["id"]

    resp = await client.get(f"/api/v1/sessions/{session_id}", headers=_auth(token_b))
    assert resp.status_code == 404
    assert resp.json()["error"]["code"] == "SESSION_NOT_FOUND"


@pytest.mark.asyncio
async def test_get_session_messages_scoped_and_ordered(client, db_session, _mock_title):
    token_a = await _register(client, "hist-a@example.com")
    token_b = await _register(client, "hist-b@example.com")
    book = await _make_book(db_session, file_hash="hash-hist")

    created = await client.post("/api/v1/sessions", json={"book_id": str(book.id)}, headers=_auth(token_a))
    session_id = created.json()["id"]

    await client.post(f"/api/v1/sessions/{session_id}/messages", json={"content": "What is a noun?"}, headers=_auth(token_a))
    await client.post(f"/api/v1/sessions/{session_id}/messages", json={"content": "Give an example."}, headers=_auth(token_a))

    resp = await client.get(f"/api/v1/sessions/{session_id}/messages", headers=_auth(token_a))
    assert resp.status_code == 200
    contents = [m["content"] for m in resp.json()]
    # 2 user turns + 2 assistant replies, in chronological order.
    assert contents == ["What is a noun?", "A noun is a naming word.", "Give an example.", "A noun is a naming word."]

    forbidden = await client.get(f"/api/v1/sessions/{session_id}/messages", headers=_auth(token_b))
    assert forbidden.status_code == 404


@pytest.mark.asyncio
async def test_rename_session(client, db_session):
    token_a = await _register(client, "rename-a@example.com")
    token_b = await _register(client, "rename-b@example.com")
    book = await _make_book(db_session, file_hash="hash-rename")

    created = await client.post("/api/v1/sessions", json={"book_id": str(book.id)}, headers=_auth(token_a))
    session_id = created.json()["id"]

    resp = await client.patch(f"/api/v1/sessions/{session_id}", json={"title": "My renamed chat"}, headers=_auth(token_a))
    assert resp.status_code == 200
    assert resp.json()["title"] == "My renamed chat"

    forbidden = await client.patch(f"/api/v1/sessions/{session_id}", json={"title": "Hijacked"}, headers=_auth(token_b))
    assert forbidden.status_code == 404

    empty_title = await client.patch(f"/api/v1/sessions/{session_id}", json={"title": ""}, headers=_auth(token_a))
    assert empty_title.status_code == 422


@pytest.mark.asyncio
async def test_delete_session_then_idempotent_404(client, db_session):
    token = await _register(client, "delete@example.com")
    book = await _make_book(db_session, file_hash="hash-delete")

    created = await client.post("/api/v1/sessions", json={"book_id": str(book.id)}, headers=_auth(token))
    session_id = created.json()["id"]

    delete = await client.delete(f"/api/v1/sessions/{session_id}", headers=_auth(token))
    assert delete.status_code == 204

    get_after = await client.get(f"/api/v1/sessions/{session_id}", headers=_auth(token))
    assert get_after.status_code == 404

    delete_again = await client.delete(f"/api/v1/sessions/{session_id}", headers=_auth(token))
    assert delete_again.status_code == 404

    listing = await client.get("/api/v1/sessions", headers=_auth(token))
    assert session_id not in [row["id"] for row in listing.json()]


@pytest.mark.asyncio
async def test_auto_title_set_once_and_not_overwritten(client, db_session, _mock_title):
    token = await _register(client, "title@example.com")
    book = await _make_book(db_session, file_hash="hash-title")

    created = await client.post("/api/v1/sessions", json={"book_id": str(book.id)}, headers=_auth(token))
    session_id = created.json()["id"]
    assert created.json()["title"] is None

    _mock_title["value"] = "Nouns And Naming Words"
    first = await client.post(f"/api/v1/sessions/{session_id}/messages", json={"content": "What is a noun?"}, headers=_auth(token))
    assert first.status_code == 201

    after_first = await client.get(f"/api/v1/sessions/{session_id}", headers=_auth(token))
    assert after_first.json()["title"] == "Nouns And Naming Words"

    # A second message must not re-title an already-titled session, even if
    # the (mocked) title generator would now return something different.
    _mock_title["value"] = "A Completely Different Title"
    await client.post(f"/api/v1/sessions/{session_id}/messages", json={"content": "Give an example."}, headers=_auth(token))

    after_second = await client.get(f"/api/v1/sessions/{session_id}", headers=_auth(token))
    assert after_second.json()["title"] == "Nouns And Naming Words"


@pytest.mark.asyncio
async def test_updated_at_bumps_on_each_message(client, db_session, _mock_title):
    token = await _register(client, "bump@example.com")
    book = await _make_book(db_session, file_hash="hash-bump")

    created = await client.post("/api/v1/sessions", json={"book_id": str(book.id)}, headers=_auth(token))
    session_id = created.json()["id"]
    created_updated_at = created.json()["updated_at"]

    await client.post(f"/api/v1/sessions/{session_id}/messages", json={"content": "What is a noun?"}, headers=_auth(token))
    after_first = await client.get(f"/api/v1/sessions/{session_id}", headers=_auth(token))
    assert after_first.json()["updated_at"] > created_updated_at

    await client.post(f"/api/v1/sessions/{session_id}/messages", json={"content": "Give an example."}, headers=_auth(token))
    after_second = await client.get(f"/api/v1/sessions/{session_id}", headers=_auth(token))
    assert after_second.json()["updated_at"] > after_first.json()["updated_at"]
