"""Route tests for api/auth.py's forgot-password / reset-password pair --
same documented exception to CLAUDE.md's "skip route tests unless asked"
rule as test_auth_routes.py: token expiry, reuse, and the anti-enumeration
response shape are exactly where these bugs hide.

Needs docker-compose's Postgres running and migrated (`alembic upgrade
head`) — every test here is marked "db"; `pytest -m "not db"` skips this
file entirely."""

import pytest

from app.core import rate_limit
from app.core.config import get_settings

pytestmark = pytest.mark.db


@pytest.fixture(autouse=True)
def _reset_rate_limiter():
    rate_limit.reset_all()
    yield
    rate_limit.reset_all()


@pytest.fixture
def captured_link(monkeypatch):
    """Captures the reset link forgot-password would have emailed, instead
    of depending on the console/smtp backend -- same style
    test_sessions_routes.py already uses for mocking a side-effecting call."""
    state: dict = {}

    def fake_send(to_email: str, reset_link: str) -> None:
        state["to_email"] = to_email
        state["reset_link"] = reset_link

    monkeypatch.setattr("app.api.auth.send_password_reset_email", fake_send)
    return state


def _register_body(email="reset-target@example.com", password="hunter22", display_name="Test", grade=5) -> dict:
    return {"email": email, "password": password, "display_name": display_name, "grade": grade}


def _extract_token(reset_link: str) -> str:
    return reset_link.split("token=", 1)[1]


@pytest.mark.asyncio
async def test_forgot_password_known_and_unknown_email_get_identical_response(client, captured_link):
    await client.post("/api/v1/auth/register", json=_register_body())

    known = await client.post("/api/v1/auth/forgot-password", json={"email": "reset-target@example.com"})
    assert "reset_link" in captured_link  # confirms the known-email path actually ran

    captured_link.clear()
    unknown = await client.post("/api/v1/auth/forgot-password", json={"email": "nobody@example.com"})

    assert known.status_code == 200
    assert unknown.status_code == 200
    assert known.json() == unknown.json()
    assert "reset_link" not in captured_link  # confirms the unknown-email path never emails anything


@pytest.mark.asyncio
async def test_forgot_password_rate_limited_after_repeated_requests(client, captured_link):
    get_settings().login_rate_limit_attempts = 3
    await client.post("/api/v1/auth/register", json=_register_body(email="rate@example.com"))

    for _ in range(3):
        resp = await client.post("/api/v1/auth/forgot-password", json={"email": "rate@example.com"})
        assert resp.status_code == 200

    blocked = await client.post("/api/v1/auth/forgot-password", json={"email": "rate@example.com"})
    assert blocked.status_code == 429
    assert blocked.json()["error"]["code"] == "RATE_LIMITED"


@pytest.mark.asyncio
async def test_reset_password_with_valid_token_changes_password(client, captured_link):
    await client.post("/api/v1/auth/register", json=_register_body(email="valid-reset@example.com"))
    await client.post("/api/v1/auth/forgot-password", json={"email": "valid-reset@example.com"})
    token = _extract_token(captured_link["reset_link"])

    resp = await client.post(
        "/api/v1/auth/reset-password", json={"token": token, "new_password": "newpassword123"}
    )
    assert resp.status_code == 204

    old_login = await client.post(
        "/api/v1/auth/login", json={"email": "valid-reset@example.com", "password": "hunter22"}
    )
    assert old_login.status_code == 401

    new_login = await client.post(
        "/api/v1/auth/login", json={"email": "valid-reset@example.com", "password": "newpassword123"}
    )
    assert new_login.status_code == 200


@pytest.mark.asyncio
async def test_reset_password_token_cannot_be_used_twice(client, captured_link):
    await client.post("/api/v1/auth/register", json=_register_body(email="reuse@example.com"))
    await client.post("/api/v1/auth/forgot-password", json={"email": "reuse@example.com"})
    token = _extract_token(captured_link["reset_link"])

    first = await client.post("/api/v1/auth/reset-password", json={"token": token, "new_password": "newpassword123"})
    assert first.status_code == 204

    second = await client.post("/api/v1/auth/reset-password", json={"token": token, "new_password": "anotherpass123"})
    assert second.status_code == 401
    assert second.json()["error"]["code"] == "TOKEN_INVALID"


@pytest.mark.asyncio
async def test_reset_password_rejects_garbage_token(client):
    resp = await client.post(
        "/api/v1/auth/reset-password", json={"token": "not-a-real-token", "new_password": "newpassword123"}
    )
    assert resp.status_code == 401


@pytest.mark.asyncio
async def test_reset_password_revokes_existing_refresh_tokens(client, captured_link):
    register = await client.post("/api/v1/auth/register", json=_register_body(email="revoke@example.com"))
    old_refresh_cookie = register.cookies["refresh_token"]

    await client.post("/api/v1/auth/forgot-password", json={"email": "revoke@example.com"})
    token = _extract_token(captured_link["reset_link"])
    reset = await client.post("/api/v1/auth/reset-password", json={"token": token, "new_password": "newpassword123"})
    assert reset.status_code == 204

    client.cookies.set("refresh_token", old_refresh_cookie)
    refresh_attempt = await client.post("/api/v1/auth/refresh")
    assert refresh_attempt.status_code == 401
