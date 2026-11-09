"""Idempotency persistence for retryable public authentication writes."""

from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from typing import Any, Callable
from uuid import UUID, uuid4

from sqlalchemy import text
from sqlalchemy.orm import Session

from app.api.errors import ApiError

IDEMPOTENCY_RETENTION = timedelta(hours=24)


@dataclass(frozen=True)
class IdempotencyResult:
    status_code: int
    body: dict[str, Any]
    headers: dict[str, str]


def _now() -> datetime:
    return datetime.now(timezone.utc)


def validate_idempotency_key(key: str | None) -> str:
    if key is None or not (16 <= len(key) <= 128):
        raise ApiError(
            422,
            "validation_error",
            "A valid Idempotency-Key header is required.",
            field_errors=[
                {
                    "field": "Idempotency-Key",
                    "code": "invalid_idempotency_key",
                    "message": "Use 16-128 printable ASCII characters.",
                }
            ],
        )
    if not all(32 <= ord(character) <= 126 for character in key):
        raise ApiError(
            422,
            "validation_error",
            "A valid Idempotency-Key header is required.",
            field_errors=[
                {
                    "field": "Idempotency-Key",
                    "code": "invalid_idempotency_key",
                    "message": "Use 16-128 printable ASCII characters.",
                }
            ],
        )
    return key


def _hash(value: str) -> str:
    return hashlib.sha256(value.encode("utf-8")).hexdigest()


def _request_hash(payload: dict[str, Any] | None) -> str:
    canonical = json.dumps(
        payload or {},
        sort_keys=True,
        separators=(",", ":"),
        ensure_ascii=True,
    )
    return _hash(canonical)


def _advisory_lock_key(
    *,
    scope_hash: str,
    method: str,
    route: str,
    key_hash: str,
) -> int:
    digest = hashlib.sha256(
        f"{scope_hash}|{method}|{route}|{key_hash}".encode("utf-8")
    ).digest()
    return int.from_bytes(digest[:8], "big", signed=True)


def run_idempotent(
    session: Session,
    *,
    scope: str,
    method: str,
    route: str,
    key: str,
    payload: dict[str, Any] | None,
    owner_user_id: UUID | None,
    operation: Callable[[], IdempotencyResult],
) -> tuple[IdempotencyResult, bool]:
    """Run ``operation`` once per scoped idempotency key.

    Returns ``(result, replayed)``. The caller owns the surrounding transaction;
    a failure in ``operation`` rolls back the in-progress record as well.
    """

    validate_idempotency_key(key)
    scope_hash = _hash(scope)
    key_hash = _hash(key)
    request_hash = _request_hash(payload)
    lock_key = _advisory_lock_key(
        scope_hash=scope_hash,
        method=method,
        route=route,
        key_hash=key_hash,
    )

    session.execute(
        text("SELECT pg_advisory_xact_lock(:lock_key)"),
        {"lock_key": lock_key},
    )

    existing = session.execute(
        text(
            """
            SELECT request_hash, state, response_status, response_body,
                   response_headers, expires_at
            FROM idempotency_records
            WHERE principal_scope_hash = :scope_hash
              AND http_method = :method
              AND route_key = :route
              AND key_hash = :key_hash
            """
        ),
        {
            "scope_hash": scope_hash,
            "method": method,
            "route": route,
            "key_hash": key_hash,
        },
    ).mappings().first()

    if existing is not None:
        if existing["expires_at"] <= _now():
            raise ApiError(
                409,
                "idempotency_key_expired",
                "This idempotency key has expired.",
            )
        if existing["request_hash"] != request_hash:
            raise ApiError(
                409,
                "idempotency_key_reused",
                "This idempotency key was used with a different request.",
            )
        if existing["state"] != "completed":
            raise ApiError(
                409,
                "idempotency_key_in_progress",
                "This request is already being processed.",
            )
        return (
            IdempotencyResult(
                status_code=int(existing["response_status"]),
                body=dict(existing["response_body"]),
                headers=dict(existing["response_headers"] or {}),
            ),
            True,
        )

    session.execute(
        text(
            """
            INSERT INTO idempotency_records (
                id, owner_user_id, principal_scope_hash, http_method,
                route_key, key_hash, request_hash, expires_at
            ) VALUES (
                :id, :owner_user_id, :scope_hash, :method,
                :route, :key_hash, :request_hash, :expires_at
            )
            """
        ),
        {
            "id": uuid4(),
            "owner_user_id": owner_user_id,
            "scope_hash": scope_hash,
            "method": method,
            "route": route,
            "key_hash": key_hash,
            "request_hash": request_hash,
            "expires_at": _now() + IDEMPOTENCY_RETENTION,
        },
    )

    result = operation()

    session.execute(
        text(
            """
            UPDATE idempotency_records
            SET state = 'completed',
                response_status = :status,
                response_body = CAST(:body AS jsonb),
                response_headers = CAST(:headers AS jsonb),
                completed_at = :completed_at
            WHERE principal_scope_hash = :scope_hash
              AND http_method = :method
              AND route_key = :route
              AND key_hash = :key_hash
            """
        ),
        {
            "status": result.status_code,
            "body": json.dumps(result.body),
            "headers": json.dumps(result.headers),
            "completed_at": _now(),
            "scope_hash": scope_hash,
            "method": method,
            "route": route,
            "key_hash": key_hash,
        },
    )

    return result, False
