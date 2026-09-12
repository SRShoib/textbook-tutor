"""Tests for core/security.py's password hashing and JWT helpers. All pure
functions — no DB, no HTTP, no app. CLAUDE.md: test pipeline/ functions,
skip route tests; these aren't route tests, they're the unit-testable
helpers auth routes are built from."""

import uuid

import pytest

from app.core import security
from app.core.config import get_settings
from app.core.errors import AppError


# --- password hashing --------------------------------------------------


def test_hash_password_roundtrip():
    hashed = security.hash_password("correct horse battery staple")
    assert hashed != "correct horse battery staple"
    assert security.verify_password("correct horse battery staple", hashed)


def test_verify_password_rejects_wrong_password():
    hashed = security.hash_password("correct horse battery staple")
    assert not security.verify_password("wrong password", hashed)


# --- access tokens -------------------------------------------------------


def test_access_token_roundtrip():
    user_id = uuid.uuid4()
    token = security.create_access_token(user_id, "student")
    payload = security.decode_token(token, expected_type="access")
    assert payload.user_id == user_id
    assert payload.role == "student"
    assert payload.token_type == "access"


def test_access_token_expired():
    get_settings().access_token_minutes = -1  # already-expired token
    token = security.create_access_token(uuid.uuid4(), "student")
    with pytest.raises(AppError) as exc_info:
        security.decode_token(token, expected_type="access")
    assert exc_info.value.code == "TOKEN_EXPIRED"


def test_refresh_token_rejected_as_access_token():
    token, _, _ = security.create_refresh_token(uuid.uuid4())
    with pytest.raises(AppError) as exc_info:
        security.decode_token(token, expected_type="access")
    assert exc_info.value.code == "TOKEN_INVALID"


def test_access_token_rejected_as_refresh_token():
    token = security.create_access_token(uuid.uuid4(), "student")
    with pytest.raises(AppError) as exc_info:
        security.decode_token(token, expected_type="refresh")
    assert exc_info.value.code == "TOKEN_INVALID"


def test_tampered_signature_rejected():
    token = security.create_access_token(uuid.uuid4(), "student")
    tampered = token[:-1] + ("A" if token[-1] != "A" else "B")
    with pytest.raises(AppError) as exc_info:
        security.decode_token(tampered, expected_type="access")
    assert exc_info.value.code == "TOKEN_INVALID"


def test_garbage_token_rejected():
    with pytest.raises(AppError) as exc_info:
        security.decode_token("not-a-jwt-at-all", expected_type="access")
    assert exc_info.value.code == "TOKEN_INVALID"


# --- refresh tokens -------------------------------------------------------


def test_refresh_token_hash_matches_hash_refresh_token():
    token, token_hash, _ = security.create_refresh_token(uuid.uuid4())
    assert security.hash_refresh_token(token) == token_hash


def test_refresh_token_has_unique_jti():
    user_id = uuid.uuid4()
    token_a, _, _ = security.create_refresh_token(user_id)
    token_b, _, _ = security.create_refresh_token(user_id)
    payload_a = security.decode_token(token_a, expected_type="refresh")
    payload_b = security.decode_token(token_b, expected_type="refresh")
    assert payload_a.jti != payload_b.jti


# --- the "change-me" secret guard -----------------------------------------


def test_refuses_to_sign_with_default_secret_when_auth_required():
    settings = get_settings()
    settings.jwt_secret = "change-me"
    settings.allow_anonymous = False
    with pytest.raises(RuntimeError):
        security.create_access_token(uuid.uuid4(), "student")


def test_default_secret_allowed_when_anonymous_allowed():
    settings = get_settings()
    settings.jwt_secret = "change-me"
    settings.allow_anonymous = True
    # Should not raise — local dev never signs a token through the normal
    # ALLOW_ANONYMOUS path, but nothing here should block it either.
    security.create_access_token(uuid.uuid4(), "student")
