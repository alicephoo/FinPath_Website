from __future__ import annotations

from uuid import uuid4

import pytest
from pydantic import SecretStr

from app.core import security
from app.core.config import settings


@pytest.fixture(autouse=True)
def configured_secret(monkeypatch: pytest.MonkeyPatch):
    monkeypatch.setattr(
        settings,
        "access_token_secret",
        SecretStr("test-secret-value-with-enough-entropy"),
    )
    monkeypatch.setattr(settings, "access_token_ttl_seconds", 900)


def test_passwords_use_argon2id_and_verify_safely() -> None:
    password = "correct horse battery staple"
    password_hash = security.hash_password(password)

    assert password_hash.startswith("$argon2id$")
    assert password not in password_hash
    assert security.verify_password(password_hash, password) is True
    assert security.verify_password(password_hash, "wrong password") is False
    assert security.verify_password("not-a-hash", password) is False


def test_opaque_tokens_are_random_and_hashed() -> None:
    first = security.generate_opaque_token()
    second = security.generate_opaque_token()

    assert first != second
    assert len(first) >= 64
    assert security.hash_opaque_token(first) != first
    assert security.hash_opaque_token(first) == security.hash_opaque_token(first)


def test_access_token_round_trip_and_claims() -> None:
    user_id = uuid4()
    session_id = uuid4()
    token = security.create_access_token(
        user_id=user_id,
        session_id=session_id,
    )
    payload = security.decode_access_token(token)

    assert payload["sub"] == str(user_id)
    assert payload["sid"] == str(session_id)
    assert payload["typ"] == "access"
    assert payload["iss"] == settings.access_token_issuer
    assert payload["aud"] == settings.access_token_audience


def test_expired_or_tampered_access_tokens_are_rejected(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    user_id = uuid4()
    session_id = uuid4()

    monkeypatch.setattr(settings, "access_token_ttl_seconds", -1)
    expired = security.create_access_token(
        user_id=user_id,
        session_id=session_id,
    )
    with pytest.raises(security.InvalidTokenError):
        security.decode_access_token(expired)

    monkeypatch.setattr(settings, "access_token_ttl_seconds", 900)
    valid = security.create_access_token(
        user_id=user_id,
        session_id=session_id,
    )
    with pytest.raises(security.InvalidTokenError):
        security.decode_access_token(valid + "tampered")


def test_access_token_with_wrong_secret_is_rejected(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    user_id = uuid4()
    session_id = uuid4()
    token = security.create_access_token(
        user_id=user_id,
        session_id=session_id,
    )
    monkeypatch.setattr(
        settings,
        "access_token_secret",
        SecretStr("a-different-secret-value-with-entropy"),
    )
    with pytest.raises(security.InvalidTokenError):
        security.decode_access_token(token)


def test_csrf_token_is_bound_to_the_session_family() -> None:
    family_id = uuid4()
    other_family_id = uuid4()
    token = security.create_csrf_token(session_family_id=family_id)

    assert security.verify_csrf_token(token, session_family_id=family_id) is True
    assert (
        security.verify_csrf_token(token, session_family_id=other_family_id)
        is False
    )
    assert security.verify_csrf_token("not-a-token", session_family_id=family_id) is False
