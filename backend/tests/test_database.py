import asyncio
import logging

import httpx
import pytest
from fastapi import Depends, FastAPI
from pydantic import ValidationError
from sqlalchemy import create_engine, text
from sqlalchemy.exc import OperationalError
from sqlalchemy.orm import Session, sessionmaker
from sqlalchemy.pool import StaticPool

from app.api.errors import database_unavailable_handler
from app.core.config import MigrationSettings, Settings
from app.db import session as database_session
from app.db.session import DatabaseUnavailableError


def test_database_url_comes_from_environment_and_is_masked(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    database_url = "postgresql+psycopg://user:top-secret@localhost/finpath"
    monkeypatch.setenv("FINPATH_DATABASE_URL", database_url)

    configured = Settings(_env_file=None)

    assert configured.database_url.get_secret_value() == database_url
    assert "top-secret" not in repr(configured)


def test_migration_database_url_is_separate_and_required(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    migration_url = "postgresql+psycopg://finpath_migrator:top-secret@localhost/finpath"
    monkeypatch.setenv("FINPATH_MIGRATION_DATABASE_URL", migration_url)

    configured = MigrationSettings(_env_file=None)

    assert configured.database_url.get_secret_value() == migration_url
    assert "top-secret" not in repr(configured)

    monkeypatch.delenv("FINPATH_MIGRATION_DATABASE_URL")
    with pytest.raises(ValueError):
        MigrationSettings(_env_file=None)


def test_email_verification_can_be_disabled_outside_production(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("FINPATH_ENVIRONMENT", "development")
    monkeypatch.setenv("FINPATH_REQUIRE_EMAIL_VERIFICATION", "false")

    configured = Settings(_env_file=None)

    assert configured.require_email_verification is False


def _set_production_resend(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("FINPATH_EMAIL_BACKEND", "resend")
    monkeypatch.setenv("FINPATH_RESEND_API_KEY", "test-only-resend-key")
    monkeypatch.setenv("FINPATH_EMAIL_FROM", "no-reply@example.test")
    monkeypatch.setenv(
        "FINPATH_FRONTEND_ORIGINS",
        "https://finpath.example.test",
    )


def test_email_verification_cannot_be_disabled_in_production(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("FINPATH_ENVIRONMENT", "production")
    monkeypatch.setenv("FINPATH_REQUIRE_EMAIL_VERIFICATION", "false")
    monkeypatch.delenv("FINPATH_ALLOW_UNVERIFIED_REGISTRATION", raising=False)
    _set_production_resend(monkeypatch)

    with pytest.raises(ValidationError, match="cannot be disabled in production"):
        Settings(_env_file=None)


def test_unverified_registration_opt_in_defaults_to_false(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("FINPATH_ENVIRONMENT", "development")
    monkeypatch.delenv("FINPATH_ALLOW_UNVERIFIED_REGISTRATION", raising=False)

    configured = Settings(_env_file=None)

    assert configured.allow_unverified_registration is False


def test_production_requires_explicit_unverified_registration_opt_in(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("FINPATH_ENVIRONMENT", "production")
    monkeypatch.setenv("FINPATH_REQUIRE_EMAIL_VERIFICATION", "false")
    monkeypatch.delenv("FINPATH_ALLOW_UNVERIFIED_REGISTRATION", raising=False)

    with pytest.raises(ValidationError, match="FINPATH_ALLOW_UNVERIFIED_REGISTRATION"):
        Settings(_env_file=None)


def test_production_allows_disabled_verification_with_explicit_opt_in(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("FINPATH_ENVIRONMENT", "production")
    monkeypatch.setenv("FINPATH_REQUIRE_EMAIL_VERIFICATION", "false")
    monkeypatch.setenv("FINPATH_ALLOW_UNVERIFIED_REGISTRATION", "true")
    monkeypatch.setenv(
        "FINPATH_FRONTEND_ORIGINS",
        "https://finpath.example.test",
    )
    monkeypatch.delenv("FINPATH_EMAIL_BACKEND", raising=False)
    monkeypatch.delenv("FINPATH_RESEND_API_KEY", raising=False)
    monkeypatch.delenv("FINPATH_EMAIL_FROM", raising=False)
    monkeypatch.delenv("FINPATH_REFRESH_COOKIE_SECURE", raising=False)

    configured = Settings(_env_file=None)

    assert configured.require_email_verification is False
    assert configured.allow_unverified_registration is True
    assert configured.refresh_cookie_secure is True


def test_unverified_registration_flag_does_not_disable_verification(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("FINPATH_ENVIRONMENT", "development")
    monkeypatch.setenv("FINPATH_REQUIRE_EMAIL_VERIFICATION", "true")
    monkeypatch.setenv("FINPATH_ALLOW_UNVERIFIED_REGISTRATION", "true")

    configured = Settings(_env_file=None)

    assert configured.require_email_verification is True
    assert configured.allow_unverified_registration is True


def test_refresh_cookie_is_not_secure_by_default_in_development(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("FINPATH_ENVIRONMENT", "development")
    monkeypatch.delenv("FINPATH_REFRESH_COOKIE_SECURE", raising=False)

    configured = Settings(_env_file=None)

    assert configured.refresh_cookie_secure is False


def test_refresh_cookie_is_secure_by_default_in_production(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("FINPATH_ENVIRONMENT", "production")
    monkeypatch.delenv("FINPATH_REFRESH_COOKIE_SECURE", raising=False)
    _set_production_resend(monkeypatch)

    configured = Settings(_env_file=None)

    assert configured.refresh_cookie_secure is True


def test_refresh_cookie_secure_can_be_overridden_outside_production(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("FINPATH_ENVIRONMENT", "development")
    monkeypatch.setenv("FINPATH_REFRESH_COOKIE_SECURE", "true")

    configured = Settings(_env_file=None)

    assert configured.refresh_cookie_secure is True


def test_refresh_cookie_cannot_be_insecure_in_production(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("FINPATH_ENVIRONMENT", "production")
    monkeypatch.setenv("FINPATH_REFRESH_COOKIE_SECURE", "false")
    _set_production_resend(monkeypatch)

    with pytest.raises(ValidationError, match="cannot be made insecure"):
        Settings(_env_file=None)


def test_production_requires_explicit_frontend_origins(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("FINPATH_ENVIRONMENT", "production")
    _set_production_resend(monkeypatch)
    monkeypatch.delenv("FINPATH_FRONTEND_ORIGINS", raising=False)

    with pytest.raises(ValidationError, match="FINPATH_FRONTEND_ORIGINS"):
        Settings(_env_file=None)


@pytest.mark.parametrize(
    "origins",
    [
        "http://finpath.example.test",
        "https://localhost:5173",
        "https://127.0.0.1:5173",
        "https://[::1]:5173",
        "https://[::1",
        "https://finpath.example.test,http://localhost:5173",
    ],
)
def test_production_rejects_insecure_or_local_frontend_origins(
    monkeypatch: pytest.MonkeyPatch,
    origins: str,
) -> None:
    monkeypatch.setenv("FINPATH_ENVIRONMENT", "production")
    _set_production_resend(monkeypatch)
    monkeypatch.setenv("FINPATH_FRONTEND_ORIGINS", origins)

    with pytest.raises(ValidationError, match="FINPATH_FRONTEND_ORIGINS"):
        Settings(_env_file=None)


def test_production_accepts_https_non_local_frontend_origins(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("FINPATH_ENVIRONMENT", "production")
    monkeypatch.setenv(
        "FINPATH_FRONTEND_ORIGINS",
        "https://finpath.example.test",
    )
    _set_production_resend(monkeypatch)

    configured = Settings(_env_file=None)

    assert configured.frontend_origin_list == ("https://finpath.example.test",)


def test_development_keeps_localhost_frontend_origins(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("FINPATH_ENVIRONMENT", "development")
    monkeypatch.delenv("FINPATH_FRONTEND_ORIGINS", raising=False)

    configured = Settings(_env_file=None)

    assert configured.frontend_origin_list == (
        "http://localhost:5173",
        "http://127.0.0.1:5173",
    )


def test_engine_and_session_factory_use_expected_configuration() -> None:
    assert database_session.engine.url.drivername == "postgresql+psycopg"
    assert database_session.engine.hide_parameters is True
    assert database_session.engine.pool.size() == 5
    assert database_session.engine.pool._pre_ping is True
    assert database_session.engine.pool._max_overflow == 10
    assert database_session.engine.pool._timeout == 30
    assert database_session.SessionLocal.kw["autoflush"] is False
    assert database_session.SessionLocal.kw["expire_on_commit"] is False


def test_database_dependency_yields_and_closes_session(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    sessions: list[Session] = []

    class TrackingSession(Session):
        was_closed = False

        def __init__(self, **kwargs: object) -> None:
            super().__init__(**kwargs)
            sessions.append(self)

        def close(self) -> None:
            self.was_closed = True
            super().close()

    sqlite_engine = create_engine(
        "sqlite+pysqlite:///:memory:",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    test_session_factory = sessionmaker(
        bind=sqlite_engine,
        class_=TrackingSession,
        autoflush=False,
        expire_on_commit=False,
    )
    monkeypatch.setattr(database_session, "SessionLocal", test_session_factory)

    app = FastAPI()

    @app.get("/db-test")
    def db_test(db: Session = Depends(database_session.get_db)) -> dict[str, int]:
        return {"result": db.execute(text("SELECT 1")).scalar_one()}

    async def request() -> httpx.Response:
        async with httpx.AsyncClient(
            transport=httpx.ASGITransport(app=app),
            base_url="http://test",
        ) as client:
            return await client.get("/db-test")

    response = asyncio.run(request())

    assert response.status_code == 200
    assert response.json() == {"result": 1}
    assert len(sessions) == 1
    assert sessions[0].was_closed is True
    sqlite_engine.dispose()


def test_operational_error_returns_safe_503_and_closes_session(
    monkeypatch: pytest.MonkeyPatch,
    caplog: pytest.LogCaptureFixture,
) -> None:
    class UnavailableSession:
        was_closed = False

        def execute(self, statement: object) -> None:
            raise OperationalError(
                "SELECT 1",
                {},
                RuntimeError("password=top-secret"),
            )

        def close(self) -> None:
            self.was_closed = True

    unavailable_session = UnavailableSession()
    monkeypatch.setattr(
        database_session,
        "SessionLocal",
        lambda: unavailable_session,
    )

    app = FastAPI()
    app.add_exception_handler(
        DatabaseUnavailableError,
        database_unavailable_handler,
    )

    @app.get("/db-test")
    def db_test(
        db: Session = Depends(database_session.get_db),
    ) -> dict[str, str]:
        db.execute(text("SELECT 1"))
        return {"status": "ok"}

    async def request() -> httpx.Response:
        async with httpx.AsyncClient(
            transport=httpx.ASGITransport(app=app),
            base_url="http://test",
        ) as client:
            return await client.get("/db-test")

    with caplog.at_level(logging.WARNING, logger="app.db.session"):
        response = asyncio.run(request())

    assert response.status_code == 503
    error = response.json()["error"]
    assert error["code"] == "database_unavailable"
    assert error["message"] == "The database is temporarily unavailable."
    assert error["field_errors"] == []
    assert error["request_id"]
    assert "top-secret" not in response.text
    assert "top-secret" not in caplog.text
    assert unavailable_session.was_closed is True
