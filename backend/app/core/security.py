"""Password, opaque-token, and JWT helpers for the authentication foundation."""

from __future__ import annotations

import hashlib
import hmac
import secrets
from datetime import datetime, timedelta, timezone
from typing import Any
from uuid import UUID

import jwt
from argon2 import PasswordHasher
from argon2.exceptions import (
    InvalidHashError,
    VerificationError,
    VerifyMismatchError,
)

from app.core.config import settings

_PASSWORD_HASHER = PasswordHasher(
    time_cost=3,
    memory_cost=65536,
    parallelism=4,
    hash_len=32,
    salt_len=16,
)

# A valid Argon2id hash used to keep failed-login timing roughly constant when
# the supplied email does not exist. It is not a credential for any account.
_DUMMY_PASSWORD_HASH = _PASSWORD_HASHER.hash(
    "finpath-dummy-password-not-a-real-credential"
)


class TokenConfigurationError(RuntimeError):
    pass


class InvalidTokenError(Exception):
    pass


def hash_password(password: str) -> str:
    return _PASSWORD_HASHER.hash(password)


def verify_password(password_hash: str, password: str) -> bool:
    try:
        return _PASSWORD_HASHER.verify(password_hash, password)
    except (VerifyMismatchError, VerificationError, InvalidHashError):
        return False


def verify_dummy_password(password: str) -> None:
    """Burn comparable time for an unknown account without revealing it."""
    verify_password(_DUMMY_PASSWORD_HASH, password)


def password_needs_rehash(password_hash: str) -> bool:
    try:
        return _PASSWORD_HASHER.check_needs_rehash(password_hash)
    except InvalidHashError:
        return True


def generate_opaque_token() -> str:
    return secrets.token_urlsafe(48)


def hash_opaque_token(raw_token: str) -> str:
    return hashlib.sha256(raw_token.encode("utf-8")).hexdigest()


def fingerprint_secret(value: str) -> str:
    """Return a keyed digest suitable for idempotency request fingerprints.

    The digest lets a retry detect that a password-bearing payload changed
    without storing the password or an unkeyed password hash.
    """

    return hmac.new(
        _secret().encode("utf-8"),
        value.encode("utf-8"),
        hashlib.sha256,
    ).hexdigest()


def _secret() -> str:
    secret = settings.access_token_secret.get_secret_value()
    if not secret:
        raise TokenConfigurationError(
            "FINPATH_ACCESS_TOKEN_SECRET must be configured."
        )
    return secret


def _now() -> datetime:
    return datetime.now(timezone.utc)


def create_access_token(*, user_id: UUID, session_id: UUID) -> str:
    issued_at = _now()
    payload = {
        "sub": str(user_id),
        "sid": str(session_id),
        "typ": "access",
        "iss": settings.access_token_issuer,
        "aud": settings.access_token_audience,
        "iat": issued_at,
        "exp": issued_at
        + timedelta(seconds=settings.access_token_ttl_seconds),
    }
    return jwt.encode(payload, _secret(), algorithm="HS256")


def decode_access_token(token: str) -> dict[str, Any]:
    try:
        payload = jwt.decode(
            token,
            _secret(),
            algorithms=["HS256"],
            issuer=settings.access_token_issuer,
            audience=settings.access_token_audience,
            options={"require": ["exp", "iat", "iss", "aud", "sub", "sid"]},
        )
    except jwt.PyJWTError as exc:
        raise InvalidTokenError from exc
    if payload.get("typ") != "access":
        raise InvalidTokenError
    return payload


def create_csrf_token(*, session_family_id: UUID) -> str:
    issued_at = _now()
    payload = {
        "sub": str(session_family_id),
        "typ": "csrf",
        "iss": settings.access_token_issuer,
        "aud": settings.csrf_token_audience,
        "iat": issued_at,
        "exp": issued_at + timedelta(seconds=settings.csrf_token_ttl_seconds),
    }
    return jwt.encode(payload, _secret(), algorithm="HS256")


def verify_csrf_token(token: str, *, session_family_id: UUID) -> bool:
    try:
        payload = jwt.decode(
            token,
            _secret(),
            algorithms=["HS256"],
            issuer=settings.access_token_issuer,
            audience=settings.csrf_token_audience,
            options={"require": ["exp", "iat", "iss", "aud", "sub"]},
        )
    except jwt.PyJWTError:
        return False
    return (
        payload.get("typ") == "csrf"
        and payload.get("sub") == str(session_family_id)
    )


def access_token_expires_in() -> int:
    return settings.access_token_ttl_seconds
