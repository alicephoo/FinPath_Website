"""Safety helpers for PostgreSQL tests.

PostgreSQL tests run against a dedicated test database configured through
``FINPATH_TEST_DATABASE_URL`` (runtime role),
``FINPATH_TEST_MIGRATION_DATABASE_URL`` (migration role), and
``FINPATH_TEST_REGISTRATION_DATABASE_URL`` (registration role). They never
fall back to the development database.

This module deliberately does not create or drop databases. Provisioning is a
one-time administrative step documented in ``backend/README.md``.
"""

from __future__ import annotations

import pytest
from sqlalchemy.engine import make_url

from app.core.config import MigrationSettings, PostgresTestSettings, Settings

SETUP_INSTRUCTIONS = (
    "PostgreSQL tests require a dedicated test database. Provision it once as a "
    "PostgreSQL superuser:\n"
    "  CREATE ROLE finpath_registration LOGIN PASSWORD '<random>' "
    "NOSUPERUSER NOCREATEDB NOCREATEROLE NOREPLICATION NOBYPASSRLS;\n"
    "  CREATE DATABASE finpath_test OWNER finpath_migrator;\n"
    "  GRANT CONNECT ON DATABASE finpath_test TO finpath_app, "
    "finpath_registration;\n"
    "Then set all three variables in backend/.env:\n"
    "  FINPATH_TEST_DATABASE_URL=postgresql+psycopg://finpath_app@127.0.0.1:5432/finpath_test\n"
    "  FINPATH_TEST_MIGRATION_DATABASE_URL=postgresql+psycopg://finpath_migrator@127.0.0.1:5432/finpath_test\n"
    "  FINPATH_TEST_REGISTRATION_DATABASE_URL=postgresql+psycopg://finpath_registration@127.0.0.1:5432/finpath_test\n"
    "The test database name must differ from the development database name."
)

SYSTEM_DATABASE_NAMES = frozenset({"postgres", "template0", "template1"})


def database_name(url: str) -> str:
    """Return the database name from a SQLAlchemy URL without exposing secrets."""
    name = make_url(url).database
    if not name:
        raise ValueError("A PostgreSQL test URL must include a database name.")
    return name


def validate_test_database_urls(
    *,
    test_runtime_url: str,
    test_migration_url: str,
    development_runtime_url: str,
    development_migration_url: str,
) -> str:
    """Reject unsafe test targets and return the validated test database name.

    Raises:
        ValueError: if the two test URLs disagree, target a system database,
            or target the development database.
    """
    test_names = {
        database_name(test_runtime_url),
        database_name(test_migration_url),
    }
    if len(test_names) != 1:
        raise ValueError(
            "FINPATH_TEST_DATABASE_URL and FINPATH_TEST_MIGRATION_DATABASE_URL "
            "must reference the same test database."
        )

    test_name = test_names.pop()
    if test_name in SYSTEM_DATABASE_NAMES:
        raise ValueError(
            f"Refusing to run PostgreSQL tests against system database {test_name!r}."
        )

    development_names = {
        database_name(development_runtime_url),
        database_name(development_migration_url),
    }
    if test_name in development_names:
        raise ValueError(
            "Refusing to run PostgreSQL tests against the development database "
            f"{test_name!r}. Configure a dedicated test database instead."
        )

    return test_name


def validate_test_registration_url(
    *,
    test_registration_url: str,
    test_database_name: str,
    development_registration_url: str,
    development_runtime_url: str,
    development_migration_url: str,
) -> str:
    """Validate the test registration URL and return its database name."""
    registration_name = database_name(test_registration_url)
    if registration_name in SYSTEM_DATABASE_NAMES:
        raise ValueError(
            "Refusing to use a system database for registration tests."
        )
    if registration_name != test_database_name:
        raise ValueError(
            "FINPATH_TEST_REGISTRATION_DATABASE_URL must reference the same "
            "test database as the other test URLs."
        )
    development_names = {
        database_name(development_registration_url),
        database_name(development_runtime_url),
        database_name(development_migration_url),
    }
    if registration_name in development_names:
        raise ValueError(
            "Refusing to run registration tests against the development database "
            f"{registration_name!r}."
        )
    return registration_name


def require_test_database_urls() -> tuple[str, str, str]:
    """Return ``(runtime_url, migration_url, database_name)`` for tests.

    Skips the calling test when the test database is not configured. Raises
    ``ValueError`` when the configured target is unsafe.
    """
    settings = PostgresTestSettings()
    if settings.database_url is None or settings.migration_database_url is None:
        pytest.skip(
            "FINPATH_TEST_DATABASE_URL and "
            "FINPATH_TEST_MIGRATION_DATABASE_URL are not configured. "
            + SETUP_INSTRUCTIONS
        )

    test_runtime_url = settings.database_url.get_secret_value()
    test_migration_url = settings.migration_database_url.get_secret_value()
    test_name = validate_test_database_urls(
        test_runtime_url=test_runtime_url,
        test_migration_url=test_migration_url,
        development_runtime_url=Settings().database_url.get_secret_value(),
        development_migration_url=MigrationSettings().database_url.get_secret_value(),
    )
    return test_runtime_url, test_migration_url, test_name


def require_test_registration_url() -> tuple[str, str]:
    """Return ``(registration_url, database_name)`` for registration tests."""
    settings = PostgresTestSettings()
    if settings.registration_database_url is None:
        pytest.skip(
            "FINPATH_TEST_REGISTRATION_DATABASE_URL is not configured. "
            + SETUP_INSTRUCTIONS
        )
    test_runtime_url, _, test_name = require_test_database_urls()
    registration_url = settings.registration_database_url.get_secret_value()
    validate_test_registration_url(
        test_registration_url=registration_url,
        test_database_name=test_name,
        development_registration_url=Settings()
        .registration_database_url.get_secret_value(),
        development_runtime_url=Settings().database_url.get_secret_value(),
        development_migration_url=MigrationSettings().database_url.get_secret_value(),
    )
    return registration_url, test_name
