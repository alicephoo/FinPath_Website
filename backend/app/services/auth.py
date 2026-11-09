"""Authentication service layer.

The normal runtime identity (``finpath_app``) handles login, verification,
sessions, and password changes. Registration writes use the separate
least-privilege ``finpath_registration`` identity through a dedicated session.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from typing import Any, Mapping
from uuid import UUID, uuid4

from sqlalchemy import text
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app.api.errors import ApiError
from app.core import security
from app.core.config import settings
from app.services.email import EmailDeliveryError, EmailMessage, EmailSender
from app.services.idempotency import IdempotencyResult, run_idempotent

logger = logging.getLogger(__name__)

DEFAULT_TIMEZONE = "Asia/Yangon"
USER_ROLE_ID = 1


@dataclass(frozen=True)
class AuthContext:
    user_id: UUID
    session_id: UUID
    session_family_id: UUID
    email: str
    status: str
    timezone: str
    revision: int
    email_verified_at: datetime | None


@dataclass(frozen=True)
class LoginResult:
    body: dict[str, Any]
    refresh_token: str
    refresh_expires_at: datetime
    csrf_token: str


def _now() -> datetime:
    return datetime.now(timezone.utc)


def _etag(revision: int) -> str:
    return f'"{revision}"'


def normalize_email(email: str) -> str:
    return email.strip().lower()


def _user_by_email(session: Session, email: str) -> Mapping[str, Any] | None:
    return session.execute(
        text(
            """
            SELECT id, email, password_hash, email_verified_at, pending_email,
                   status, timezone, revision, created_at, updated_at
            FROM users
            WHERE lower(email) = lower(:email)
            """
        ),
        {"email": email},
    ).mappings().first()


def _user_by_id(session: Session, user_id: UUID) -> Mapping[str, Any] | None:
    return session.execute(
        text(
            """
            SELECT id, email, password_hash, email_verified_at, pending_email,
                   status, timezone, revision, created_at, updated_at
            FROM users
            WHERE id = :user_id
            """
        ),
        {"user_id": user_id},
    ).mappings().first()


def _roles_for_user(session: Session, user_id: UUID) -> list[str]:
    rows = session.execute(
        text(
            """
            SELECT role.name
            FROM user_roles AS user_role
            JOIN roles AS role ON role.id = user_role.role_id
            WHERE user_role.user_id = :user_id
            ORDER BY role.id
            """
        ),
        {"user_id": user_id},
    ).scalars()
    return list(rows)


def _user_summary(
    user: Mapping[str, Any],
    roles: list[str],
) -> dict[str, Any]:
    return {
        "id": str(user["id"]),
        "email": user["email"],
        "roles": roles,
        "status": user["status"],
        "email_verified": user["email_verified_at"] is not None,
        "timezone": user["timezone"],
    }


def _raise_account_state(status: str) -> None:
    if status == "suspended":
        raise ApiError(
            403,
            "account_suspended",
            "This account cannot access the application. "
            "Contact support for assistance.",
        )
    if status == "closed":
        raise ApiError(
            403,
            "account_closed",
            "This account cannot access the application. "
            "Contact support for assistance.",
        )


def _revoke_user_sessions(session: Session, user_id: UUID) -> None:
    session.execute(
        text(
            """
            UPDATE auth_sessions
            SET revoked_at = :now
            WHERE user_id = :user_id AND revoked_at IS NULL
            """
        ),
        {"now": _now(), "user_id": user_id},
    )


def _insert_action_token(
    session: Session,
    *,
    user_id: UUID,
    token_type: str,
    ttl_seconds: int,
    target_email: str | None = None,
) -> str:
    raw_token = security.generate_opaque_token()
    session.execute(
        text(
            """
            INSERT INTO auth_action_tokens (
                id, user_id, token_type, token_hash, target_email, expires_at
            ) VALUES (
                :id, :user_id, :token_type, :token_hash, :target_email,
                :expires_at
            )
            """
        ),
        {
            "id": uuid4(),
            "user_id": user_id,
            "token_type": token_type,
            "token_hash": security.hash_opaque_token(raw_token),
            "target_email": target_email,
            "expires_at": _now() + timedelta(seconds=ttl_seconds),
        },
    )
    return raw_token


def _send_verification_email(
    sender: EmailSender,
    *,
    email: str,
    token: str,
) -> None:
    try:
        sender.send(
            EmailMessage(
                to=email,
                subject="Verify your FinPath email address",
                text=(
                    "Use this single-use token to verify your FinPath email "
                    f"address. It expires in 24 hours: {token}"
                ),
                token=token,
                purpose="email_verification",
            )
        )
    except EmailDeliveryError:
        logger.warning(
            "Verification email delivery failed; the account remains "
            "unverified and the user can request another message.",
            extra={"event": "email_delivery_failed", "purpose": "email_verification"},
        )


def _send_password_reset_email(
    sender: EmailSender,
    *,
    email: str,
    token: str,
) -> bool:
    try:
        sender.send(
            EmailMessage(
                to=email,
                subject="Reset your FinPath password",
                text=(
                    "Use this single-use token to reset your FinPath password. "
                    f"It expires in 30 minutes: {token}"
                ),
                token=token,
                purpose="password_reset",
            )
        )
        return True
    except EmailDeliveryError:
        logger.warning(
            "Password-reset email delivery failed; no reset token was stored.",
            extra={"event": "email_delivery_failed", "purpose": "password_reset"},
        )
        return False


def _insert_registration_user(
    session: Session,
    *,
    user_id: UUID,
    email: str,
    password_hash: str,
    email_verified_at: datetime | None,
) -> None:
    session.execute(
        text(
            """
            INSERT INTO users (
                id, email, password_hash, timezone, email_verified_at
            ) VALUES (
                :id, :email, :password_hash, :timezone, :email_verified_at
            )
            """
        ),
        {
            "id": user_id,
            "email": email,
            "password_hash": password_hash,
            "timezone": DEFAULT_TIMEZONE,
            "email_verified_at": email_verified_at,
        },
    )


def _insert_registration_user_role(
    session: Session,
    *,
    user_id: UUID,
) -> None:
    session.execute(
        text(
            """
            INSERT INTO user_roles (user_id, role_id)
            VALUES (:user_id, :role_id)
            """
        ),
        {"user_id": user_id, "role_id": USER_ROLE_ID},
    )


def _insert_financial_settings(
    session: Session,
    *,
    user_id: UUID,
) -> None:
    session.execute(
        text(
            """
            INSERT INTO user_financial_settings (user_id)
            VALUES (:user_id)
            """
        ),
        {"user_id": user_id},
    )


def _insert_verification_token(
    session: Session,
    *,
    user_id: UUID,
    token_hash: str,
    expires_at: datetime,
) -> None:
    session.execute(
        text(
            """
            INSERT INTO auth_action_tokens (
                id, user_id, token_type, token_hash, expires_at
            ) VALUES (
                :id, :user_id, 'email_verification', :token_hash, :expires_at
            )
            """
        ),
        {
            "id": uuid4(),
            "user_id": user_id,
            "token_hash": token_hash,
            "expires_at": expires_at,
        },
    )


def _registration_rows(
    session: Session,
    *,
    user_id: UUID,
    email: str,
    password_hash: str,
    email_verified_at: datetime | None,
    verification_token_hash: str | None,
    verification_expires_at: datetime | None,
) -> None:
    _insert_registration_user(
        session,
        user_id=user_id,
        email=email,
        password_hash=password_hash,
        email_verified_at=email_verified_at,
    )
    _insert_registration_user_role(session, user_id=user_id)
    _insert_financial_settings(session, user_id=user_id)
    if verification_token_hash is not None and verification_expires_at is not None:
        _insert_verification_token(
            session,
            user_id=user_id,
            token_hash=verification_token_hash,
            expires_at=verification_expires_at,
        )


def register_user(
    session: Session,
    *,
    email: str,
    password: str,
    idempotency_key: str,
    email_sender: EmailSender,
) -> IdempotencyResult:
    normalized_email = normalize_email(email)
    user_id = uuid4()
    verification_required = settings.require_email_verification
    email_verified_at = None if verification_required else _now()
    raw_verification_token = (
        security.generate_opaque_token() if verification_required else None
    )
    verification_expires_at = (
        _now() + timedelta(seconds=settings.email_verification_ttl_seconds)
        if verification_required
        else None
    )
    response_body = {
        "data": {
            "id": str(user_id),
            "email": normalized_email,
            "roles": ["user"],
            "status": "active",
            "verification_status": (
                "required" if verification_required else "not_required"
            ),
        }
    }
    response_headers = {"ETag": _etag(1)}

    def operation() -> IdempotencyResult:
        _registration_rows(
            session,
            user_id=user_id,
            email=normalized_email,
            password_hash=security.hash_password(password),
            email_verified_at=email_verified_at,
            verification_token_hash=(
                security.hash_opaque_token(raw_verification_token)
                if raw_verification_token is not None
                else None
            ),
            verification_expires_at=verification_expires_at,
        )
        return IdempotencyResult(
            status_code=201,
            body=response_body,
            headers=response_headers,
        )

    try:
        result, replayed = run_idempotent(
            session,
            scope="public:registration",
            method="POST",
            route="/api/v1/auth/register",
            key=idempotency_key,
            payload={
                "email": normalized_email,
                "password_fingerprint": security.fingerprint_secret(password),
            },
            owner_user_id=None,
            operation=operation,
        )
        session.commit()
    except IntegrityError as exc:
        session.rollback()
        constraint = getattr(getattr(exc.orig, "diag", None), "constraint_name", None)
        if constraint in {"uq_users_email_lower", "uq_users_pending_email_lower"}:
            raise ApiError(
                409,
                "registration_conflict",
                "Registration could not be completed with the supplied details.",
            ) from None
        raise

    if not replayed and raw_verification_token is not None:
        _send_verification_email(
            email_sender,
            email=normalized_email,
            token=raw_verification_token,
        )
    return result


def login_user(
    session: Session,
    *,
    email: str,
    password: str,
) -> LoginResult:
    normalized_email = normalize_email(email)
    user = _user_by_email(session, normalized_email)
    if user is None:
        security.verify_dummy_password(password)
        raise ApiError(
            401,
            "authentication_failed",
            "The email or password is incorrect.",
        )
    if not security.verify_password(user["password_hash"], password):
        raise ApiError(
            401,
            "authentication_failed",
            "The email or password is incorrect.",
        )

    _raise_account_state(user["status"])
    if (
        settings.require_email_verification
        and user["email_verified_at"] is None
    ):
        raise ApiError(
            403,
            "email_verification_required",
            "Verify your email address before signing in.",
        )

    session_id = uuid4()
    family_id = uuid4()
    raw_refresh_token = security.generate_opaque_token()
    refresh_expires_at = _now() + timedelta(
        seconds=settings.refresh_token_ttl_seconds
    )
    session.execute(
        text(
            """
            INSERT INTO auth_sessions (
                id, user_id, session_family_id, refresh_token_hash,
                refresh_expires_at
            ) VALUES (
                :id, :user_id, :family_id, :refresh_token_hash,
                :refresh_expires_at
            )
            """
        ),
        {
            "id": session_id,
            "user_id": user["id"],
            "family_id": family_id,
            "refresh_token_hash": security.hash_opaque_token(
                raw_refresh_token
            ),
            "refresh_expires_at": refresh_expires_at,
        },
    )
    session.commit()

    roles = _roles_for_user(session, user["id"])
    csrf_token = security.create_csrf_token(session_family_id=family_id)
    body = {
        "data": {
            "access_token": security.create_access_token(
                user_id=user["id"],
                session_id=session_id,
            ),
            "csrf_token": csrf_token,
            "token_type": "Bearer",
            "expires_in": security.access_token_expires_in(),
            "user": _user_summary(user, roles),
        }
    }
    return LoginResult(
        body=body,
        refresh_token=raw_refresh_token,
        refresh_expires_at=refresh_expires_at,
        csrf_token=csrf_token,
    )


def _session_by_refresh_hash(
    session: Session,
    refresh_token: str,
    *,
    for_update: bool = False,
) -> Mapping[str, Any] | None:
    statement = (
        """
        SELECT id, user_id, session_family_id, refresh_token_hash,
               created_at, refresh_expires_at, last_refreshed_at, revoked_at,
               replaced_by_session_id
        FROM auth_sessions
        WHERE refresh_token_hash = :refresh_token_hash
        """
        + (" FOR UPDATE" if for_update else "")
    )
    return session.execute(
        text(statement),
        {"refresh_token_hash": security.hash_opaque_token(refresh_token)},
    ).mappings().first()


def _session_by_id(
    session: Session,
    session_id: UUID,
) -> Mapping[str, Any] | None:
    return session.execute(
        text(
            """
            SELECT id, user_id, session_family_id, refresh_token_hash,
                   created_at, refresh_expires_at, last_refreshed_at,
                   revoked_at, replaced_by_session_id
            FROM auth_sessions
            WHERE id = :session_id
            """
        ),
        {"session_id": session_id},
    ).mappings().first()


def _revoke_family_and_audit(
    session: Session,
    *,
    family_id: UUID,
    user_id: UUID,
    session_id: UUID,
) -> None:
    session.execute(
        text(
            """
            UPDATE auth_sessions
            SET revoked_at = :now
            WHERE session_family_id = :family_id AND revoked_at IS NULL
            """
        ),
        {"now": _now(), "family_id": family_id},
    )
    session.execute(
        text(
            """
            INSERT INTO admin_audit_logs (
                id, actor_type, actor_user_id, action, target_user_id,
                target_type, target_id, details
            ) VALUES (
                :id, 'system', NULL, 'refresh_token_reuse', :target_user_id,
                'auth_session', :target_id, CAST(:details AS jsonb)
            )
            """
        ),
        {
            "id": uuid4(),
            "target_user_id": user_id,
            "target_id": session_id,
            "details": '{"reason": "refresh_token_reuse"}',
        },
    )


def _inactivity_expired(row: Mapping[str, Any]) -> bool:
    reference = row["last_refreshed_at"] or row["created_at"]
    return _now() - reference > timedelta(
        seconds=settings.refresh_inactivity_ttl_seconds
    )


def refresh_session(
    session: Session,
    *,
    refresh_token: str | None,
    csrf_token: str | None,
) -> LoginResult:
    if not refresh_token:
        raise ApiError(
            401,
            "authentication_required",
            "Authentication is required.",
        )

    row = _session_by_refresh_hash(session, refresh_token, for_update=True)
    if row is None:
        raise ApiError(
            401,
            "authentication_required",
            "Authentication is required.",
        )

    # Validate the synchronizer token before any state mutation. Without this
    # ordering a cross-site request could revoke a session family even though
    # it cannot read the CSRF token.
    if not csrf_token or not security.verify_csrf_token(
        csrf_token,
        session_family_id=row["session_family_id"],
    ):
        raise ApiError(
            403,
            "csrf_invalid",
            "The CSRF token is missing or invalid.",
        )

    user = _user_by_id(session, row["user_id"])
    if user is None:
        raise ApiError(
            401,
            "authentication_required",
            "Authentication is required.",
        )
    if user["status"] in {"suspended", "closed"}:
        _revoke_user_sessions(session, user["id"])
        session.commit()
        _raise_account_state(user["status"])

    if row["revoked_at"] is not None:
        _revoke_family_and_audit(
            session,
            family_id=row["session_family_id"],
            user_id=row["user_id"],
            session_id=row["id"],
        )
        session.commit()
        raise ApiError(
            401,
            "authentication_required",
            "Authentication is required.",
        )

    if row["refresh_expires_at"] <= _now():
        raise ApiError(
            401,
            "authentication_required",
            "Authentication is required.",
        )

    if _inactivity_expired(row):
        session.execute(
            text(
                """
                UPDATE auth_sessions
                SET revoked_at = :now
                WHERE id = :session_id AND revoked_at IS NULL
                """
            ),
            {"now": _now(), "session_id": row["id"]},
        )
        session.commit()
        raise ApiError(
            401,
            "authentication_required",
            "Authentication is required.",
        )

    if not csrf_token or not security.verify_csrf_token(
        csrf_token,
        session_family_id=row["session_family_id"],
    ):
        raise ApiError(
            403,
            "csrf_invalid",
            "The CSRF token is missing or invalid.",
        )

    new_session_id = uuid4()
    new_refresh_token = security.generate_opaque_token()
    # Insert the replacement before linking it: ``replaced_by_session_id`` is a
    # foreign key to ``auth_sessions.id``.
    session.execute(
        text(
            """
            INSERT INTO auth_sessions (
                id, user_id, session_family_id, refresh_token_hash,
                refresh_expires_at
            ) VALUES (
                :id, :user_id, :family_id, :refresh_token_hash,
                :refresh_expires_at
            )
            """
        ),
        {
            "id": new_session_id,
            "user_id": row["user_id"],
            "family_id": row["session_family_id"],
            "refresh_token_hash": security.hash_opaque_token(
                new_refresh_token
            ),
            "refresh_expires_at": row["refresh_expires_at"],
        },
    )
    session.execute(
        text(
            """
            UPDATE auth_sessions
            SET revoked_at = :now,
                last_refreshed_at = :now,
                replaced_by_session_id = :replacement_id
            WHERE id = :session_id
            """
        ),
        {
            "now": _now(),
            "replacement_id": new_session_id,
            "session_id": row["id"],
        },
    )
    session.commit()

    roles = _roles_for_user(session, user["id"])
    csrf_token_value = security.create_csrf_token(
        session_family_id=row["session_family_id"]
    )
    body = {
        "data": {
            "access_token": security.create_access_token(
                user_id=row["user_id"],
                session_id=new_session_id,
            ),
            "csrf_token": csrf_token_value,
            "token_type": "Bearer",
            "expires_in": security.access_token_expires_in(),
            "user": _user_summary(user, roles),
        }
    }
    return LoginResult(
        body=body,
        refresh_token=new_refresh_token,
        refresh_expires_at=row["refresh_expires_at"],
        csrf_token=csrf_token_value,
    )


def logout_session(
    session: Session,
    *,
    refresh_token: str | None,
    csrf_token: str | None,
) -> None:
    if not refresh_token:
        return
    row = _session_by_refresh_hash(session, refresh_token, for_update=True)
    if row is None:
        return

    if not csrf_token or not security.verify_csrf_token(
        csrf_token,
        session_family_id=row["session_family_id"],
    ):
        raise ApiError(
            403,
            "csrf_invalid",
            "The CSRF token is missing or invalid.",
        )

    user = _user_by_id(session, row["user_id"])
    if user is not None and user["status"] in {"suspended", "closed"}:
        _revoke_user_sessions(session, user["id"])
        session.commit()
        _raise_account_state(user["status"])

    if row["revoked_at"] is None:
        session.execute(
            text(
                """
                UPDATE auth_sessions
                SET revoked_at = :now
                WHERE id = :session_id AND revoked_at IS NULL
                """
            ),
            {"now": _now(), "session_id": row["id"]},
        )
    session.commit()


def csrf_for_session(
    session: Session,
    *,
    refresh_token: str | None,
) -> str:
    if not refresh_token:
        raise ApiError(
            401,
            "authentication_required",
            "Authentication is required.",
        )
    row = _session_by_refresh_hash(session, refresh_token)
    if (
        row is None
        or row["revoked_at"] is not None
        or row["refresh_expires_at"] <= _now()
        or _inactivity_expired(row)
    ):
        raise ApiError(
            401,
            "authentication_required",
            "Authentication is required.",
        )
    user = _user_by_id(session, row["user_id"])
    if user is None:
        raise ApiError(
            401,
            "authentication_required",
            "Authentication is required.",
        )
    _raise_account_state(user["status"])
    return security.create_csrf_token(
        session_family_id=row["session_family_id"]
    )


def authenticate_access_token(session: Session, token: str) -> AuthContext:
    try:
        payload = security.decode_access_token(token)
    except security.InvalidTokenError:
        raise ApiError(
            401,
            "authentication_required",
            "Authentication is required.",
        ) from None

    try:
        user_id = UUID(str(payload["sub"]))
        session_id = UUID(str(payload["sid"]))
    except (KeyError, ValueError):
        raise ApiError(
            401,
            "authentication_required",
            "Authentication is required.",
        ) from None

    user = _user_by_id(session, user_id)
    if user is None:
        raise ApiError(
            401,
            "authentication_required",
            "Authentication is required.",
        )
    _raise_account_state(user["status"])
    if (
        settings.require_email_verification
        and user["email_verified_at"] is None
    ):
        raise ApiError(
            403,
            "email_verification_required",
            "Verify your email address before using the application.",
        )

    session_row = _session_by_id(session, session_id)
    if (
        session_row is None
        or session_row["user_id"] != user_id
        or session_row["revoked_at"] is not None
        or session_row["refresh_expires_at"] <= _now()
    ):
        raise ApiError(
            401,
            "authentication_required",
            "Authentication is required.",
        )

    return AuthContext(
        user_id=user["id"],
        session_id=session_row["id"],
        session_family_id=session_row["session_family_id"],
        email=user["email"],
        status=user["status"],
        timezone=user["timezone"],
        revision=user["revision"],
        email_verified_at=user["email_verified_at"],
    )


def current_user_summary(session: Session, context: AuthContext) -> dict[str, Any]:
    user = _user_by_id(session, context.user_id)
    if user is None:
        raise ApiError(
            401,
            "authentication_required",
            "Authentication is required.",
        )
    return _user_summary(user, _roles_for_user(session, context.user_id))


def resend_email_verification(
    session: Session,
    *,
    email: str,
    idempotency_key: str,
    email_sender: EmailSender,
) -> IdempotencyResult:
    normalized_email = normalize_email(email)
    raw_token_holder: dict[str, str] = {}

    def operation() -> IdempotencyResult:
        if not settings.require_email_verification:
            return IdempotencyResult(
                status_code=202,
                body={"data": {"status": "accepted"}},
                headers={},
            )
        user = _user_by_email(session, normalized_email)
        if (
            user is not None
            and user["status"] == "active"
            and user["email_verified_at"] is None
        ):
            session.execute(
                text(
                    """
                    UPDATE auth_action_tokens
                    SET consumed_at = :now
                    WHERE user_id = :user_id
                      AND token_type = 'email_verification'
                      AND consumed_at IS NULL
                    """
                ),
                {"now": _now(), "user_id": user["id"]},
            )
            raw_token_holder["token"] = _insert_action_token(
                session,
                user_id=user["id"],
                token_type="email_verification",
                ttl_seconds=settings.email_verification_ttl_seconds,
            )
            raw_token_holder["email"] = user["email"]
        return IdempotencyResult(
            status_code=202,
            body={"data": {"status": "accepted"}},
            headers={},
        )

    result, replayed = run_idempotent(
        session,
        scope="public:email_verification_request",
        method="POST",
        route="/api/v1/auth/email-verification-requests",
        key=idempotency_key,
        payload={"email": normalized_email},
        owner_user_id=None,
        operation=operation,
    )
    session.commit()
    if not replayed and "token" in raw_token_holder:
        _send_verification_email(
            email_sender,
            email=raw_token_holder["email"],
            token=raw_token_holder["token"],
        )
    return result


def consume_email_verification(
    session: Session,
    *,
    token: str,
    idempotency_key: str,
) -> IdempotencyResult:
    token_hash = security.hash_opaque_token(token)

    def operation() -> IdempotencyResult:
        row = session.execute(
            text(
                """
                SELECT id, user_id, token_type, token_hash, expires_at,
                       consumed_at
                FROM auth_action_tokens
                WHERE token_hash = :token_hash
                  AND token_type = 'email_verification'
                FOR UPDATE
                """
            ),
            {"token_hash": token_hash},
        ).mappings().first()
        if (
            row is None
            or row["consumed_at"] is not None
            or row["expires_at"] <= _now()
        ):
            raise ApiError(
                422,
                "invalid_or_expired_verification_token",
                "The verification token is invalid or has expired.",
            )

        user = _user_by_id(session, row["user_id"])
        if user is None:
            raise ApiError(
                422,
                "invalid_or_expired_verification_token",
                "The verification token is invalid or has expired.",
            )

        now = _now()
        session.execute(
            text(
                """
                UPDATE auth_action_tokens
                SET consumed_at = :now
                WHERE id = :token_id AND consumed_at IS NULL
                """
            ),
            {"now": now, "token_id": row["id"]},
        )
        revision = user["revision"]
        if user["email_verified_at"] is None:
            revision += 1
            session.execute(
                text(
                    """
                    UPDATE users
                    SET email_verified_at = :now,
                        revision = :revision,
                        updated_at = :now
                    WHERE id = :user_id
                    """
                ),
                {
                    "now": now,
                    "revision": revision,
                    "user_id": user["id"],
                },
            )
        return IdempotencyResult(
            status_code=200,
            body={"data": {"email_verified": True}},
            headers={"ETag": _etag(revision)},
        )

    result, _ = run_idempotent(
        session,
        scope="public:email_verification_consume",
        method="POST",
        route="/api/v1/auth/email-verification/consume",
        key=idempotency_key,
        payload={"token": token},
        owner_user_id=None,
        operation=operation,
    )
    session.commit()
    return result


def request_password_reset(
    session: Session,
    *,
    email: str,
    email_sender: EmailSender,
) -> None:
    user = _user_by_email(session, normalize_email(email))
    raw_token: str | None = None
    if (
        user is not None
        and user["status"] == "active"
        and user["email_verified_at"] is not None
        and email_sender.is_available
    ):
        session.execute(
            text(
                """
                UPDATE auth_action_tokens
                SET consumed_at = :now
                WHERE user_id = :user_id
                  AND token_type = 'password_reset'
                  AND consumed_at IS NULL
                """
            ),
            {"now": _now(), "user_id": user["id"]},
        )
        raw_token = _insert_action_token(
            session,
            user_id=user["id"],
            token_type="password_reset",
            ttl_seconds=settings.password_reset_ttl_seconds,
        )

    if raw_token is not None and user is not None:
        if not _send_password_reset_email(
            email_sender,
            email=user["email"],
            token=raw_token,
        ):
            # Do not persist an undelivered reset token. Roll back both the
            # new token and the invalidation of any previous reset tokens.
            session.rollback()
            return

    session.commit()


def consume_password_reset(
    session: Session,
    *,
    token: str,
    new_password: str,
    idempotency_key: str,
) -> IdempotencyResult:
    token_hash = security.hash_opaque_token(token)

    def operation() -> IdempotencyResult:
        new_password_hash = security.hash_password(new_password)
        row = session.execute(
            text(
                """
                SELECT id, user_id, token_type, token_hash, expires_at,
                       consumed_at
                FROM auth_action_tokens
                WHERE token_hash = :token_hash
                  AND token_type = 'password_reset'
                FOR UPDATE
                """
            ),
            {"token_hash": token_hash},
        ).mappings().first()
        if (
            row is None
            or row["consumed_at"] is not None
            or row["expires_at"] <= _now()
        ):
            raise ApiError(
                422,
                "invalid_or_expired_password_reset_token",
                "The password-reset token is invalid or has expired.",
            )

        user = _user_by_id(session, row["user_id"])
        if user is None:
            raise ApiError(
                422,
                "invalid_or_expired_password_reset_token",
                "The password-reset token is invalid or has expired.",
            )
        _raise_account_state(user["status"])

        now = _now()
        session.execute(
            text(
                """
                UPDATE users
                SET password_hash = :password_hash,
                    revision = revision + 1,
                    updated_at = :now
                WHERE id = :user_id
                """
            ),
            {
                "password_hash": new_password_hash,
                "now": now,
                "user_id": user["id"],
            },
        )
        session.execute(
            text(
                """
                UPDATE auth_action_tokens
                SET consumed_at = :now
                WHERE id = :token_id AND consumed_at IS NULL
                """
            ),
            {"now": now, "token_id": row["id"]},
        )
        _revoke_user_sessions(session, user["id"])
        return IdempotencyResult(
            status_code=200,
            body={"data": {"password_reset": True}},
            headers={},
        )

    result, _ = run_idempotent(
        session,
        scope="public:password_reset_consume",
        method="POST",
        route="/api/v1/auth/password-reset/consume",
        key=idempotency_key,
        payload={
            "token": token,
            "new_password_fingerprint": security.fingerprint_secret(
                new_password
            ),
        },
        owner_user_id=None,
        operation=operation,
    )
    session.commit()
    return result


def change_password(
    session: Session,
    *,
    context: AuthContext,
    current_password: str,
    new_password: str,
) -> None:
    user = _user_by_id(session, context.user_id)
    if user is None:
        raise ApiError(
            401,
            "authentication_required",
            "Authentication is required.",
        )
    if not security.verify_password(user["password_hash"], current_password):
        raise ApiError(
            401,
            "authentication_failed",
            "The current password is incorrect.",
        )

    session.execute(
        text(
            """
            UPDATE users
            SET password_hash = :password_hash,
                revision = revision + 1,
                updated_at = :now
            WHERE id = :user_id
            """
        ),
        {
            "password_hash": security.hash_password(new_password),
            "now": _now(),
            "user_id": context.user_id,
        },
    )
    session.execute(
        text(
            """
            UPDATE auth_sessions
            SET revoked_at = :now
            WHERE user_id = :user_id
              AND id <> :current_session_id
              AND revoked_at IS NULL
            """
        ),
        {
            "now": _now(),
            "user_id": context.user_id,
            "current_session_id": context.session_id,
        },
    )
    session.commit()
