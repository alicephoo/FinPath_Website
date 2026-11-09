from __future__ import annotations

import pytest

from tests.pg_test_env import database_name, validate_test_database_urls

DEVELOPMENT_RUNTIME_URL = "postgresql+psycopg://finpath_app@127.0.0.1:5432/finpath"
DEVELOPMENT_MIGRATION_URL = (
    "postgresql+psycopg://finpath_migrator@127.0.0.1:5432/finpath"
)


def _validate(runtime_url: str, migration_url: str) -> str:
    return validate_test_database_urls(
        test_runtime_url=runtime_url,
        test_migration_url=migration_url,
        development_runtime_url=DEVELOPMENT_RUNTIME_URL,
        development_migration_url=DEVELOPMENT_MIGRATION_URL,
    )


def test_database_name_ignores_credentials_and_host_details() -> None:
    assert (
        database_name(
            "postgresql+psycopg://someone:secret@127.0.0.1:5432/finpath_test"
        )
        == "finpath_test"
    )


def test_database_name_requires_a_name() -> None:
    with pytest.raises(ValueError):
        database_name("postgresql+psycopg://finpath_app@127.0.0.1:5432/")


def test_dedicated_test_database_is_accepted() -> None:
    assert (
        _validate(
            "postgresql+psycopg://finpath_app@127.0.0.1:5432/finpath_test",
            "postgresql+psycopg://finpath_migrator@127.0.0.1:5432/finpath_test",
        )
        == "finpath_test"
    )


def test_development_database_is_rejected() -> None:
    with pytest.raises(ValueError, match="development database"):
        _validate(DEVELOPMENT_RUNTIME_URL, DEVELOPMENT_MIGRATION_URL)


def test_system_database_is_rejected() -> None:
    with pytest.raises(ValueError, match="system database"):
        _validate(
            "postgresql+psycopg://finpath_app@127.0.0.1:5432/postgres",
            "postgresql+psycopg://finpath_migrator@127.0.0.1:5432/postgres",
        )


def test_mismatched_test_databases_are_rejected() -> None:
    with pytest.raises(ValueError, match="same test database"):
        _validate(
            "postgresql+psycopg://finpath_app@127.0.0.1:5432/finpath_test",
            "postgresql+psycopg://finpath_migrator@127.0.0.1:5432/finpath_other",
        )


def test_test_registration_url_must_match_test_database() -> None:
    from tests.pg_test_env import validate_test_registration_url

    with pytest.raises(ValueError, match="same test database"):
        validate_test_registration_url(
            test_registration_url=(
                "postgresql+psycopg://finpath_registration@127.0.0.1:5432/finpath_other"
            ),
            test_database_name="finpath_test",
            development_registration_url=(
                "postgresql+psycopg://finpath_registration@127.0.0.1:5432/finpath"
            ),
            development_runtime_url=DEVELOPMENT_RUNTIME_URL,
            development_migration_url=DEVELOPMENT_MIGRATION_URL,
        )


def test_development_registration_database_is_rejected() -> None:
    from tests.pg_test_env import validate_test_registration_url

    with pytest.raises(ValueError, match="development database"):
        validate_test_registration_url(
            test_registration_url=(
                "postgresql+psycopg://finpath_registration@127.0.0.1:5432/finpath"
            ),
            test_database_name="finpath",
            development_registration_url=(
                "postgresql+psycopg://finpath_registration@127.0.0.1:5432/finpath"
            ),
            development_runtime_url=DEVELOPMENT_RUNTIME_URL,
            development_migration_url=DEVELOPMENT_MIGRATION_URL,
        )


def test_dedicated_test_registration_database_is_accepted() -> None:
    from tests.pg_test_env import validate_test_registration_url

    assert (
        validate_test_registration_url(
            test_registration_url=(
                "postgresql+psycopg://finpath_registration@127.0.0.1:5432/finpath_test"
            ),
            test_database_name="finpath_test",
            development_registration_url=(
                "postgresql+psycopg://finpath_registration@127.0.0.1:5432/finpath"
            ),
            development_runtime_url=DEVELOPMENT_RUNTIME_URL,
            development_migration_url=DEVELOPMENT_MIGRATION_URL,
        )
        == "finpath_test"
    )
