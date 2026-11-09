"""PostgreSQL fixture shared by the Transactions API test modules."""

from __future__ import annotations

import pytest
from pydantic import SecretStr
from sqlalchemy import create_engine, text
from sqlalchemy.orm import Session, sessionmaker

from app.core.config import settings
from app.db.session import get_db, get_registration_db
from app.main import app
from app.services.email import get_email_sender
from tests.pg_test_env import require_test_database_urls, require_test_registration_url
from tests.tx_support import ORIGIN, RecordingSender, TransactionEnv


@pytest.fixture
def tx_env(monkeypatch: pytest.MonkeyPatch):
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
        revision = connection.execute(
            text("SELECT version_num FROM alembic_version")
        ).scalar_one()
        assert revision == "c4f7a1b9e2d6"
        category_count = connection.execute(
            text("SELECT count(*) FROM transaction_categories")
        ).scalar_one()
        assert category_count == 11

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
    monkeypatch.setattr(settings, "max_financial_entry_kyat", 1_000_000_000)
    # Tests are independent of the local .env email-verification setting.
    monkeypatch.setattr(settings, "require_email_verification", True)

    env = TransactionEnv(
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
            user_filter = (
                "SELECT id FROM users "
                "WHERE email LIKE 'tx-test-%@example.test'"
            )
            # Immutable-financial-fact triggers intentionally reject DELETE for
            # every identity. Test cleanup temporarily disables only those
            # triggers as the table owner so isolated test rows can be removed.
            protected_triggers = (
                (
                    "trg_loan_repayments_immutable_facts",
                    "loan_repayments",
                ),
                (
                    "trg_loan_repayment_corrections_append_only",
                    "loan_repayment_corrections",
                ),
                (
                    "trg_savings_entries_immutable_facts",
                    "savings_entries",
                ),
            )
            for trigger_name, table_name in protected_triggers:
                connection.execute(
                    text(
                        f"ALTER TABLE {table_name} "
                        f"DISABLE TRIGGER {trigger_name}"
                    )
                )
            try:
                for table in (
                    "loan_repayment_corrections",
                    "loan_repayments",
                    "loans",
                    "savings_entries",
                    "savings_plan_days",
                    "savings_plans",
                    "savings_goals",
                    "trip_spending_entries",
                    "trip_category_budgets",
                    "trips",
                    "transactions",
                    "user_financial_settings",
                    "user_roles",
                ):
                    connection.execute(
                        text(
                            f"DELETE FROM {table} "
                            f"WHERE user_id IN ({user_filter})"
                        )
                    )
                connection.execute(
                    text(
                        "DELETE FROM idempotency_records "
                        f"WHERE owner_user_id IN ({user_filter})"
                    )
                )
                connection.execute(
                    text(
                        "DELETE FROM users "
                        "WHERE email LIKE 'tx-test-%@example.test'"
                    )
                )
            finally:
                for trigger_name, table_name in protected_triggers:
                    connection.execute(
                        text(
                            f"ALTER TABLE {table_name} "
                            f"ENABLE TRIGGER {trigger_name}"
                        )
                    )
        runtime_engine.dispose()
        registration_engine.dispose()
        migration_engine.dispose()
