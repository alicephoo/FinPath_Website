from __future__ import annotations

import asyncio
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Any
from uuid import UUID, uuid4

import httpx
import pytest
from pydantic import SecretStr
from sqlalchemy import create_engine, text
from sqlalchemy.engine import Engine
from sqlalchemy.exc import DBAPIError
from sqlalchemy.orm import Session, sessionmaker

from app.core import security
from app.core.config import settings
from app.db.session import get_db, get_registration_db
from app.main import app
from app.services import auth as auth_service
from app.services.email import (
    EmailDeliveryError,
    EmailMessage,
    UnconfiguredEmailSender,
    get_email_sender,
)
from tests.pg_test_env import require_test_database_urls, require_test_registration_url

ORIGIN = "http://localhost:5173"
PASSWORD = "correct horse battery staple"
NEW_PASSWORD = "a different long password"
AUTH_ROUTE_KEYS = (
    "/api/v1/auth/register",
    "/api/v1/auth/email-verification-requests",
    "/api/v1/auth/email-verification/consume",
    "/api/v1/auth/password-reset/consume",
)


class RecordingSender:
    def __init__(self) -> None:
        self.messages: list[EmailMessage] = []
        self.fail = False

    @property
    def is_available(self) -> bool:
        return True

    def send(self, message: EmailMessage) -> None:
        if self.fail:
            raise EmailDeliveryError("simulated delivery failure")
        self.messages.append(message)

    def last_token(self, email: str, purpose: str) -> str:
        for message in reversed(self.messages):
            if message.to == email and message.purpose == purpose:
                return message.token
        raise AssertionError(f"No {purpose} email for {email}")


@dataclass
class AuthEnv:
    runtime_engine: Engine
    registration_engine: Engine
    migration_engine: Engine
    sender: RecordingSender
    database_name: str

    def client(self, **kwargs: Any) -> httpx.AsyncClient:
        transport = httpx.ASGITransport(
            app=app,
            raise_app_exceptions=False,
        )
        return httpx.AsyncClient(
            transport=transport,
            base_url="https://test",
            **kwargs,
        )

    def one(self, statement: str, parameters: dict[str, Any] | None = None):
        with self.migration_engine.connect() as connection:
            return connection.execute(
                text(statement), parameters or {}
            ).mappings().first()

    def all(self, statement: str, parameters: dict[str, Any] | None = None):
        with self.migration_engine.connect() as connection:
            return list(
                connection.execute(
                    text(statement), parameters or {}
                ).mappings()
            )

    def execute(self, statement: str, parameters: dict[str, Any] | None = None):
        with self.migration_engine.begin() as connection:
            connection.execute(text(statement), parameters or {})


@pytest.fixture
def auth_env(monkeypatch: pytest.MonkeyPatch):
    runtime_url, migration_url, test_database = require_test_database_urls()
    registration_url, registration_database = require_test_registration_url()
    assert registration_database == test_database

    runtime_engine = create_engine(
        runtime_url, hide_parameters=True, pool_pre_ping=True
    )
    registration_engine = create_engine(
        registration_url, hide_parameters=True, pool_pre_ping=True
    )
    migration_engine = create_engine(
        migration_url, hide_parameters=True, pool_pre_ping=True
    )

    with runtime_engine.connect() as connection:
        assert connection.execute(text("SELECT current_user")).scalar_one() == (
            "finpath_app"
        )
        assert connection.execute(
            text("SELECT current_database()")
        ).scalar_one() == test_database
    with registration_engine.connect() as connection:
        assert connection.execute(text("SELECT current_user")).scalar_one() == (
            "finpath_registration"
        )
    with migration_engine.connect() as connection:
        assert connection.execute(text("SELECT current_user")).scalar_one() == (
            "finpath_migrator"
        )
        fixture_started_at = connection.execute(
            text("SELECT now()")
        ).scalar_one()
        revision = connection.execute(
            text("SELECT version_num FROM alembic_version")
        ).scalar_one()
        assert revision == "c4f7a1b9e2d6"
        roles = connection.execute(
            text("SELECT id, name FROM roles WHERE id IN (1, 2) ORDER BY id")
        ).all()
        assert roles == [(1, "user"), (2, "admin")]

    runtime_factory = sessionmaker(
        bind=runtime_engine,
        autoflush=False,
        expire_on_commit=False,
    )
    registration_factory = sessionmaker(
        bind=registration_engine,
        autoflush=False,
        expire_on_commit=False,
    )

    def override_get_db():
        session: Session = runtime_factory()
        try:
            yield session
        finally:
            session.close()

    def override_get_registration_db():
        session: Session = registration_factory()
        try:
            yield session
        finally:
            session.close()

    sender = RecordingSender()
    app.dependency_overrides[get_db] = override_get_db
    app.dependency_overrides[get_registration_db] = override_get_registration_db
    app.dependency_overrides[get_email_sender] = lambda: sender

    monkeypatch.setattr(
        settings,
        "access_token_secret",
        SecretStr("test-only-access-token-secret-with-entropy"),
    )
    monkeypatch.setattr(settings, "frontend_origins", ORIGIN)
    monkeypatch.setattr(settings, "refresh_cookie_secure", True)
    # Tests are independent of the local .env email-verification setting.
    monkeypatch.setattr(settings, "require_email_verification", True)

    env = AuthEnv(
        runtime_engine=runtime_engine,
        registration_engine=registration_engine,
        migration_engine=migration_engine,
        sender=sender,
        database_name=test_database,
    )
    try:
        yield env
    finally:
        app.dependency_overrides.clear()
        with migration_engine.begin() as connection:
            # ``admin_audit_logs`` is append-only. Deleting the test users sets
            # ``target_user_id`` to NULL through the approved ON DELETE SET NULL
            # action, which the protection trigger explicitly permits.
            connection.execute(
                text(
                    """
                    DELETE FROM user_roles
                    WHERE user_id IN (
                        SELECT id FROM users
                        WHERE email LIKE 'auth-test-%@example.test'
                    )
                    """
                )
            )
            connection.execute(
                text(
                    """
                    DELETE FROM user_financial_settings
                    WHERE user_id IN (
                        SELECT id FROM users
                        WHERE email LIKE 'auth-test-%@example.test'
                    )
                    """
                )
            )
            connection.execute(
                text(
                    "DELETE FROM users "
                    "WHERE email LIKE 'auth-test-%@example.test'"
                )
            )
            for route in AUTH_ROUTE_KEYS:
                connection.execute(
                    text(
                        "DELETE FROM idempotency_records "
                        "WHERE route_key = :route "
                        "AND created_at >= :started_at"
                    ),
                    {
                        "route": route,
                        "started_at": fixture_started_at,
                    },
                )
        runtime_engine.dispose()
        registration_engine.dispose()
        migration_engine.dispose()


def new_email() -> str:
    return f"auth-test-{uuid4().hex}@example.test"


def new_key(prefix: str = "key") -> str:
    return f"{prefix}-{uuid4().hex}"


def headers(
    *,
    origin: str | None = ORIGIN,
    idem: str | None = None,
    csrf: str | None = None,
    token: str | None = None,
    extra: dict[str, str] | None = None,
) -> dict[str, str]:
    result: dict[str, str] = {}
    if origin is not None:
        result["Origin"] = origin
    if idem is not None:
        result["Idempotency-Key"] = idem
    if csrf is not None:
        result["X-CSRF-Token"] = csrf
    if token is not None:
        result["Authorization"] = f"Bearer {token}"
    if extra:
        result.update(extra)
    return result


async def register(
    client: httpx.AsyncClient,
    email: str,
    *,
    password: str = PASSWORD,
    key: str | None = None,
) -> tuple[httpx.Response, str]:
    idempotency_key = key or new_key("reg")
    response = await client.post(
        "/api/v1/auth/register",
        json={"email": email, "password": password},
        headers=headers(idem=idempotency_key),
    )
    return response, idempotency_key


async def verify(
    client: httpx.AsyncClient,
    env: AuthEnv,
    email: str,
    *,
    key: str | None = None,
) -> httpx.Response:
    token = env.sender.last_token(email, "email_verification")
    return await client.post(
        "/api/v1/auth/email-verification/consume",
        json={"token": token},
        headers=headers(idem=key or new_key("verify")),
    )


async def login(
    client: httpx.AsyncClient,
    email: str,
    *,
    password: str = PASSWORD,
    origin: str | None = ORIGIN,
) -> httpx.Response:
    return await client.post(
        "/api/v1/auth/login",
        json={"email": email, "password": password},
        headers=headers(origin=origin),
    )


async def register_verify_login(
    client: httpx.AsyncClient,
    env: AuthEnv,
    email: str | None = None,
) -> tuple[str, dict[str, Any]]:
    address = email or new_email()
    registered, _ = await register(client, address)
    assert registered.status_code == 201, registered.text
    verified = await verify(client, env, address)
    assert verified.status_code == 200, verified.text
    logged_in = await login(client, address)
    assert logged_in.status_code == 200, logged_in.text
    return address, logged_in.json()["data"]


def _sqlstate(exc: DBAPIError) -> str | None:
    return getattr(getattr(exc, "orig", None), "sqlstate", None)


def test_registration_creates_required_rows_and_safe_idempotency(auth_env: AuthEnv):
    email = new_email()

    async def scenario() -> str:
        async with auth_env.client() as client:
            response, key = await register(client, email)
            assert response.status_code == 201, response.text
            body = response.json()["data"]
            assert body["email"] == email
            assert body["roles"] == ["user"]
            assert body["status"] == "active"
            assert body["verification_status"] == "required"
            assert response.headers["etag"] == '"1"'
            assert "access_token" not in response.text
            assert "finpath_refresh" not in response.headers.get("set-cookie", "")

            replayed, _ = await register(client, email, key=key)
            assert replayed.status_code == 201
            assert replayed.json() == response.json()
            assert len(auth_env.sender.messages) == 1

            conflict, _ = await register(client, email)
            assert conflict.status_code == 409
            assert conflict.json()["error"]["code"] == "registration_conflict"
            assert "already" not in conflict.json()["error"]["message"].lower()
            return key

    key = asyncio.run(scenario())

    user = auth_env.one(
        "SELECT id, email, password_hash, email_verified_at, status, timezone, "
        "revision FROM users WHERE lower(email) = :email",
        {"email": email},
    )
    assert user is not None
    assert user["password_hash"].startswith("$argon2id$")
    assert PASSWORD not in user["password_hash"]
    assert security.verify_password(user["password_hash"], PASSWORD) is True
    assert user["email_verified_at"] is None
    assert user["status"] == "active"
    assert user["timezone"] == "Asia/Yangon"
    assert user["revision"] == 1

    roles = auth_env.all(
        "SELECT role_id FROM user_roles WHERE user_id = :user_id",
        {"user_id": user["id"]},
    )
    assert [row["role_id"] for row in roles] == [1]

    financial_settings = auth_env.one(
        "SELECT starting_balance_kyat, effective_date FROM user_financial_settings "
        "WHERE user_id = :user_id",
        {"user_id": user["id"]},
    )
    assert financial_settings is not None
    assert financial_settings["starting_balance_kyat"] is None
    assert financial_settings["effective_date"] is None

    token = auth_env.sender.messages[0].token
    action_token = auth_env.one(
        "SELECT token_hash, expires_at, consumed_at FROM auth_action_tokens "
        "WHERE user_id = :user_id AND token_type = 'email_verification'",
        {"user_id": user["id"]},
    )
    assert action_token is not None
    assert action_token["token_hash"] == security.hash_opaque_token(token)
    assert action_token["token_hash"] != token
    assert action_token["consumed_at"] is None
    assert action_token["expires_at"] > datetime.now(timezone.utc)

    idempotency = auth_env.one(
        "SELECT request_hash, response_body FROM idempotency_records "
        "WHERE route_key = '/api/v1/auth/register' AND key_hash = :key_hash",
        {"key_hash": security.hash_opaque_token(key)},
    )
    assert idempotency is not None
    assert PASSWORD not in str(idempotency["response_body"])
    assert token not in str(idempotency["response_body"])
    assert idempotency["request_hash"] != token

    admin_assignments = auth_env.one(
        "SELECT count(*) AS count FROM user_roles "
        "WHERE user_id = :user_id AND role_id = 2",
        {"user_id": user["id"]},
    )
    assert admin_assignments["count"] == 0


def test_registration_rejects_invalid_input_and_role_fields(auth_env: AuthEnv):
    async def scenario():
        async with auth_env.client() as client:
            short = await client.post(
                "/api/v1/auth/register",
                json={"email": new_email(), "password": "too-short"},
                headers=headers(idem=new_key()),
            )
            assert short.status_code == 422
            assert short.json()["error"]["code"] == "validation_error"

            invalid_email = await client.post(
                "/api/v1/auth/register",
                json={"email": "not-an-email", "password": PASSWORD},
                headers=headers(idem=new_key()),
            )
            assert invalid_email.status_code == 422

            role_field = await client.post(
                "/api/v1/auth/register",
                json={
                    "email": new_email(),
                    "password": PASSWORD,
                    "role_id": 2,
                },
                headers=headers(idem=new_key()),
            )
            assert role_field.status_code == 422

            admin_field = await client.post(
                "/api/v1/auth/register",
                json={
                    "email": new_email(),
                    "password": PASSWORD,
                    "is_admin": True,
                },
                headers=headers(idem=new_key()),
            )
            assert admin_field.status_code == 422

            missing_key = await client.post(
                "/api/v1/auth/register",
                json={"email": new_email(), "password": PASSWORD},
                headers=headers(),
            )
            assert missing_key.status_code == 422
            assert missing_key.json()["error"]["code"] == "validation_error"

    asyncio.run(scenario())
    # Scope the count to records this module creates. The test database may
    # contain unrelated pre-existing E2E users that must not be touched.
    count = auth_env.one(
        "SELECT count(*) AS count FROM users "
        "WHERE email LIKE 'auth-test-%@example.test'"
    )
    assert count["count"] == 0


def test_registration_transaction_rolls_back_on_required_insert_failure(
    auth_env: AuthEnv,
    monkeypatch: pytest.MonkeyPatch,
):
    email = new_email()

    def fail_settings(*args, **kwargs):
        raise RuntimeError("simulated settings insert failure")

    monkeypatch.setattr(auth_service, "_insert_financial_settings", fail_settings)

    async def scenario():
        async with auth_env.client() as client:
            response, key = await register(client, email)
            assert response.status_code == 500
            return key

    key = asyncio.run(scenario())

    user = auth_env.one(
        "SELECT id FROM users WHERE lower(email) = :email",
        {"email": email},
    )
    assert user is None
    # Scope the check to this attempt's key. Historical registration records
    # from earlier test runs must not make the rollback assertion ambiguous.
    assert auth_env.one(
        "SELECT count(*) AS count FROM idempotency_records "
        "WHERE route_key = '/api/v1/auth/register' AND key_hash = :key_hash",
        {"key_hash": security.hash_opaque_token(key)},
    )["count"] == 0
    assert len(auth_env.sender.messages) == 0


def test_registration_identity_is_least_privilege(auth_env: AuthEnv):
    with auth_env.registration_engine.connect() as connection:
        assert connection.execute(text("SELECT current_user")).scalar_one() == (
            "finpath_registration"
        )
        role = connection.execute(
            text(
                "SELECT rolsuper, rolcreatedb, rolcreaterole, rolreplication, "
                "rolbypassrls FROM pg_roles WHERE rolname = 'finpath_registration'"
            )
        ).one()
        assert role == (False, False, False, False, False)
        assert connection.execute(
            text(
                "SELECT count(*) FROM pg_auth_members AS membership "
                "JOIN pg_roles AS member ON member.oid = membership.member "
                "WHERE member.rolname = 'finpath_registration'"
            )
        ).scalar_one() == 0
        assert connection.execute(
            text(
                "SELECT pg_has_role('finpath_registration', "
                "'finpath_migrator', 'MEMBER')"
            )
        ).scalar_one() is False
        assert connection.execute(
            text(
                "SELECT has_column_privilege('finpath_registration', "
                "'public.users', 'email_verified_at', 'INSERT')"
            )
        ).scalar_one() is True
        assert connection.execute(
            text(
                "SELECT has_column_privilege('finpath_registration', "
                "'public.users', 'email_verified_at', 'UPDATE')"
            )
        ).scalar_one() is False

        for table, column in [
            ("users", "email"),
            ("users", "status"),
            ("transactions", "amount_kyat"),
            ("loans", "principal_kyat"),
            ("savings_entries", "amount_kyat"),
        ]:
            privilege = "INSERT" if table == "users" else "SELECT"
            if table == "users" and column == "email":
                assert connection.execute(
                    text(
                        "SELECT has_column_privilege('finpath_registration', "
                        "'public.users', 'email', 'INSERT')"
                    )
                ).scalar_one() is True
                assert connection.execute(
                    text(
                        "SELECT has_column_privilege('finpath_registration', "
                        "'public.users', 'email', 'SELECT')"
                    )
                ).scalar_one() is False
            elif table == "users":
                assert connection.execute(
                    text(
                        "SELECT has_column_privilege('finpath_registration', "
                        "'public.users', 'status', 'INSERT')"
                    )
                ).scalar_one() is False
            else:
                assert connection.execute(
                    text(
                        "SELECT has_column_privilege('finpath_registration', "
                        f"'public.{table}', '{column}', '{privilege}')"
                    )
                ).scalar_one() is False

        with pytest.raises(DBAPIError) as admin_attempt:
            connection.execute(
                text(
                    "INSERT INTO user_roles (user_id, role_id) "
                    "VALUES (:user_id, 2)"
                ),
                {"user_id": uuid4()},
            )
        assert _sqlstate(admin_attempt.value) == "42501"
        connection.rollback()

        with pytest.raises(DBAPIError) as user_role_attempt:
            connection.execute(
                text(
                    "INSERT INTO user_roles (user_id, role_id) "
                    "VALUES (:user_id, 1)"
                ),
                {"user_id": uuid4()},
            )
        # The guard allows role 1; the unrelated user id fails the FK instead.
        assert _sqlstate(user_role_attempt.value) == "23503"
        connection.rollback()

        for statement in (
            "SELECT * FROM users",
            "SELECT * FROM transactions",
            "SELECT * FROM loans",
            "SELECT * FROM savings_entries",
            "SELECT * FROM user_financial_settings",
            "UPDATE users SET status = 'suspended'",
            "DELETE FROM users",
            "UPDATE transactions SET amount_kyat = amount_kyat",
            "DELETE FROM transactions",
            "UPDATE user_financial_settings "
            "SET starting_balance_kyat = 1",
            "DELETE FROM auth_action_tokens",
            "UPDATE auth_action_tokens SET consumed_at = now()",
            "INSERT INTO auth_sessions "
            "(id, user_id, session_family_id, refresh_token_hash, "
            "refresh_expires_at) VALUES "
            "(:id, :user_id, :family, 'hash', now() + interval '1 day')",
        ):
            parameters = {}
            if "auth_sessions" in statement:
                parameters = {
                    "id": uuid4(),
                    "user_id": uuid4(),
                    "family": uuid4(),
                }
            with pytest.raises(DBAPIError) as denied:
                connection.execute(text(statement), parameters)
            assert _sqlstate(denied.value) == "42501", statement
            connection.rollback()

        assert connection.execute(
            text(
                "SELECT prosecdef FROM pg_proc "
                "WHERE proname = 'enforce_registration_role_assignment'"
            )
        ).scalar_one() is False

        function_privileges = connection.execute(
            text(
                """
                SELECT proc.prosecdef,
                       proc.proowner::regrole::text AS owner,
                       has_function_privilege(
                           'public', proc.oid, 'EXECUTE'
                       ) AS public_execute,
                       has_function_privilege(
                           'finpath_app', proc.oid, 'EXECUTE'
                       ) AS app_execute,
                       has_function_privilege(
                           'finpath_registration', proc.oid, 'EXECUTE'
                       ) AS registration_execute
                FROM pg_proc AS proc
                JOIN pg_namespace AS namespace
                  ON namespace.oid = proc.pronamespace
                WHERE namespace.nspname = 'public'
                  AND proc.proname =
                      'enforce_registration_role_assignment'
                """
            )
        ).mappings().one()
        assert function_privileges["prosecdef"] is False
        assert function_privileges["owner"] == "finpath_migrator"
        assert function_privileges["public_execute"] is False
        assert function_privileges["app_execute"] is False
        assert function_privileges["registration_execute"] is False

        token_guard_privileges = connection.execute(
            text(
                """
                SELECT proc.prosecdef,
                       proc.proowner::regrole::text AS owner,
                       has_function_privilege(
                           'public', proc.oid, 'EXECUTE'
                       ) AS public_execute,
                       has_function_privilege(
                           'finpath_app', proc.oid, 'EXECUTE'
                       ) AS app_execute,
                       has_function_privilege(
                           'finpath_registration', proc.oid, 'EXECUTE'
                       ) AS registration_execute
                FROM pg_proc AS proc
                JOIN pg_namespace AS namespace
                  ON namespace.oid = proc.pronamespace
                WHERE namespace.nspname = 'public'
                  AND proc.proname =
                      'enforce_registration_action_token'
                """
            )
        ).mappings().one()
        assert token_guard_privileges["prosecdef"] is False
        assert token_guard_privileges["owner"] == "finpath_migrator"
        assert token_guard_privileges["public_execute"] is False
        assert token_guard_privileges["app_execute"] is False
        assert token_guard_privileges["registration_execute"] is False

        # The registration identity may only mint the initial email-verification
        # token. Minting a password-reset token for an existing account would
        # be a privilege-escalation path.
        with pytest.raises(DBAPIError) as reset_token_attempt:
            connection.execute(
                text(
                    "INSERT INTO auth_action_tokens "
                    "(id, user_id, token_type, token_hash, expires_at) "
                    "VALUES (:id, :user_id, 'password_reset', :token_hash, "
                    "now() + interval '1 hour')"
                ),
                {
                    "id": uuid4(),
                    "user_id": uuid4(),
                    "token_hash": uuid4().hex,
                },
            )
        assert _sqlstate(reset_token_attempt.value) == "42501"
        connection.rollback()

        # ``target_email`` is not granted to the registration identity, so an
        # email-change token is rejected even before the guard can inspect it.
        with pytest.raises(DBAPIError) as email_change_attempt:
            connection.execute(
                text(
                    "INSERT INTO auth_action_tokens "
                    "(id, user_id, token_type, token_hash, target_email, "
                    "expires_at) VALUES "
                    "(:id, :user_id, 'email_change_verification', :token_hash, "
                    ":target_email, now() + interval '1 hour')"
                ),
                {
                    "id": uuid4(),
                    "user_id": uuid4(),
                    "token_hash": uuid4().hex,
                    "target_email": "attacker@example.test",
                },
            )
        assert _sqlstate(email_change_attempt.value) == "42501"
        connection.rollback()

        # A permitted email-verification token still reaches the foreign-key
        # check, proving the guard does not block the registration flow.
        with pytest.raises(DBAPIError) as verification_token_attempt:
            connection.execute(
                text(
                    "INSERT INTO auth_action_tokens "
                    "(id, user_id, token_type, token_hash, expires_at) "
                    "VALUES (:id, :user_id, 'email_verification', :token_hash, "
                    "now() + interval '1 hour')"
                ),
                {
                    "id": uuid4(),
                    "user_id": uuid4(),
                    "token_hash": uuid4().hex,
                },
            )
        assert _sqlstate(verification_token_attempt.value) == "23503"
        connection.rollback()

        for table in (
            "users",
            "user_roles",
            "auth_action_tokens",
            "idempotency_records",
            "transactions",
            "user_financial_settings",
        ):
            for privilege in ("SELECT", "INSERT", "UPDATE", "DELETE"):
                assert connection.execute(
                    text(
                        "SELECT has_table_privilege('public', "
                        f"'public.{table}', '{privilege}')"
                    )
                ).scalar_one() is False, (table, privilege)

        assert connection.execute(
            text(
                "SELECT count(*) FROM pg_class AS relation "
                "JOIN pg_roles AS owner ON owner.oid = relation.relowner "
                "JOIN pg_namespace AS namespace "
                "ON namespace.oid = relation.relnamespace "
                "WHERE namespace.nspname = 'public' "
                "AND owner.rolname = 'finpath_registration'"
            )
        ).scalar_one() == 0
        assert connection.execute(
            text(
                "SELECT count(*) FROM pg_namespace AS namespace "
                "JOIN pg_roles AS owner ON owner.oid = namespace.nspowner "
                "WHERE owner.rolname = 'finpath_registration'"
            )
        ).scalar_one() == 0
        assert connection.execute(
            text(
                "SELECT count(*) FROM pg_database AS database "
                "JOIN pg_roles AS owner ON owner.oid = database.datdba "
                "WHERE owner.rolname = 'finpath_registration'"
            )
        ).scalar_one() == 0

    with auth_env.runtime_engine.connect() as connection:
        assert connection.execute(
            text(
                "SELECT has_table_privilege('finpath_app', "
                "'public.user_roles', 'SELECT')"
            )
        ).scalar_one() is True
        for privilege in ("INSERT", "UPDATE", "DELETE"):
            assert connection.execute(
                text(
                    "SELECT has_table_privilege('finpath_app', "
                    f"'public.user_roles', '{privilege}')"
                )
            ).scalar_one() is False


def test_email_verification_lifecycle(auth_env: AuthEnv):
    email = new_email()

    async def scenario():
        async with auth_env.client() as client:
            registered, _ = await register(client, email)
            assert registered.status_code == 201
            user_id = registered.json()["data"]["id"]

            verified = await verify(client, auth_env, email)
            assert verified.status_code == 200, verified.text
            assert verified.json() == {"data": {"email_verified": True}}
            assert verified.headers["etag"] == '"2"'

            user = auth_env.one(
                "SELECT email_verified_at, revision FROM users WHERE id = :id",
                {"id": UUID(user_id)},
            )
            assert user["email_verified_at"] is not None
            assert user["revision"] == 2
            token_row = auth_env.one(
                "SELECT consumed_at FROM auth_action_tokens "
                "WHERE user_id = :id AND token_type = 'email_verification'",
                {"id": UUID(user_id)},
            )
            assert token_row["consumed_at"] is not None

            logged_in = await login(client, email)
            assert logged_in.status_code == 200, logged_in.text
            assert logged_in.json()["data"]["access_token"]

            invalid = await client.post(
                "/api/v1/auth/email-verification/consume",
                json={"token": "x" * 64},
                headers=headers(idem=new_key("verify")),
            )
            assert invalid.status_code == 422
            assert (
                invalid.json()["error"]["code"]
                == "invalid_or_expired_verification_token"
            )

            second_email = new_email()
            await register(client, second_email)
            expired_token = auth_env.sender.last_token(
                second_email, "email_verification"
            )
            auth_env.execute(
                "UPDATE auth_action_tokens SET expires_at = now() - interval '1 minute' "
                "WHERE token_hash = :token_hash",
                {"token_hash": security.hash_opaque_token(expired_token)},
            )
            expired = await client.post(
                "/api/v1/auth/email-verification/consume",
                json={"token": expired_token},
                headers=headers(idem=new_key("verify")),
            )
            assert expired.status_code == 422

            third_email = new_email()
            await register(client, third_email)
            old_token = auth_env.sender.last_token(
                third_email, "email_verification"
            )
            resend = await client.post(
                "/api/v1/auth/email-verification-requests",
                json={"email": third_email},
                headers=headers(idem=new_key("resend")),
            )
            assert resend.status_code == 202
            new_token = auth_env.sender.last_token(
                third_email, "email_verification"
            )
            assert new_token != old_token

            revoked = await client.post(
                "/api/v1/auth/email-verification/consume",
                json={"token": old_token},
                headers=headers(idem=new_key("verify")),
            )
            assert revoked.status_code == 422
            accepted = await client.post(
                "/api/v1/auth/email-verification/consume",
                json={"token": new_token},
                headers=headers(idem=new_key("verify")),
            )
            assert accepted.status_code == 200

    asyncio.run(scenario())


def test_email_verification_consume_is_idempotent(auth_env: AuthEnv):
    email = new_email()

    async def scenario():
        async with auth_env.client() as client:
            await register(client, email)
            token = auth_env.sender.last_token(email, "email_verification")
            key = new_key("verify")
            first = await client.post(
                "/api/v1/auth/email-verification/consume",
                json={"token": token},
                headers=headers(idem=key),
            )
            second = await client.post(
                "/api/v1/auth/email-verification/consume",
                json={"token": token},
                headers=headers(idem=key),
            )
            assert first.status_code == 200
            assert second.status_code == 200
            assert second.json() == first.json()

            reused = await client.post(
                "/api/v1/auth/email-verification/consume",
                json={"token": token},
                headers=headers(idem=new_key("verify")),
            )
            assert reused.status_code == 422

    asyncio.run(scenario())


def test_email_verification_request_requires_and_honors_idempotency(
    auth_env: AuthEnv,
):
    email = new_email()

    async def scenario():
        async with auth_env.client() as client:
            await register(client, email)
            first_token = auth_env.sender.last_token(
                email, "email_verification"
            )

            missing_key = await client.post(
                "/api/v1/auth/email-verification-requests",
                json={"email": email},
                headers=headers(),
            )
            assert missing_key.status_code == 422
            assert missing_key.json()["error"]["code"] == "validation_error"

            key = new_key("resend")
            first = await client.post(
                "/api/v1/auth/email-verification-requests",
                json={"email": email},
                headers=headers(idem=key),
            )
            assert first.status_code == 202
            assert first.json() == {"data": {"status": "accepted"}}
            second_token = auth_env.sender.last_token(
                email, "email_verification"
            )
            assert second_token != first_token
            message_count = len(auth_env.sender.messages)

            replayed = await client.post(
                "/api/v1/auth/email-verification-requests",
                json={"email": email},
                headers=headers(idem=key),
            )
            assert replayed.status_code == 202
            assert replayed.json() == first.json()
            assert len(auth_env.sender.messages) == message_count

            different = await client.post(
                "/api/v1/auth/email-verification-requests",
                json={"email": new_email()},
                headers=headers(idem=key),
            )
            assert different.status_code == 409
            assert different.json()["error"]["code"] == "idempotency_key_reused"

            unknown = await client.post(
                "/api/v1/auth/email-verification-requests",
                json={"email": new_email()},
                headers=headers(idem=new_key("resend")),
            )
            assert unknown.status_code == 202
            assert len(auth_env.sender.messages) == message_count

    asyncio.run(scenario())


def test_unverified_login_returns_403_without_credentials(auth_env: AuthEnv):
    email = new_email()

    async def scenario():
        async with auth_env.client() as client:
            await register(client, email)
            response = await login(client, email)
            assert response.status_code == 403
            assert response.json()["error"]["code"] == "email_verification_required"
            assert "access_token" not in response.text
            assert "finpath_refresh" not in response.headers.get("set-cookie", "")

            await verify(client, auth_env, email)
            verified = await login(client, email)
            assert verified.status_code == 200
            assert verified.json()["data"]["access_token"]

    asyncio.run(scenario())


def test_registration_without_email_verification_creates_verified_account(
    auth_env: AuthEnv,
    monkeypatch: pytest.MonkeyPatch,
):
    monkeypatch.setattr(settings, "require_email_verification", False)
    email = new_email()

    async def scenario():
        async with auth_env.client() as client:
            response, key = await register(client, email)
            assert response.status_code == 201, response.text
            body = response.json()["data"]
            assert body["email"] == email
            assert body["verification_status"] == "not_required"
            assert "access_token" not in response.text
            assert "finpath_refresh" not in response.headers.get("set-cookie", "")

            replayed, _ = await register(client, email, key=key)
            assert replayed.status_code == 201
            assert replayed.json() == response.json()

            logged_in = await login(client, email)
            assert logged_in.status_code == 200, logged_in.text
            data = logged_in.json()["data"]
            assert data["access_token"]
            assert data["user"]["email_verified"] is True
            assert client.cookies.get(settings.refresh_cookie_name)

            me = await client.get(
                "/api/v1/auth/me",
                headers=headers(token=data["access_token"]),
            )
            assert me.status_code == 200
            assert me.json()["data"]["email_verified"] is True

            resend = await client.post(
                "/api/v1/auth/email-verification-requests",
                json={"email": email},
                headers=headers(idem=new_key("resend")),
            )
            assert resend.status_code == 202
            assert resend.json() == {"data": {"status": "accepted"}}

    asyncio.run(scenario())

    assert auth_env.sender.messages == []
    token_count = auth_env.one(
        """
        SELECT count(*) AS count
        FROM auth_action_tokens t
        JOIN users u ON u.id = t.user_id
        WHERE lower(u.email) = lower(:email)
          AND t.token_type = 'email_verification'
        """,
        {"email": email},
    )
    assert token_count["count"] == 0

    assert auth_env.sender.messages == []
    user = auth_env.one(
        "SELECT id, email_verified_at, revision FROM users "
        "WHERE lower(email) = :email",
        {"email": email},
    )
    assert user is not None
    assert user["email_verified_at"] is not None
    assert user["revision"] == 1
    tokens = auth_env.one(
        "SELECT count(*) AS count FROM auth_action_tokens "
        "WHERE user_id = :user_id AND token_type = 'email_verification'",
        {"user_id": user["id"]},
    )
    assert tokens["count"] == 0


def test_disabled_verification_allows_existing_unverified_login(
    auth_env: AuthEnv,
    monkeypatch: pytest.MonkeyPatch,
):
    email = new_email()

    async def scenario():
        async with auth_env.client() as client:
            registered, _ = await register(client, email)
            assert registered.status_code == 201
            assert registered.json()["data"]["verification_status"] == "required"

            blocked = await login(client, email)
            assert blocked.status_code == 403
            assert blocked.json()["error"]["code"] == "email_verification_required"

            monkeypatch.setattr(settings, "require_email_verification", False)

            allowed = await login(client, email)
            assert allowed.status_code == 200, allowed.text
            data = allowed.json()["data"]
            assert data["user"]["email_verified"] is False

            me = await client.get(
                "/api/v1/auth/me",
                headers=headers(token=data["access_token"]),
            )
            assert me.status_code == 200
            assert me.json()["data"]["email_verified"] is False

    asyncio.run(scenario())

    user = auth_env.one(
        "SELECT email_verified_at FROM users WHERE lower(email) = :email",
        {"email": email},
    )
    assert user is not None
    assert user["email_verified_at"] is None


def test_login_failures_are_generic(auth_env: AuthEnv):
    email = new_email()

    async def scenario():
        async with auth_env.client() as client:
            await register_verify_login(client, auth_env, email)

            wrong_password = await login(client, email, password="wrong password")
            assert wrong_password.status_code == 401
            assert wrong_password.json()["error"]["code"] == "authentication_failed"

            unknown = await login(client, new_email())
            assert unknown.status_code == 401
            assert unknown.json()["error"]["code"] == "authentication_failed"
            assert (
                unknown.json()["error"]["message"]
                == wrong_password.json()["error"]["message"]
            )
            assert "finpath_refresh" not in unknown.headers.get("set-cookie", "")

    asyncio.run(scenario())


def test_suspended_and_closed_accounts_are_blocked(auth_env: AuthEnv):
    email = new_email()

    async def scenario():
        async with auth_env.client() as client:
            _, data = await register_verify_login(client, auth_env, email)
            access_token = data["access_token"]
            csrf_token = data["csrf_token"]
            refresh_token = client.cookies.get(settings.refresh_cookie_name)

            auth_env.execute(
                "UPDATE users SET status = 'suspended' WHERE lower(email) = :email",
                {"email": email},
            )

            suspended_login = await login(client, email)
            assert suspended_login.status_code == 403
            assert suspended_login.json()["error"]["code"] == "account_suspended"
            assert "access_token" not in suspended_login.text

            suspended_refresh = await client.post(
                "/api/v1/auth/refresh",
                headers=headers(csrf=csrf_token),
            )
            assert suspended_refresh.status_code == 403
            assert (
                suspended_refresh.json()["error"]["code"] == "account_suspended"
            )

            suspended_me = await client.get(
                "/api/v1/auth/me",
                headers=headers(token=access_token),
            )
            assert suspended_me.status_code == 403
            assert suspended_me.json()["error"]["code"] == "account_suspended"

            auth_env.execute(
                "UPDATE users SET status = 'closed' WHERE lower(email) = :email",
                {"email": email},
            )
            closed_login = await login(client, email)
            assert closed_login.status_code == 403
            assert closed_login.json()["error"]["code"] == "account_closed"
            closed_me = await client.get(
                "/api/v1/auth/me",
                headers=headers(token=access_token),
            )
            assert closed_me.status_code == 403
            assert closed_me.json()["error"]["code"] == "account_closed"
            assert refresh_token

    asyncio.run(scenario())


def test_access_token_authentication_and_spoofing(
    auth_env: AuthEnv,
    monkeypatch: pytest.MonkeyPatch,
):
    email = new_email()

    async def scenario():
        async with auth_env.client() as client:
            _, data = await register_verify_login(client, auth_env, email)
            access_token = data["access_token"]
            user_id = data["user"]["id"]
            payload = security.decode_access_token(access_token)
            session_id = UUID(payload["sid"])

            me = await client.get(
                "/api/v1/auth/me",
                headers=headers(token=access_token),
            )
            assert me.status_code == 200
            assert me.json()["data"]["id"] == user_id
            assert me.json()["data"]["roles"] == ["user"]

            spoofed = await client.get(
                "/api/v1/auth/me",
                headers=headers(
                    token=access_token,
                    extra={"X-User-ID": str(uuid4())},
                ),
            )
            assert spoofed.status_code == 200
            assert spoofed.json()["data"]["id"] == user_id

            missing = await client.get("/api/v1/auth/me")
            assert missing.status_code == 401
            assert missing.json()["error"]["code"] == "authentication_required"

            tampered = await client.get(
                "/api/v1/auth/me",
                headers=headers(token=access_token + "x"),
            )
            assert tampered.status_code == 401

            original_secret = settings.access_token_secret
            settings.access_token_secret = SecretStr(
                "wrong-secret-value-with-enough-entropy"
            )
            try:
                wrong_secret = security.create_access_token(
                    user_id=UUID(user_id),
                    session_id=session_id,
                )
            finally:
                settings.access_token_secret = original_secret
            rejected = await client.get(
                "/api/v1/auth/me",
                headers=headers(token=wrong_secret),
            )
            assert rejected.status_code == 401

            monkeypatch.setattr(settings, "access_token_ttl_seconds", -1)
            expired = security.create_access_token(
                user_id=UUID(user_id),
                session_id=session_id,
            )
            monkeypatch.setattr(settings, "access_token_ttl_seconds", 900)
            expired_response = await client.get(
                "/api/v1/auth/me",
                headers=headers(token=expired),
            )
            assert expired_response.status_code == 401

    asyncio.run(scenario())


def test_refresh_rotation_and_reuse_detection(auth_env: AuthEnv):
    email = new_email()

    async def scenario():
        async with auth_env.client() as client:
            _, data = await register_verify_login(client, auth_env, email)
            old_refresh = client.cookies.get(settings.refresh_cookie_name)
            old_csrf = data["csrf_token"]
            old_access = data["access_token"]
            assert old_refresh

            rotated = await client.post(
                "/api/v1/auth/refresh",
                headers=headers(csrf=old_csrf),
            )
            assert rotated.status_code == 200, rotated.text
            new_data = rotated.json()["data"]
            new_refresh = client.cookies.get(settings.refresh_cookie_name)
            assert new_refresh and new_refresh != old_refresh
            assert new_data["csrf_token"]
            assert new_data["access_token"] != old_access

            old_access_response = await client.get(
                "/api/v1/auth/me",
                headers=headers(token=old_access),
            )
            assert old_access_response.status_code == 401

            async with auth_env.client(
                cookies={settings.refresh_cookie_name: old_refresh}
            ) as replay_client:
                reuse = await replay_client.post(
                    "/api/v1/auth/refresh",
                    headers=headers(csrf=old_csrf),
                )
                assert reuse.status_code == 401
                assert (
                    reuse.json()["error"]["code"] == "authentication_required"
                )

            async with auth_env.client(
                cookies={settings.refresh_cookie_name: new_refresh}
            ) as new_client:
                revoked = await new_client.post(
                    "/api/v1/auth/refresh",
                    headers=headers(csrf=new_data["csrf_token"]),
                )
                assert revoked.status_code == 401

    asyncio.run(scenario())


def test_logout_revokes_session_and_is_repeatable(auth_env: AuthEnv):
    email = new_email()

    async def scenario():
        async with auth_env.client() as client:
            _, data = await register_verify_login(client, auth_env, email)
            refresh_token = client.cookies.get(settings.refresh_cookie_name)
            csrf_token = data["csrf_token"]

            logged_out = await client.post(
                "/api/v1/auth/logout",
                headers=headers(csrf=csrf_token),
            )
            assert logged_out.status_code == 204
            assert "finpath_refresh" in logged_out.headers.get("set-cookie", "")
            assert client.cookies.get(settings.refresh_cookie_name) in (None, "")

            async with auth_env.client(
                cookies={settings.refresh_cookie_name: refresh_token}
            ) as replay_client:
                rejected = await replay_client.post(
                    "/api/v1/auth/refresh",
                    headers=headers(csrf=csrf_token),
                )
                assert rejected.status_code == 401

            repeated = await client.post(
                "/api/v1/auth/logout",
                headers=headers(
                    csrf=csrf_token,
                    extra={"Cookie": f"{settings.refresh_cookie_name}={refresh_token}"},
                ),
            )
            assert repeated.status_code == 204

    asyncio.run(scenario())


def test_expired_and_inactive_sessions_are_rejected(auth_env: AuthEnv):
    email = new_email()

    async def scenario():
        async with auth_env.client() as client:
            _, data = await register_verify_login(client, auth_env, email)
            auth_env.execute(
                "UPDATE auth_sessions SET refresh_expires_at = now() - interval '1 minute' "
                "WHERE user_id = (SELECT id FROM users WHERE lower(email) = :email)",
                {"email": email},
            )
            expired = await client.post(
                "/api/v1/auth/refresh",
                headers=headers(csrf=data["csrf_token"]),
            )
            assert expired.status_code == 401

            expired_access = await client.get(
                "/api/v1/auth/me",
                headers=headers(token=data["access_token"]),
            )
            assert expired_access.status_code == 401

            inactive_login = await login(client, email)
            inactive_csrf = inactive_login.json()["data"]["csrf_token"]
            inactive_refresh = client.cookies.get(settings.refresh_cookie_name)
            assert inactive_refresh
            auth_env.execute(
                "UPDATE auth_sessions SET created_at = now() - interval '8 days', "
                "last_refreshed_at = NULL "
                "WHERE user_id = (SELECT id FROM users WHERE lower(email) = :email)",
                {"email": email},
            )
            async with auth_env.client(
                cookies={settings.refresh_cookie_name: inactive_refresh}
            ) as inactive_client:
                inactive = await inactive_client.post(
                    "/api/v1/auth/refresh",
                    headers=headers(csrf=inactive_csrf),
                )
            assert inactive.status_code == 401
            inactive_session_id = UUID(
                security.decode_access_token(
                    inactive_login.json()["data"]["access_token"]
                )["sid"]
            )
            inactive_row = auth_env.one(
                "SELECT revoked_at FROM auth_sessions WHERE id = :session_id",
                {"session_id": inactive_session_id},
            )
            assert inactive_row["revoked_at"] is not None

    asyncio.run(scenario())


def test_concurrent_refresh_cannot_rotate_twice(auth_env: AuthEnv):
    email = new_email()

    async def scenario():
        async with auth_env.client() as client:
            _, data = await register_verify_login(client, auth_env, email)
            refresh_token = client.cookies.get(settings.refresh_cookie_name)
            csrf_token = data["csrf_token"]

        async def attempt() -> httpx.Response:
            async with auth_env.client(
                cookies={settings.refresh_cookie_name: refresh_token}
            ) as competing:
                return await competing.post(
                    "/api/v1/auth/refresh",
                    headers=headers(csrf=csrf_token),
                )

        first, second = await asyncio.gather(attempt(), attempt())
        statuses = sorted([first.status_code, second.status_code])
        assert statuses == [200, 401], (first.text, second.text)

        winner = first if first.status_code == 200 else second
        winner_data = winner.json()["data"]
        new_refresh = winner.cookies.get(settings.refresh_cookie_name)
        assert new_refresh

        async with auth_env.client(
            cookies={settings.refresh_cookie_name: new_refresh}
        ) as replay_client:
            revoked = await replay_client.post(
                "/api/v1/auth/refresh",
                headers=headers(csrf=winner_data["csrf_token"]),
            )
            assert revoked.status_code == 401

    asyncio.run(scenario())

    audit = auth_env.one(
        "SELECT count(*) AS count FROM admin_audit_logs "
        "WHERE action = 'refresh_token_reuse'"
    )
    assert audit["count"] >= 1


def test_password_reset_without_email_provider_creates_no_token(
    auth_env: AuthEnv,
):
    email = new_email()

    async def scenario():
        async with auth_env.client() as client:
            await register_verify_login(client, auth_env, email)
            app.dependency_overrides[get_email_sender] = (
                lambda: UnconfiguredEmailSender()
            )
            message_count = len(auth_env.sender.messages)

            requested = await client.post(
                "/api/v1/auth/password-reset-requests",
                json={"email": email},
                headers=headers(),
            )

            assert requested.status_code == 202
            assert requested.json() == {"data": {"status": "accepted"}}
            assert "token" not in requested.text.lower()
            assert len(auth_env.sender.messages) == message_count

    asyncio.run(scenario())

    token_count = auth_env.one(
        """
        SELECT count(*) AS count
        FROM auth_action_tokens t
        JOIN users u ON u.id = t.user_id
        WHERE lower(u.email) = lower(:email)
          AND t.token_type = 'password_reset'
        """,
        {"email": email},
    )
    assert token_count["count"] == 0


def test_password_reset_delivery_failure_stores_no_token(
    auth_env: AuthEnv,
):
    email = new_email()

    async def scenario():
        async with auth_env.client() as client:
            await register_verify_login(client, auth_env, email)
            auth_env.sender.fail = True
            message_count = len(auth_env.sender.messages)

            requested = await client.post(
                "/api/v1/auth/password-reset-requests",
                json={"email": email},
                headers=headers(),
            )

            assert requested.status_code == 202
            assert requested.json() == {"data": {"status": "accepted"}}
            assert len(auth_env.sender.messages) == message_count

    asyncio.run(scenario())

    token_count = auth_env.one(
        """
        SELECT count(*) AS count
        FROM auth_action_tokens t
        JOIN users u ON u.id = t.user_id
        WHERE lower(u.email) = lower(:email)
          AND t.token_type = 'password_reset'
        """,
        {"email": email},
    )
    assert token_count["count"] == 0


def test_password_reset_flow_revokes_sessions(auth_env: AuthEnv):
    email = new_email()

    async def scenario():
        async with auth_env.client() as client:
            _, data = await register_verify_login(client, auth_env, email)
            old_refresh = client.cookies.get(settings.refresh_cookie_name)

            requested = await client.post(
                "/api/v1/auth/password-reset-requests",
                json={"email": email},
                headers=headers(),
            )
            assert requested.status_code == 202
            assert requested.json() == {"data": {"status": "accepted"}}
            reset_token = auth_env.sender.last_token(email, "password_reset")

            key = new_key("reset")
            consumed = await client.post(
                "/api/v1/auth/password-reset/consume",
                json={"token": reset_token, "new_password": NEW_PASSWORD},
                headers=headers(idem=key),
            )
            assert consumed.status_code == 200, consumed.text
            assert consumed.json() == {"data": {"password_reset": True}}

            old_password = await login(client, email)
            assert old_password.status_code == 401
            new_password = await login(client, email, password=NEW_PASSWORD)
            assert new_password.status_code == 200

            async with auth_env.client(
                cookies={settings.refresh_cookie_name: old_refresh}
            ) as replay_client:
                revoked = await replay_client.post(
                    "/api/v1/auth/refresh",
                    headers=headers(csrf=data["csrf_token"]),
                )
                assert revoked.status_code == 401

            replayed = await client.post(
                "/api/v1/auth/password-reset/consume",
                json={"token": reset_token, "new_password": NEW_PASSWORD},
                headers=headers(idem=key),
            )
            assert replayed.status_code == 200
            reused = await client.post(
                "/api/v1/auth/password-reset/consume",
                json={"token": reset_token, "new_password": NEW_PASSWORD},
                headers=headers(idem=new_key("reset")),
            )
            assert reused.status_code == 422

            message_count = len(auth_env.sender.messages)
            unknown = await client.post(
                "/api/v1/auth/password-reset-requests",
                json={"email": new_email()},
                headers=headers(),
            )
            assert unknown.status_code == 202
            assert len(auth_env.sender.messages) == message_count

            unverified_email = new_email()
            await register(client, unverified_email)
            unverified_message_count = len(auth_env.sender.messages)
            unverified = await client.post(
                "/api/v1/auth/password-reset-requests",
                json={"email": unverified_email},
                headers=headers(),
            )
            assert unverified.status_code == 202
            assert len(auth_env.sender.messages) == unverified_message_count

    asyncio.run(scenario())


def test_change_password(auth_env: AuthEnv):
    email = new_email()

    async def scenario():
        async with auth_env.client() as client:
            _, data = await register_verify_login(client, auth_env, email)
            changed = await client.post(
                "/api/v1/auth/change-password",
                json={
                    "current_password": PASSWORD,
                    "new_password": NEW_PASSWORD,
                },
                headers=headers(token=data["access_token"]),
            )
            assert changed.status_code == 204

            old_password = await login(client, email)
            assert old_password.status_code == 401
            new_password = await login(client, email, password=NEW_PASSWORD)
            assert new_password.status_code == 200

    asyncio.run(scenario())


def test_origin_and_csrf_protection(auth_env: AuthEnv):
    email = new_email()

    async def scenario():
        async with auth_env.client() as client:
            await register_verify_login(client, auth_env, email)

            missing_origin = await login(client, email, origin=None)
            assert missing_origin.status_code == 403
            assert missing_origin.json()["error"]["code"] == "origin_not_allowed"

            bad_origin = await login(
                client, email, origin="https://attacker.example"
            )
            assert bad_origin.status_code == 403

            logged_in = await login(client, email)
            data = logged_in.json()["data"]

            no_csrf = await client.post(
                "/api/v1/auth/refresh",
                headers=headers(),
            )
            assert no_csrf.status_code == 403
            assert no_csrf.json()["error"]["code"] == "csrf_invalid"

            bad_csrf = await client.post(
                "/api/v1/auth/refresh",
                headers=headers(csrf="not-a-csrf-token"),
            )
            assert bad_csrf.status_code == 403

            csrf_response = await client.get(
                "/api/v1/auth/csrf",
                headers=headers(),
            )
            assert csrf_response.status_code == 200
            assert csrf_response.json()["data"]["csrf_token"]

            csrf_without_origin = await client.get(
                "/api/v1/auth/csrf",
                headers=headers(origin=None),
            )
            assert csrf_without_origin.status_code == 403
            assert data["csrf_token"]

    asyncio.run(scenario())


def test_registration_survives_email_delivery_failure(auth_env: AuthEnv):
    email = new_email()
    auth_env.sender.fail = True

    async def scenario():
        async with auth_env.client() as client:
            response, _ = await register(client, email)
            assert response.status_code == 201
            assert response.json()["data"]["verification_status"] == "required"

    asyncio.run(scenario())

    user = auth_env.one(
        "SELECT id FROM users WHERE lower(email) = :email",
        {"email": email},
    )
    assert user is not None
    token = auth_env.one(
        "SELECT count(*) AS count FROM auth_action_tokens "
        "WHERE user_id = :user_id AND token_type = 'email_verification'",
        {"user_id": user["id"]},
    )
    assert token["count"] == 1
    assert auth_env.sender.messages == []
