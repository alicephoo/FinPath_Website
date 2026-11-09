from __future__ import annotations

import importlib.util
from datetime import date, datetime, timezone
from pathlib import Path
from types import ModuleType
from uuid import uuid4

import pytest
from sqlalchemy import Connection, create_engine, text
from sqlalchemy.exc import DBAPIError, IntegrityError
from sqlalchemy.engine import Engine

from app.db.base import Base
import app.models
from tests.pg_test_env import require_test_database_urls

BACKEND_DIR = Path(__file__).resolve().parents[1]
MIGRATION_PATH = (
    BACKEND_DIR
    / "migrations"
    / "versions"
    / "7270e464aca9_initial_schema.py"
)


def _load_migration() -> ModuleType:
    spec = importlib.util.spec_from_file_location(
        "finpath_initial_schema_for_protection_tests",
        MIGRATION_PATH,
    )
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def _public_schema_fingerprint(connection: Connection) -> tuple[int, ...]:
    """Snapshot the public schema so tests can prove they never changed it.

    The test database may or may not have the initial migration applied, so
    this compares before/after state instead of requiring an empty schema.
    """
    return tuple(
        connection.execute(
            text(
                """
            SELECT
                (SELECT count(*) FROM pg_tables WHERE schemaname = 'public'),
                (
                    SELECT count(*)
                    FROM pg_class AS relation
                    JOIN pg_namespace AS namespace
                      ON namespace.oid = relation.relnamespace
                    WHERE namespace.nspname = 'public'
                      AND relation.relkind = 'S'
                ),
                (
                    SELECT count(*)
                    FROM pg_trigger AS trigger
                    JOIN pg_class AS relation
                      ON relation.oid = trigger.tgrelid
                    JOIN pg_namespace AS namespace
                      ON namespace.oid = relation.relnamespace
                    WHERE namespace.nspname = 'public'
                      AND NOT trigger.tgisinternal
                ),
                (
                    SELECT count(*)
                    FROM information_schema.tables
                    WHERE table_schema = 'public'
                      AND table_name = 'alembic_version'
                )
            """
            )
        ).one()
    )


@pytest.fixture
def protection_db():
    _, migration_url, test_database = require_test_database_urls()
    engine: Engine = create_engine(
        migration_url,
        hide_parameters=True,
        pool_pre_ping=True,
    )
    schema = f"finpath_protection_test_{uuid4().hex}"
    migration = _load_migration()

    schema_created = False
    public_before: tuple[int, ...] | None = None
    try:
        with engine.connect() as setup:
            assert setup.execute(text("SELECT current_user")).scalar_one() == (
                "finpath_migrator"
            )
            version = setup.execute(text("SELECT version()")).scalar_one()
            assert version.startswith("PostgreSQL 17."), version.split(",")[0]
            assert setup.execute(
                text("SELECT current_database()")
            ).scalar_one() == test_database
            assert len(Base.metadata.tables) == 20
            public_before = _public_schema_fingerprint(setup)

            setup.execute(text(f'CREATE SCHEMA "{schema}"'))
            setup.commit()
            schema_created = True
            try:
                translated = setup.execution_options(
                    schema_translate_map={None: schema}
                )
                Base.metadata.create_all(translated)
                for statement in migration.PROTECTION_FUNCTION_DDL:
                    setup.execute(
                        text(statement.replace("public.", f'"{schema}".'))
                    )
                for statement in migration.PROTECTION_TRIGGER_DDL:
                    setup.execute(
                        text(statement.replace("public.", f'"{schema}".'))
                    )
                setup.commit()
            finally:
                if setup.in_transaction():
                    setup.rollback()

        with engine.connect() as connection:
            transaction = connection.begin()
            try:
                yield connection, schema
            finally:
                if transaction.is_active:
                    transaction.rollback()
    finally:
        if schema_created:
            with engine.begin() as cleanup:
                cleanup.execute(text(f'DROP SCHEMA "{schema}" CASCADE'))
                if public_before is not None:
                    assert _public_schema_fingerprint(cleanup) == public_before, (
                        "The public schema changed during a protection test"
                    )
        engine.dispose()


def _qualified(schema: str, table: str) -> str:
    return f'"{schema}"."{table}"'


def _insert_user(
    connection: Connection,
    schema: str,
    user_id,
    *,
    email: str | None = None,
    status: str = "active",
) -> None:
    connection.execute(
        text(
            f"""
            INSERT INTO {_qualified(schema, "users")}
                (id, email, password_hash, status, timezone)
            VALUES (:id, :email, 'test-hash', :status, 'Asia/Yangon')
            """
        ),
        {
            "id": user_id,
            "email": email or f"{user_id}@example.test",
            "status": status,
        },
    )


def _insert_loan(
    connection: Connection,
    schema: str,
    loan_id,
    user_id,
) -> None:
    connection.execute(
        text(
            f"""
            INSERT INTO {_qualified(schema, "loans")}
                (id, user_id, name, principal_kyat, start_date)
            VALUES (:id, :user_id, 'Test loan', 100000, DATE '2026-01-01')
            """
        ),
        {"id": loan_id, "user_id": user_id},
    )


def _insert_repayment(
    connection: Connection,
    schema: str,
    repayment_id,
    loan_id,
    user_id,
    *,
    amount: int = 1000,
) -> None:
    connection.execute(
        text(
            f"""
            INSERT INTO {_qualified(schema, "loan_repayments")}
                (id, user_id, loan_id, amount_kyat, repayment_date)
            VALUES (:id, :user_id, :loan_id, :amount, DATE '2026-01-02')
            """
        ),
        {
            "id": repayment_id,
            "user_id": user_id,
            "loan_id": loan_id,
            "amount": amount,
        },
    )


def _insert_goal(connection: Connection, schema: str, goal_id, user_id) -> None:
    connection.execute(
        text(
            f"""
            INSERT INTO {_qualified(schema, "savings_goals")}
                (id, user_id, name, target_kyat)
            VALUES (:id, :user_id, 'Test goal', 50000)
            """
        ),
        {"id": goal_id, "user_id": user_id},
    )


def _insert_plan(
    connection: Connection,
    schema: str,
    plan_id,
    goal_id,
    user_id,
    *,
    start_date: date = date(2026, 1, 1),
    duration: int = 1,
) -> None:
    connection.execute(
        text(
            f"""
            INSERT INTO {_qualified(schema, "savings_plans")}
                (id, user_id, goal_id, start_date, duration_days, daily_amount_kyat)
            VALUES (:id, :user_id, :goal_id, :start_date, :duration, 100)
            """
        ),
        {
            "id": plan_id,
            "user_id": user_id,
            "goal_id": goal_id,
            "start_date": start_date,
            "duration": duration,
        },
    )


def _insert_plan_day(
    connection: Connection,
    schema: str,
    day_id,
    plan_id,
    goal_id,
    user_id,
    *,
    day_number: int = 1,
    planned_date: date = date(2026, 1, 1),
    status: str = "pending",
) -> None:
    connection.execute(
        text(
            f"""
            INSERT INTO {_qualified(schema, "savings_plan_days")}
                (id, user_id, goal_id, plan_id, day_number, planned_date,
                 base_amount_kyat, carried_forward_kyat, required_amount_kyat,
                 status)
            VALUES (:id, :user_id, :goal_id, :plan_id, :day_number, :planned_date,
                    100, 0, 100, 'pending')
            """
        ),
        {
            "id": day_id,
            "user_id": user_id,
            "goal_id": goal_id,
            "plan_id": plan_id,
            "day_number": day_number,
            "planned_date": planned_date,
        },
    )
    if status == "completed":
        connection.execute(
            text(
                f"""
                UPDATE {_qualified(schema, "savings_plan_days")}
                SET status = 'completed',
                    completion_date = :completion_date,
                    actual_amount_kyat = 100,
                    reconciled_contributions_kyat = 100,
                    unpaid_carry_forward_kyat = 0
                WHERE id = :id
                """
            ),
            {"id": day_id, "completion_date": planned_date},
        )
    else:
        assert status == "pending"


def _insert_savings_entry(
    connection: Connection,
    schema: str,
    entry_id,
    goal_id,
    user_id,
    *,
    entry_type: str = "contribution",
    amount: int = 100,
    entry_date: date = date(2026, 1, 1),
    plan_day_id=None,
    correction_of_entry_id=None,
    correction_reason=None,
) -> None:
    connection.execute(
        text(
            f"""
            INSERT INTO {_qualified(schema, "savings_entries")}
                (id, user_id, goal_id, plan_day_id, entry_type, amount_kyat,
                 entry_date, correction_of_entry_id, correction_reason)
            VALUES (:id, :user_id, :goal_id, :plan_day_id, :entry_type, :amount,
                    :entry_date, :correction_of_entry_id, :correction_reason)
            """
        ),
        {
            "id": entry_id,
            "user_id": user_id,
            "goal_id": goal_id,
            "plan_day_id": plan_day_id,
            "entry_type": entry_type,
            "amount": amount,
            "entry_date": entry_date,
            "correction_of_entry_id": correction_of_entry_id,
            "correction_reason": correction_reason,
        },
    )


def _assert_rejected(
    connection: Connection,
    statement: str,
    parameters: dict[str, object] | None = None,
) -> None:
    savepoint = connection.begin_nested()
    try:
        connection.execute(text(statement), parameters or {})
    except DBAPIError:
        if savepoint.is_active:
            savepoint.rollback()
    else:
        savepoint.rollback()
        raise AssertionError("Expected PostgreSQL to reject the operation")


def test_admin_audit_logs_are_append_only(protection_db) -> None:
    connection, schema = protection_db
    user_id, audit_id = uuid4(), uuid4()
    _insert_user(connection, schema, user_id)
    connection.execute(
        text(
            f"""
            INSERT INTO {_qualified(schema, "admin_audit_logs")}
                (id, actor_type, actor_user_id, action, target_user_id, target_type)
            VALUES (:id, 'user', :user_id, 'test_action', :user_id, 'user')
            """
        ),
        {"id": audit_id, "user_id": user_id},
    )

    _assert_rejected(
        connection,
        f"UPDATE {_qualified(schema, 'admin_audit_logs')} "
        "SET action = 'changed' WHERE id = :id",
        {"id": audit_id},
    )
    _assert_rejected(
        connection,
        f"DELETE FROM {_qualified(schema, 'admin_audit_logs')} WHERE id = :id",
        {"id": audit_id},
    )
    _assert_rejected(
        connection,
        f"UPDATE {_qualified(schema, 'admin_audit_logs')} "
        "SET target_user_id = NULL WHERE id = :id",
        {"id": audit_id},
    )


def test_audit_target_set_null_foreign_key_action_is_preserved(
    protection_db,
) -> None:
    connection, schema = protection_db
    target_user_id, audit_id = uuid4(), uuid4()
    _insert_user(connection, schema, target_user_id)
    connection.execute(
        text(
            f"""
            INSERT INTO {_qualified(schema, "admin_audit_logs")}
                (id, actor_type, action, target_user_id, target_type)
            VALUES (:id, 'system', 'targeted_event', :target_id, 'user')
            """
        ),
        {"id": audit_id, "target_id": target_user_id},
    )

    connection.execute(
        text(
            f"DELETE FROM {_qualified(schema, 'users')} WHERE id = :id"
        ),
        {"id": target_user_id},
    )
    assert connection.execute(
        text(
            f"SELECT target_user_id FROM {_qualified(schema, 'admin_audit_logs')} "
            "WHERE id = :id"
        ),
        {"id": audit_id},
    ).scalar_one() is None


def test_loan_repayment_facts_are_immutable_but_revision_can_advance(
    protection_db,
) -> None:
    connection, schema = protection_db
    user_id, loan_id, repayment_id = uuid4(), uuid4(), uuid4()
    other_user_id, other_loan_id = uuid4(), uuid4()
    _insert_user(connection, schema, user_id)
    _insert_user(connection, schema, other_user_id)
    _insert_loan(connection, schema, loan_id, user_id)
    _insert_loan(connection, schema, other_loan_id, user_id)
    _insert_repayment(connection, schema, repayment_id, loan_id, user_id)

    for update in (
        "amount_kyat = 2000",
        "repayment_date = DATE '2026-01-03'",
        "note = 'changed'",
        "created_at = TIMESTAMPTZ '2026-01-03 00:00:00+00'",
        "loan_id = :other_loan_id",
        "user_id = :other_user_id",
    ):
        _assert_rejected(
            connection,
            f"UPDATE {_qualified(schema, 'loan_repayments')} "
            f"SET {update} WHERE id = :id",
            {
                "id": repayment_id,
                "other_loan_id": other_loan_id,
                "other_user_id": other_user_id,
            },
        )
    _assert_rejected(
        connection,
        f"UPDATE {_qualified(schema, 'loan_repayments')} "
        "SET amount_kyat = 2000, revision = revision + 1 WHERE id = :id",
        {"id": repayment_id},
    )
    connection.execute(
        text(
            f"UPDATE {_qualified(schema, 'loan_repayments')} "
            "SET revision = revision + 1 WHERE id = :id"
        ),
        {"id": repayment_id},
    )
    assert connection.execute(
        text(
            f"SELECT revision FROM {_qualified(schema, 'loan_repayments')} "
            "WHERE id = :id"
        ),
        {"id": repayment_id},
    ).scalar_one() == 2
    _assert_rejected(
        connection,
        f"UPDATE {_qualified(schema, 'loan_repayments')} "
        "SET revision = revision + 2 WHERE id = :id",
        {"id": repayment_id},
    )
    _assert_rejected(
        connection,
        f"DELETE FROM {_qualified(schema, 'loan_repayments')} WHERE id = :id",
        {"id": repayment_id},
    )


def test_loan_repayment_corrections_validate_and_are_append_only(
    protection_db,
) -> None:
    connection, schema = protection_db
    owner_id, other_id = uuid4(), uuid4()
    loan_id, other_loan_id, repayment_id = uuid4(), uuid4(), uuid4()
    correction_id = uuid4()
    _insert_user(connection, schema, owner_id)
    _insert_user(connection, schema, other_id)
    _insert_loan(connection, schema, loan_id, owner_id)
    _insert_loan(connection, schema, other_loan_id, other_id)
    _insert_repayment(connection, schema, repayment_id, loan_id, owner_id)

    correction_insert = f"""
        INSERT INTO {_qualified(schema, "loan_repayment_corrections")}
            (id, user_id, loan_id, repayment_id, type, amount_kyat, reason,
             created_by_user_id)
        VALUES (:id, :user_id, :loan_id, :repayment_id, 'reversal', :amount,
                :reason, :user_id)
    """
    _assert_rejected(
        connection,
        correction_insert,
        {
            "id": uuid4(),
            "user_id": owner_id,
            "loan_id": loan_id,
            "repayment_id": repayment_id,
            "amount": 999,
            "reason": "Correction",
        },
    )
    _assert_rejected(
        connection,
        correction_insert,
        {
            "id": uuid4(),
            "user_id": other_id,
            "loan_id": other_loan_id,
            "repayment_id": repayment_id,
            "amount": 1000,
            "reason": "Correction",
        },
    )
    connection.execute(
        text(correction_insert),
        {
            "id": correction_id,
            "user_id": owner_id,
            "loan_id": loan_id,
            "repayment_id": repayment_id,
            "amount": 1000,
            "reason": "Duplicate payment",
        },
    )
    _assert_rejected(connection, correction_insert, {
        "id": uuid4(),
        "user_id": owner_id,
        "loan_id": loan_id,
        "repayment_id": repayment_id,
        "amount": 1000,
        "reason": "Second correction",
    })
    _assert_rejected(
        connection,
        f"UPDATE {_qualified(schema, 'loan_repayment_corrections')} "
        "SET reason = 'changed' WHERE id = :id",
        {"id": correction_id},
    )
    _assert_rejected(
        connection,
        f"DELETE FROM {_qualified(schema, 'loan_repayment_corrections')} "
        "WHERE id = :id",
        {"id": correction_id},
    )
    repayment = connection.execute(
        text(
            f"SELECT amount_kyat, revision FROM "
            f"{_qualified(schema, 'loan_repayments')} WHERE id = :id"
        ),
        {"id": repayment_id},
    ).one()
    assert tuple(repayment) == (1000, 1)


def test_savings_entries_are_immutable_and_corrections_must_match(
    protection_db,
) -> None:
    connection, schema = protection_db
    owner_id, other_id = uuid4(), uuid4()
    goal_id, other_goal_id, entry_id = uuid4(), uuid4(), uuid4()
    _insert_user(connection, schema, owner_id)
    _insert_user(connection, schema, other_id)
    _insert_goal(connection, schema, goal_id, owner_id)
    _insert_goal(connection, schema, other_goal_id, other_id)
    _insert_savings_entry(connection, schema, entry_id, goal_id, owner_id)
    other_entry_id = uuid4()
    _insert_savings_entry(
        connection,
        schema,
        other_entry_id,
        goal_id,
        owner_id,
        entry_type="withdrawal",
    )

    immutable_updates = (
        "amount_kyat = 200",
        "entry_type = 'withdrawal'",
        "goal_id = :other_goal_id",
        "correction_of_entry_id = :other_entry_id, "
        "correction_reason = 'not a correction'",
        "note = 'changed'",
        "entry_date = DATE '2026-01-02'",
        "created_at = TIMESTAMPTZ '2026-01-02 00:00:00+00'",
        "user_id = :other_user_id",
    )
    for update in immutable_updates:
        _assert_rejected(
            connection,
            f"UPDATE {_qualified(schema, 'savings_entries')} "
            f"SET {update} WHERE id = :id",
            {
                "id": entry_id,
                "other_goal_id": other_goal_id,
                "other_entry_id": other_entry_id,
                "other_user_id": other_id,
            },
        )
    _assert_rejected(
        connection,
        f"DELETE FROM {_qualified(schema, 'savings_entries')} WHERE id = :id",
        {"id": entry_id},
    )
    correction_insert = f"""
        INSERT INTO {_qualified(schema, "savings_entries")}
            (id, user_id, goal_id, entry_type, amount_kyat, entry_date,
             correction_of_entry_id, correction_reason)
        VALUES (:id, :user_id, :goal_id, :entry_type, :amount, :entry_date,
                :original_id, :reason)
    """
    _assert_rejected(
        connection,
        correction_insert,
        {
            "id": uuid4(),
            "user_id": owner_id,
            "goal_id": goal_id,
            "entry_type": "withdrawal",
            "amount": 99,
            "entry_date": date(2026, 1, 1),
            "original_id": entry_id,
            "reason": "Amount mismatch",
        },
    )
    _assert_rejected(
        connection,
        correction_insert,
        {
            "id": uuid4(),
            "user_id": owner_id,
            "goal_id": goal_id,
            "entry_type": "contribution",
            "amount": 100,
            "entry_date": date(2026, 1, 1),
            "original_id": entry_id,
            "reason": "Wrong type",
        },
    )
    _assert_rejected(
        connection,
        correction_insert,
        {
            "id": uuid4(),
            "user_id": owner_id,
            "goal_id": goal_id,
            "entry_type": "withdrawal",
            "amount": 100,
            "entry_date": date(2026, 1, 2),
            "original_id": entry_id,
            "reason": "Wrong date",
        },
    )
    _assert_rejected(
        connection,
        correction_insert,
        {
            "id": uuid4(),
            "user_id": other_id,
            "goal_id": other_goal_id,
            "entry_type": "withdrawal",
            "amount": 100,
            "entry_date": date(2026, 1, 1),
            "original_id": entry_id,
            "reason": "Wrong owner",
        },
    )

    correction_id = uuid4()
    connection.execute(
        text(correction_insert),
        {
            "id": correction_id,
            "user_id": owner_id,
            "goal_id": goal_id,
            "entry_type": "withdrawal",
            "amount": 100,
            "entry_date": date(2026, 1, 1),
            "original_id": entry_id,
            "reason": "Correcting entry",
        },
    )
    _assert_rejected(
        connection,
        correction_insert,
        {
            "id": uuid4(),
            "user_id": owner_id,
            "goal_id": goal_id,
            "entry_type": "withdrawal",
            "amount": 100,
            "entry_date": date(2026, 1, 1),
            "original_id": entry_id,
            "reason": "Duplicate correction",
        },
    )
    second_correction_id = uuid4()
    connection.execute(
        text(correction_insert),
        {
            "id": second_correction_id,
            "user_id": owner_id,
            "goal_id": goal_id,
            "entry_type": "contribution",
            "amount": 100,
            "entry_date": date(2026, 1, 1),
            "original_id": correction_id,
            "reason": "Valid reversal of prior correction",
        },
    )
    _assert_rejected(
        connection,
        correction_insert,
        {
            "id": uuid4(),
            "user_id": owner_id,
            "goal_id": goal_id,
            "entry_type": "withdrawal",
            "amount": 100,
            "entry_date": date(2026, 1, 1),
            "original_id": correction_id,
            "reason": "Duplicate correction in chain",
        },
    )
    original = connection.execute(
        text(
            f"""
            SELECT entry_type, amount_kyat, entry_date, note
            FROM {_qualified(schema, "savings_entries")}
            WHERE id = :id
            """
        ),
        {"id": entry_id},
    ).one()
    assert tuple(original) == ("contribution", 100, date(2026, 1, 1), None)


def test_pending_savings_plan_rebuild_detaches_entries_atomically(
    protection_db,
) -> None:
    connection, schema = protection_db
    user_id, goal_id, plan_id = uuid4(), uuid4(), uuid4()
    first_day_id, second_day_id = uuid4(), uuid4()
    entry_id = uuid4()
    _insert_user(connection, schema, user_id)
    _insert_goal(connection, schema, goal_id, user_id)
    _insert_plan(
        connection,
        schema,
        plan_id,
        goal_id,
        user_id,
        duration=2,
    )
    _insert_plan_day(
        connection,
        schema,
        first_day_id,
        plan_id,
        goal_id,
        user_id,
        planned_date=date(2026, 1, 1),
    )
    _insert_plan_day(
        connection,
        schema,
        second_day_id,
        plan_id,
        goal_id,
        user_id,
        day_number=2,
        planned_date=date(2026, 1, 2),
    )
    _insert_savings_entry(
        connection,
        schema,
        entry_id,
        goal_id,
        user_id,
        plan_day_id=first_day_id,
    )

    connection.execute(
        text(
            f"UPDATE {_qualified(schema, 'savings_entries')} "
            "SET plan_day_id = NULL WHERE id = :id"
        ),
        {"id": entry_id},
    )
    connection.execute(
        text(
            f"UPDATE {_qualified(schema, 'savings_plans')} "
            "SET start_date = DATE '2026-02-01' WHERE id = :id"
        ),
        {"id": plan_id},
    )
    connection.execute(
        text(
            f"DELETE FROM {_qualified(schema, 'savings_plan_days')} "
            "WHERE plan_id = :id"
        ),
        {"id": plan_id},
    )
    new_day_ids = (uuid4(), uuid4())
    _insert_plan_day(
        connection,
        schema,
        new_day_ids[0],
        plan_id,
        goal_id,
        user_id,
        planned_date=date(2026, 2, 1),
    )
    _insert_plan_day(
        connection,
        schema,
        new_day_ids[1],
        plan_id,
        goal_id,
        user_id,
        day_number=2,
        planned_date=date(2026, 2, 2),
    )
    connection.commit()

    assert connection.execute(
        text(
            f"SELECT plan_day_id FROM {_qualified(schema, 'savings_entries')} "
            "WHERE id = :id"
        ),
        {"id": entry_id},
    ).scalar_one() is None
    assert connection.execute(
        text(
            f"SELECT count(*) FROM {_qualified(schema, 'savings_plan_days')} "
            "WHERE plan_id = :id"
        ),
        {"id": plan_id},
    ).scalar_one() == 2


def test_savings_entry_detachment_requires_pending_rebuild(
    protection_db,
) -> None:
    connection, schema = protection_db
    user_id, goal_id, plan_id = uuid4(), uuid4(), uuid4()
    day_id, entry_id = uuid4(), uuid4()
    _insert_user(connection, schema, user_id)
    _insert_goal(connection, schema, goal_id, user_id)
    _insert_plan(connection, schema, plan_id, goal_id, user_id)
    _insert_plan_day(connection, schema, day_id, plan_id, goal_id, user_id)
    _insert_savings_entry(
        connection,
        schema,
        entry_id,
        goal_id,
        user_id,
        plan_day_id=day_id,
    )

    _assert_rejected(
        connection,
        f"UPDATE {_qualified(schema, 'savings_entries')} "
        "SET plan_day_id = NULL, note = 'mutated' WHERE id = :id",
        {"id": entry_id},
    )
    connection.execute(
        text(
            f"UPDATE {_qualified(schema, 'savings_entries')} "
            "SET plan_day_id = NULL WHERE id = :id"
        ),
        {"id": entry_id},
    )
    with pytest.raises(IntegrityError, match="detachment must accompany"):
        connection.commit()
    connection.rollback()


def test_savings_entry_cannot_detach_from_or_attach_to_terminal_day(
    protection_db,
) -> None:
    connection, schema = protection_db
    user_id, goal_id, plan_id = uuid4(), uuid4(), uuid4()
    day_id, entry_id = uuid4(), uuid4()
    _insert_user(connection, schema, user_id)
    _insert_goal(connection, schema, goal_id, user_id)
    _insert_plan(connection, schema, plan_id, goal_id, user_id)
    _insert_plan_day(connection, schema, day_id, plan_id, goal_id, user_id)
    _insert_savings_entry(
        connection,
        schema,
        entry_id,
        goal_id,
        user_id,
        plan_day_id=day_id,
    )
    connection.execute(
        text(
            f"""
            UPDATE {_qualified(schema, "savings_plan_days")}
            SET status = 'completed',
                completion_date = DATE '2026-01-01',
                actual_amount_kyat = 100,
                reconciled_contributions_kyat = 100,
                unpaid_carry_forward_kyat = 0
            WHERE id = :id
            """
        ),
        {"id": day_id},
    )

    _assert_rejected(
        connection,
        f"UPDATE {_qualified(schema, 'savings_entries')} "
        "SET plan_day_id = NULL WHERE id = :id",
        {"id": entry_id},
    )
    _assert_rejected(
        connection,
        f"""
        INSERT INTO {_qualified(schema, "savings_entries")}
            (id, user_id, goal_id, plan_day_id, entry_type, amount_kyat, entry_date)
        VALUES (:id, :user_id, :goal_id, :day_id, 'contribution', 100,
                DATE '2026-01-01')
        """,
        {
            "id": uuid4(),
            "user_id": user_id,
            "goal_id": goal_id,
            "day_id": day_id,
        },
    )


def test_terminal_savings_plan_days_are_immutable_and_pending_rows_rebuild(
    protection_db,
) -> None:
    connection, schema = protection_db
    user_id, goal_id, plan_id = uuid4(), uuid4(), uuid4()
    terminal_day_id, pending_day_id = uuid4(), uuid4()
    _insert_user(connection, schema, user_id)
    _insert_goal(connection, schema, goal_id, user_id)
    _insert_plan(
        connection,
        schema,
        plan_id,
        goal_id,
        user_id,
        duration=2,
    )
    _insert_plan_day(
        connection,
        schema,
        terminal_day_id,
        plan_id,
        goal_id,
        user_id,
        status="completed",
    )
    _assert_rejected(
        connection,
        f"UPDATE {_qualified(schema, 'savings_plan_days')} "
        "SET required_amount_kyat = 200 WHERE id = :id",
        {"id": terminal_day_id},
    )
    _assert_rejected(
        connection,
        f"DELETE FROM {_qualified(schema, 'savings_plan_days')} "
        "WHERE id = :id",
        {"id": terminal_day_id},
    )

    _insert_plan_day(
        connection,
        schema,
        pending_day_id,
        plan_id,
        goal_id,
        user_id,
        day_number=2,
        planned_date=date(2026, 1, 2),
    )
    connection.execute(
        text(
            f"UPDATE {_qualified(schema, 'savings_plan_days')} "
            "SET required_amount_kyat = 125 WHERE id = :id"
        ),
        {"id": pending_day_id},
    )
    _assert_rejected(
        connection,
        f"UPDATE {_qualified(schema, 'savings_plan_days')} "
        "SET status = 'completed', completion_date = DATE '2026-01-02', "
        "actual_amount_kyat = 100, reconciled_contributions_kyat = 100, "
        "unpaid_carry_forward_kyat = NULL WHERE id = :id",
        {"id": pending_day_id},
    )


def test_pending_day_delete_requires_complete_replacement_schedule(
    protection_db,
) -> None:
    connection, schema = protection_db
    user_id, goal_id, plan_id = uuid4(), uuid4(), uuid4()
    first_day_id, second_day_id = uuid4(), uuid4()
    _insert_user(connection, schema, user_id)
    _insert_goal(connection, schema, goal_id, user_id)
    _insert_plan(
        connection,
        schema,
        plan_id,
        goal_id,
        user_id,
        duration=2,
    )
    _insert_plan_day(
        connection,
        schema,
        first_day_id,
        plan_id,
        goal_id,
        user_id,
    )
    _insert_plan_day(
        connection,
        schema,
        second_day_id,
        plan_id,
        goal_id,
        user_id,
        day_number=2,
        planned_date=date(2026, 1, 2),
    )
    connection.execute(
        text(
            f"DELETE FROM {_qualified(schema, 'savings_plan_days')} "
            "WHERE id = :id"
        ),
        {"id": first_day_id},
    )
    with pytest.raises(IntegrityError, match="replace the complete plan schedule"):
        connection.commit()
    connection.rollback()


def test_plan_schedule_change_requires_pending_schedule_rebuild(
    protection_db,
) -> None:
    connection, schema = protection_db
    user_id, goal_id, plan_id, day_id = uuid4(), uuid4(), uuid4(), uuid4()
    _insert_user(connection, schema, user_id)
    _insert_goal(connection, schema, goal_id, user_id)
    _insert_plan(connection, schema, plan_id, goal_id, user_id)
    _insert_plan_day(connection, schema, day_id, plan_id, goal_id, user_id)

    connection.execute(
        text(
            f"UPDATE {_qualified(schema, 'savings_plans')} "
            "SET start_date = DATE '2026-02-01' WHERE id = :id"
        ),
        {"id": plan_id},
    )
    with pytest.raises(IntegrityError, match="complete pending schedule rebuild"):
        connection.commit()
    connection.rollback()


def test_terminal_history_freezes_plan_schedule_but_not_metadata(
    protection_db,
) -> None:
    connection, schema = protection_db
    user_id, goal_id, plan_id, day_id = uuid4(), uuid4(), uuid4(), uuid4()
    _insert_user(connection, schema, user_id)
    _insert_goal(connection, schema, goal_id, user_id)
    _insert_plan(connection, schema, plan_id, goal_id, user_id)
    _insert_plan_day(
        connection,
        schema,
        day_id,
        plan_id,
        goal_id,
        user_id,
        status="completed",
    )

    _assert_rejected(
        connection,
        f"UPDATE {_qualified(schema, 'savings_plans')} "
        "SET daily_amount_kyat = 200 WHERE id = :id",
        {"id": plan_id},
    )
    connection.execute(
        text(
            f"UPDATE {_qualified(schema, 'savings_plans')} "
            "SET revision = revision + 1 WHERE id = :id"
        ),
        {"id": plan_id},
    )
    assert connection.execute(
        text(
            f"SELECT revision FROM {_qualified(schema, 'savings_plans')} "
            "WHERE id = :id"
        ),
        {"id": plan_id},
    ).scalar_one() == 2


def test_closed_users_cannot_reactivate_and_operational_cleanup_still_works(
    protection_db,
) -> None:
    connection, schema = protection_db
    active_user, suspended_user, closed_user = uuid4(), uuid4(), uuid4()
    _insert_user(connection, schema, active_user)
    _insert_user(connection, schema, suspended_user, status="suspended")
    _insert_user(connection, schema, closed_user, status="closed")

    connection.execute(
        text(
            f"UPDATE {_qualified(schema, 'users')} "
            "SET status = 'suspended' WHERE id = :id"
        ),
        {"id": active_user},
    )
    connection.execute(
        text(
            f"UPDATE {_qualified(schema, 'users')} "
            "SET status = 'active' WHERE id = :id"
        ),
        {"id": suspended_user},
    )
    connection.execute(
        text(
            f"UPDATE {_qualified(schema, 'users')} "
            "SET status = 'closed' WHERE id = :id"
        ),
        {"id": suspended_user},
    )
    connection.execute(
        text(
            f"UPDATE {_qualified(schema, 'users')} "
            "SET status = 'closed' WHERE id = :id"
        ),
        {"id": active_user},
    )
    _assert_rejected(
        connection,
        f"UPDATE {_qualified(schema, 'users')} "
        "SET status = 'active' WHERE id = :id",
        {"id": closed_user},
    )
    connection.execute(
        text(
            f"UPDATE {_qualified(schema, 'users')} "
            "SET updated_at = TIMESTAMPTZ '2026-01-03 00:00:00+00' WHERE id = :id"
        ),
        {"id": closed_user},
    )

    session_id, token_id = uuid4(), uuid4()
    connection.execute(
        text(
            f"""
            INSERT INTO {_qualified(schema, "auth_sessions")}
                (id, user_id, session_family_id, refresh_token_hash, refresh_expires_at)
            VALUES (:id, :user_id, :family_id, :token_hash, :expires_at)
            """
        ),
        {
            "id": session_id,
            "user_id": closed_user,
            "family_id": uuid4(),
            "token_hash": f"hash-{uuid4()}",
            "expires_at": datetime(2026, 1, 1, tzinfo=timezone.utc),
        },
    )
    connection.execute(
        text(
            f"""
            INSERT INTO {_qualified(schema, "auth_action_tokens")}
                (id, user_id, token_type, token_hash, expires_at)
            VALUES (:id, :user_id, 'password_reset', :token_hash, :expires_at)
            """
        ),
        {
            "id": token_id,
            "user_id": closed_user,
            "token_hash": f"hash-{uuid4()}",
            "expires_at": datetime(2026, 1, 1, tzinfo=timezone.utc),
        },
    )
    connection.execute(
        text(
            f"DELETE FROM {_qualified(schema, 'auth_sessions')} WHERE id = :id"
        ),
        {"id": session_id},
    )
    connection.execute(
        text(
            f"DELETE FROM {_qualified(schema, 'auth_action_tokens')} WHERE id = :id"
        ),
        {"id": token_id},
    )
