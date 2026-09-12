"""Route tests for api/auth.py — a documented exception to CLAUDE.md's
"skip route tests unless asked" rule: this session was asked for them,
because token expiry, cookie handling and 401 paths are exactly where auth
bugs hide and no pipeline-level unit test can reach them.

Needs docker-compose's Postgres running and migrated (`alembic upgrade
head`) — every test here is marked "db"; `pytest -m "not db"` skips this
file entirely and leaves the existing pipeline suite untouched."""

import pytest

from app.core import rate_limit
from app.core.config import get_settings

pytestmark = pytest.mark.db


@pytest.fixture(autouse=True)
def _reset_rate_limiter():
    rate_limit.reset_all()
    yield
    rate_limit.reset_all()


def _register_body(email="ayesha@example.com", password="hunter22", display_name="Ayesha", grade=5) -> dict:
    return {"email": email, "password": password, "display_name": display_name, "grade": grade}


@pytest.mark.asyncio
async def test_register_then_me_with_access_token(client):
    resp = await client.post("/api/v1/auth/register", json=_register_body())
    assert resp.status_code == 201
    body = resp.json()
    assert body["user"]["email"] == "ayesha@example.com"
    assert body["user"]["grade"] == 5
    assert "refresh_token" in resp.cookies

    me = await client.get("/api/v1/auth/me", headers={"Authorization": f"Bearer {body['access_token']}"})
    assert me.status_code == 200
    assert me.json()["email"] == "ayesha@example.com"


@pytest.mark.asyncio
async def test_register_duplicate_email_rejected(client):
    await client.post("/api/v1/auth/register", json=_register_body(email="dup@example.com"))
    resp = await client.post("/api/v1/auth/register", json=_register_body(email="dup@example.com"))
    assert resp.status_code == 409
    assert resp.json()["error"]["code"] == "EMAIL_TAKEN"


@pytest.mark.asyncio
async def test_login_wrong_password_and_unknown_email_are_indistinguishable(client):
    await client.post("/api/v1/auth/register", json=_register_body(email="bina@example.com"))

    wrong_password = await client.post(
        "/api/v1/auth/login", json={"email": "bina@example.com", "password": "wrong-password"}
    )
    unknown_email = await client.post(
        "/api/v1/auth/login", json={"email": "nobody@example.com", "password": "whatever123"}
    )

    assert wrong_password.status_code == 401
    assert unknown_email.status_code == 401
    assert wrong_password.json() == unknown_email.json()


@pytest.mark.asyncio
async def test_login_success_returns_token_and_sets_cookie(client):
    await client.post("/api/v1/auth/register", json=_register_body(email="chumki@example.com"))
    resp = await client.post("/api/v1/auth/login", json={"email": "chumki@example.com", "password": "hunter22"})
    assert resp.status_code == 200
    assert resp.json()["user"]["email"] == "chumki@example.com"
    assert "refresh_token" in resp.cookies


@pytest.mark.asyncio
async def test_login_rate_limited_after_repeated_failures(client):
    await client.post("/api/v1/auth/register", json=_register_body(email="dipa@example.com"))
    get_settings().login_rate_limit_attempts = 3

    for _ in range(3):
        resp = await client.post(
            "/api/v1/auth/login", json={"email": "dipa@example.com", "password": "wrong-password"}
        )
        assert resp.status_code == 401

    blocked = await client.post(
        "/api/v1/auth/login", json={"email": "dipa@example.com", "password": "wrong-password"}
    )
    assert blocked.status_code == 429
    assert blocked.json()["error"]["code"] == "RATE_LIMITED"


@pytest.mark.asyncio
async def test_successful_login_clears_rate_limit(client):
    await client.post("/api/v1/auth/register", json=_register_body(email="ejaz@example.com"))
    get_settings().login_rate_limit_attempts = 3

    for _ in range(2):
        await client.post("/api/v1/auth/login", json={"email": "ejaz@example.com", "password": "wrong-password"})

    ok = await client.post("/api/v1/auth/login", json={"email": "ejaz@example.com", "password": "hunter22"})
    assert ok.status_code == 200

    # A fresh bad attempt right after a clean login should be strike 1, not
    # strike 3 — proving the earlier failures were actually cleared.
    still_unblocked = await client.post(
        "/api/v1/auth/login", json={"email": "ejaz@example.com", "password": "wrong-password"}
    )
    assert still_unblocked.status_code == 401


@pytest.mark.asyncio
async def test_refresh_rotates_and_old_token_then_fails(client):
    register = await client.post("/api/v1/auth/register", json=_register_body(email="farida@example.com"))
    first_refresh = register.cookies["refresh_token"]

    # httpx's AsyncClient stores each response's Set-Cookie into client.cookies
    # automatically, so this call already carries first_refresh.
    rotated = await client.post("/api/v1/auth/refresh")
    assert rotated.status_code == 200
    second_refresh = rotated.cookies["refresh_token"]
    assert second_refresh != first_refresh

    # Force the jar back to the now-rotated-away token to prove it is dead.
    client.cookies.set("refresh_token", first_refresh)
    replayed = await client.post("/api/v1/auth/refresh")
    assert replayed.status_code == 401
    assert replayed.json()["error"]["code"] == "REFRESH_TOKEN_REUSED"


@pytest.mark.asyncio
async def test_refresh_token_reuse_revokes_the_whole_family(client):
    register = await client.post("/api/v1/auth/register", json=_register_body(email="golap@example.com"))
    first_refresh = register.cookies["refresh_token"]

    rotated = await client.post("/api/v1/auth/refresh")
    second_refresh = rotated.cookies["refresh_token"]

    # Replaying the already-rotated first token triggers reuse detection...
    client.cookies.set("refresh_token", first_refresh)
    await client.post("/api/v1/auth/refresh")

    # ...which must also have revoked the legitimate, still-unused second token.
    client.cookies.set("refresh_token", second_refresh)
    resp = await client.post("/api/v1/auth/refresh")
    assert resp.status_code == 401
    assert resp.json()["error"]["code"] == "REFRESH_TOKEN_REUSED"


@pytest.mark.asyncio
async def test_logout_then_refresh_fails(client):
    register = await client.post("/api/v1/auth/register", json=_register_body(email="himel@example.com"))
    refresh_token = register.cookies["refresh_token"]

    logout = await client.post("/api/v1/auth/logout")
    assert logout.status_code == 204

    # logout's response clears the cookie from the jar; set it back to the
    # now-revoked token to prove the server side rejects it.
    client.cookies.set("refresh_token", refresh_token)
    resp = await client.post("/api/v1/auth/refresh")
    assert resp.status_code == 401
    assert resp.json()["error"]["code"] == "REFRESH_TOKEN_REUSED"


@pytest.mark.asyncio
async def test_logout_without_cookie_is_a_no_op(client):
    resp = await client.post("/api/v1/auth/logout")
    assert resp.status_code == 204


@pytest.mark.asyncio
async def test_refresh_without_cookie_is_rejected(client):
    resp = await client.post("/api/v1/auth/refresh")
    assert resp.status_code == 401
    assert resp.json()["error"]["code"] == "REFRESH_TOKEN_MISSING"


@pytest.mark.asyncio
async def test_me_falls_back_to_dev_user_when_anonymous_allowed(client):
    get_settings().allow_anonymous = True
    resp = await client.get("/api/v1/auth/me")
    assert resp.status_code == 200
    assert resp.json()["email"] == "dev@textbook-tutor.local"


@pytest.mark.asyncio
async def test_me_rejects_garbage_bearer_even_when_anonymous_allowed(client):
    get_settings().allow_anonymous = True
    resp = await client.get("/api/v1/auth/me", headers={"Authorization": "Bearer not-a-real-token"})
    assert resp.status_code == 401
    assert resp.json()["error"]["code"] == "TOKEN_INVALID"


@pytest.mark.asyncio
async def test_me_requires_login_when_anonymous_disallowed(client):
    get_settings().allow_anonymous = False
    resp = await client.get("/api/v1/auth/me")
    assert resp.status_code == 401
    assert resp.json()["error"]["code"] == "AUTH_REQUIRED"


@pytest.mark.asyncio
async def test_new_session_defaults_grade_to_user_grade(client, db_session):
    """The other half of CLAUDE.md's "on register, grade becomes the
    default for new sessions" — api/sessions.py's create_session falls back
    to user.grade when the request omits one."""
    from app.models.book import Book, BookStatus

    register = await client.post("/api/v1/auth/register", json=_register_body(email="ivy@example.com", grade=7))
    access_token = register.json()["access_token"]

    book = Book(title="Test Book", grade=7, status=BookStatus.READY, chunk_count=1, file_hash="test-hash-ivy")
    db_session.add(book)
    await db_session.commit()
    await db_session.refresh(book)

    resp = await client.post(
        "/api/v1/sessions",
        json={"book_id": str(book.id)},
        headers={"Authorization": f"Bearer {access_token}"},
    )
    assert resp.status_code == 201
    assert resp.json()["grade"] == 7
