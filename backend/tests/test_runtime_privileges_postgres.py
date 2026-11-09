from __future__ import annotations

import importlib.util
from pathlib import Path
from uuid import uuid4

import pytest
from sqlalchemy import Connection, create_engine, text
from sqlalchemy.engine import Engine
from sqlalchemy.exc import DBAPIError

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

INSERT_COLUMNS = {
    "users": {"id", "email", "password_hash", "timezone"},
    "admin_audit_logs": {
        "id", "actor_type", "actor_user_id", "action", "target_user_id",
        "target_type", "target_id", "details",
    },
    "auth_sessions": {
        "id", "user_id", "session_family_id", "refresh_token_hash",
        "refresh_expires_at",
    },
    "auth_action_tokens": {
        "id", "user_id", "token_type", "token_hash", "target_email",
        "expires_at",
    },
    "idempotency_records": {
        "id", "owner_user_id", "principal_scope_hash", "http_method",
        "route_key", "key_hash", "request_hash", "expires_at",
    },
    "user_financial_settings": {
        "user_id", "starting_balance_kyat", "effective_date",
    },
    "transactions": {
        "id", "user_id", "category_id", "type", "description",
        "amount_kyat", "transaction_date",
    },
    "loans": {"id", "user_id", "name", "principal_kyat", "start_date"},
    "loan_repayments": {
        "id", "user_id", "loan_id", "amount_kyat", "repayment_date", "note",
    },
    "loan_repayment_corrections": {
        "id", "user_id", "loan_id", "repayment_id", "type", "amount_kyat",
        "reason", "created_by_user_id",
    },
    "savings_goals": {"id", "user_id", "name", "target_kyat", "tone"},
    "savings_plans": {
        "id", "user_id", "goal_id", "start_date", "duration_days",
        "daily_amount_kyat",
    },
    "savings_plan_days": {
        "id", "user_id", "goal_id", "plan_id", "day_number", "planned_date",
        "base_amount_kyat", "carried_forward_kyat", "required_amount_kyat",
    },
    "savings_entries": {
        "id", "user_id", "goal_id", "plan_day_id", "entry_type",
        "amount_kyat", "entry_date", "note", "correction_of_entry_id",
        "correction_reason",
    },
    "trips": {
        "id", "user_id", "name", "destination", "start_date", "end_date",
    },
    "trip_category_budgets": {
        "id", "user_id", "trip_id", "category", "budget_kyat",
    },
    "trip_spending_entries": {
        "id", "user_id", "trip_id", "category_budget_id", "category",
        "description", "amount_kyat", "spending_date",
    },
}

UPDATE_COLUMNS = {
    "users": {
        "email", "password_hash", "email_verified_at", "pending_email",
        "status", "timezone", "revision", "updated_at",
    },
    "auth_sessions": {
        "last_refreshed_at", "revoked_at", "replaced_by_session_id",
    },
    "auth_action_tokens": {"consumed_at"},
    "idempotency_records": {
        "state", "response_status", "response_body", "response_headers",
        "completed_at",
    },
    "user_financial_settings": {
        "starting_balance_kyat", "effective_date", "revision", "updated_at",
    },
    "transactions": {
        "category_id", "type", "description", "amount_kyat",
        "transaction_date", "updated_at", "revision",
    },
    "loans": {
        "name", "principal_kyat", "start_date", "updated_at", "revision",
    },
    "loan_repayments": {"revision"},
    "savings_goals": {
        "name", "target_kyat", "tone", "status", "archived_at", "revision",
        "updated_at",
    },
    "savings_plans": {
        "start_date", "duration_days", "daily_amount_kyat", "updated_at",
        "revision",
    },
    "savings_plan_days": {
        "day_number", "planned_date", "base_amount_kyat",
        "carried_forward_kyat", "required_amount_kyat", "status",
        "completion_date", "actual_amount_kyat",
        "reconciled_contributions_kyat", "unpaid_carry_forward_kyat",
    },
    "savings_entries": {"plan_day_id"},
    "trips": {
        "name", "destination", "start_date", "end_date", "status", "updated_at",
        "revision",
    },
    "trip_category_budgets": {
        "category", "budget_kyat", "updated_at", "revision",
    },
    "trip_spending_entries": {
        "category_budget_id", "category", "description", "amount_kyat",
        "spending_date", "updated_at", "revision",
    },
}

DELETE_TABLES = {
    "transactions",
    "savings_plan_days",
    "trips",
    "trip_category_budgets",
    "trip_spending_entries",
}

# Owned by finpath_migrator and deliberately left without any grant to
# finpath_app. Granting on an object the runtime role already has privileges on
# is accepted by PostgreSQL as a redundant no-op, so a probe table is required
# to prove that the runtime role cannot grant privileges at all.
RUNTIME_GRANT_PROBE = "runtime_grant_probe"


def _load_migration():
    spec = importlib.util.spec_from_file_location(
        "finpath_initial_schema_for_privilege_tests",
        MIGRATION_PATH,
    )
    assert spec is not None and spec.loader is not None
    migration = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(migration)
    return migration


def _qualified(schema: str, table: str) -> str:
    return f'"{schema}"."{table}"'


def _expect_denied(
    connection: Connection,
    statement: str,
    parameters=None,
    *,
    sqlstate: str | None = None,
) -> None:
    savepoint = connection.begin_nested()
    try:
        connection.execute(text(statement), parameters or {})
    except DBAPIError as exc:
        if savepoint.is_active:
            savepoint.rollback()
        if sqlstate is not None:
            actual = getattr(getattr(exc, "orig", None), "sqlstate", None)
            if actual != sqlstate:
                pytest.fail(
                    f"Expected SQLSTATE {sqlstate} for a denied operation, "
                    f"got {actual!r}: {statement}"
                )
    else:
        savepoint.rollback()
        pytest.fail(f"Runtime operation unexpectedly succeeded: {statement}")


@pytest.fixture
def restricted_runtime_db():
    runtime_url, migration_url, test_database = require_test_database_urls()
    migration_engine: Engine = create_engine(
        migration_url,
        hide_parameters=True,
        pool_pre_ping=True,
    )
    runtime_engine: Engine = create_engine(
        runtime_url,
        hide_parameters=True,
        pool_pre_ping=True,
    )
    schema = f"finpath_privilege_test_{uuid4().hex}"
    probe_schema = f"finpath_grant_probe_{uuid4().hex}"
    migration = _load_migration()
    created_schemas: list[str] = []
    try:
        with migration_engine.connect() as setup:
            assert setup.execute(text("SELECT current_user")).scalar_one() == (
                "finpath_migrator"
            )
            server = setup.execute(text("SELECT version()")).scalar_one()
            assert server.startswith("PostgreSQL 17."), server.split(",")[0]
            assert setup.execute(
                text("SELECT current_database()")
            ).scalar_one() == test_database
            assert len(Base.metadata.tables) == 20
            setup.execute(text(f'CREATE SCHEMA "{schema}"'))
            setup.execute(text(f'CREATE SCHEMA "{probe_schema}"'))
            setup.commit()
            created_schemas.extend([schema, probe_schema])
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
            for statement in migration.RUNTIME_PRIVILEGE_DDL:
                setup.execute(
                    text(statement.replace("public.", f'"{schema}".'))
                )
            # Later migrations add column grants; keep the isolated privilege
            # matrix in sync with the current schema.
            setup.execute(
                text(
                    f'GRANT UPDATE (status) ON TABLE "{schema}".trips '
                    "TO finpath_app"
                )
            )
            setup.execute(text(migration.CLEANUP_FUNCTION_DDL.replace(
                "public.", f'"{schema}".'
            )))
            setup.execute(text(f'GRANT USAGE ON SCHEMA "{schema}" TO finpath_app'))
            setup.execute(
                text(
                    f'REVOKE ALL ON FUNCTION "{schema}".'
                    f'{migration.CLEANUP_FUNCTION_NAME}() FROM PUBLIC, finpath_app'
                )
            )
            setup.execute(
                text(
                    f'GRANT EXECUTE ON FUNCTION "{schema}".'
                    f'{migration.CLEANUP_FUNCTION_NAME}() TO finpath_app'
                )
            )
            setup.execute(
                text(
                    f'INSERT INTO "{schema}".transaction_categories '
                    "(id, name, type) VALUES "
                    "('00000000-0000-0000-0000-000000000001', "
                    "'Test income', 'income')"
                )
            )
            # A separate schema keeps the twenty-table privilege matrix intact.
            # finpath_app gets schema USAGE but no table privilege, so a GRANT
            # attempt is denied for lack of grant authority, not for lack of
            # schema access.
            setup.execute(
                text(
                    f'CREATE TABLE "{probe_schema}".{RUNTIME_GRANT_PROBE} '
                    "(id integer)"
                )
            )
            setup.execute(
                text(f'GRANT USAGE ON SCHEMA "{probe_schema}" TO finpath_app')
            )
            setup.commit()

        with runtime_engine.connect() as connection:
            assert connection.execute(text("SELECT current_user")).scalar_one() == (
                "finpath_app"
            )
            assert connection.execute(
                text("SELECT current_database()")
            ).scalar_one() == test_database
            connection.rollback()
            transaction = connection.begin()
            try:
                yield connection, schema, probe_schema
            finally:
                if transaction.is_active:
                    transaction.rollback()
    finally:
        if created_schemas:
            with migration_engine.begin() as cleanup:
                for created_schema in created_schemas:
                    cleanup.execute(
                        text(f'DROP SCHEMA "{created_schema}" CASCADE')
                    )
        runtime_engine.dispose()
        migration_engine.dispose()


def test_effective_column_privileges_match_all_twenty_matrix_rows(
    restricted_runtime_db,
) -> None:
    connection, schema, _ = restricted_runtime_db
    memberships = connection.execute(
        text(
            """
            SELECT count(*)
            FROM pg_auth_members AS membership
            JOIN pg_roles AS granted ON granted.oid = membership.roleid
            JOIN pg_roles AS member ON member.oid = membership.member
            WHERE member.rolname = 'finpath_app'
            """
        )
    ).scalar_one()
    assert memberships == 0
    assert not connection.execute(
        text("SELECT pg_has_role('finpath_app', 'finpath_migrator', 'MEMBER')")
    ).scalar_one()
    assert not connection.execute(
        text("SELECT has_database_privilege('finpath_app', current_database(), 'CREATE')")
    ).scalar_one()
    assert not connection.execute(
        text(f"SELECT has_schema_privilege('finpath_app', '{schema}', 'CREATE')")
    ).scalar_one()
    assert not connection.execute(
        text("SELECT has_schema_privilege('finpath_app', 'public', 'CREATE')")
    ).scalar_one()

    tables = connection.execute(
        text(
            """
            SELECT relation.relname, attribute.attname,
                   has_table_privilege('finpath_app', relation.oid, 'SELECT'),
                   has_table_privilege('finpath_app', relation.oid, 'INSERT'),
                   has_table_privilege('finpath_app', relation.oid, 'UPDATE'),
                   has_table_privilege('finpath_app', relation.oid, 'DELETE'),
                   has_column_privilege('finpath_app', relation.oid, attribute.attnum, 'INSERT'),
                   has_column_privilege('finpath_app', relation.oid, attribute.attnum, 'UPDATE'),
                   EXISTS (
                       SELECT 1
                       FROM pg_catalog.aclexplode(
                           COALESCE(relation.relacl, pg_catalog.acldefault('r', relation.relowner))
                       ) AS acl
                       WHERE acl.grantee = 0
                         AND acl.privilege_type IN ('SELECT', 'INSERT', 'UPDATE', 'DELETE')
                   )
            FROM pg_class AS relation
            JOIN pg_namespace AS namespace ON namespace.oid = relation.relnamespace
            JOIN pg_attribute AS attribute ON attribute.attrelid = relation.oid
            WHERE namespace.nspname = :schema
              AND relation.relkind = 'r'
              AND attribute.attnum > 0
              AND NOT attribute.attisdropped
            ORDER BY relation.relname, attribute.attnum
            """
        ),
        {"schema": schema},
    ).all()
    observed_tables = {row.relname for row in tables}
    assert observed_tables == set(Base.metadata.tables)
    grouped = {}
    for row in tables:
        table = row.relname
        columns = Base.metadata.tables[table].columns
        expected_insert = row.attname in INSERT_COLUMNS.get(table, set())
        expected_update = row.attname in UPDATE_COLUMNS.get(table, set())
        expected_delete = table in DELETE_TABLES
        assert row[2:6] == (True, False, False, expected_delete), (
            table, row.attname, row[2:6]
        )
        assert row[6] is expected_insert, (table, row.attname, "INSERT")
        assert row[7] is expected_update, (table, row.attname, "UPDATE")
        assert not row[8], (table, row.attname, "PUBLIC privilege")
        grouped[table] = grouped.get(table, 0) + 1
        assert row.attname in columns
    assert len(grouped) == 20

    function_acl = connection.execute(
        text(
            """
            SELECT
                has_function_privilege(
                    'finpath_app', function.oid, 'EXECUTE'
                ),
                pg_get_userbyid(function.proowner),
                function.prosecdef,
                function.pronargs,
                function.proconfig,
                EXISTS (
                    SELECT 1
                    FROM pg_catalog.aclexplode(
                        COALESCE(
                            function.proacl,
                            pg_catalog.acldefault('f', function.proowner)
                        )
                    ) AS acl
                    WHERE acl.grantee = 0 AND acl.privilege_type = 'EXECUTE'
                )
            FROM pg_proc AS function
            JOIN pg_namespace AS namespace ON namespace.oid = function.pronamespace
            WHERE namespace.nspname = :schema
              AND function.proname = 'cleanup_expired_operational_records'
            """
        ),
        {"schema": schema},
    ).one()
    assert tuple(function_acl) == (
        True,
        "finpath_migrator",
        True,
        0,
        ["search_path=pg_catalog"],
        False,
    )


def test_runtime_allowed_operations_and_column_restrictions(
    restricted_runtime_db,
) -> None:
    connection, schema, _ = restricted_runtime_db
    user_id, loan_id, repayment_id = uuid4(), uuid4(), uuid4()
    _qualified_user = _qualified(schema, "users")
    connection.execute(
        text(
            f"INSERT INTO {_qualified_user} (id, email, password_hash, timezone) "
            "VALUES (:id, 'runtime@example.test', 'hash', 'Asia/Yangon')"
        ),
        {"id": user_id},
    )
    _expect_denied(
        connection,
        f"INSERT INTO {_qualified_user} "
        "(id, email, password_hash, timezone, email_verified_at) "
        "VALUES (:id, 'verified@example.test', 'hash', 'Asia/Yangon', now())",
        {"id": uuid4()},
    )
    _expect_denied(
        connection,
        f"INSERT INTO {_qualified_user} "
        "(id, email, password_hash, timezone, status) "
        "VALUES (:id, 'closed@example.test', 'hash', 'Asia/Yangon', 'closed')",
        {"id": uuid4()},
    )
    _expect_denied(
        connection,
        f"INSERT INTO {_qualified(schema, 'user_roles')} (user_id, role_id) "
        "VALUES (:user_id, 1)",
        {"user_id": user_id},
    )
    _expect_denied(
        connection,
        f"INSERT INTO {_qualified(schema, 'roles')} (id, name) "
        "VALUES (99, 'runtime-role')",
    )
    _expect_denied(
        connection,
        f"UPDATE {_qualified(schema, 'transaction_categories')} "
        "SET is_active = false",
    )
    audit_id = uuid4()
    connection.execute(
        text(
            f"INSERT INTO {_qualified(schema, 'admin_audit_logs')} "
            "(id, actor_type, action, target_type) "
            "VALUES (:id, 'system', 'test', 'user')"
        ),
        {"id": audit_id},
    )
    _expect_denied(
        connection,
        f"UPDATE {_qualified(schema, 'admin_audit_logs')} "
        "SET action = 'changed' WHERE id = :id",
        {"id": audit_id},
    )

    loan = _qualified(schema, "loans")
    connection.execute(
        text(
            f"INSERT INTO {loan} (id, user_id, name, principal_kyat, start_date) "
            "VALUES (:id, :user_id, 'Loan', 5000, DATE '2026-01-01')"
        ),
        {"id": loan_id, "user_id": user_id},
    )
    connection.execute(
        text(
            f"INSERT INTO {_qualified(schema, 'loan_repayments')} "
            "(id, user_id, loan_id, amount_kyat, repayment_date) "
            "VALUES (:id, :user_id, :loan_id, 1000, DATE '2026-01-02')"
        ),
        {"id": repayment_id, "user_id": user_id, "loan_id": loan_id},
    )
    connection.execute(
        text(
            f"UPDATE {_qualified(schema, 'loan_repayments')} "
            "SET revision = revision + 1 WHERE id = :id"
        ),
        {"id": repayment_id},
    )
    _expect_denied(
        connection,
        f"UPDATE {_qualified(schema, 'loan_repayments')} "
        "SET amount_kyat = 900 WHERE id = :id",
        {"id": repayment_id},
    )
    _expect_denied(
        connection,
        f"DELETE FROM {_qualified(schema, 'loan_repayments')} WHERE id = :id",
        {"id": repayment_id},
    )
    correction_id = uuid4()
    connection.execute(
        text(
            f"INSERT INTO {_qualified(schema, 'loan_repayment_corrections')} "
            "(id, user_id, loan_id, repayment_id, type, amount_kyat, reason, "
            "created_by_user_id) VALUES "
            "(:id, :user_id, :loan_id, :repayment_id, 'reversal', 1000, "
            "'Correction', :user_id)"
        ),
        {
            "id": correction_id,
            "user_id": user_id,
            "loan_id": loan_id,
            "repayment_id": repayment_id,
        },
    )
    _expect_denied(
        connection,
        f"UPDATE {_qualified(schema, 'loan_repayment_corrections')} "
        "SET reason = 'Changed' WHERE id = :id",
        {"id": correction_id},
    )

    transaction = _qualified(schema, "transactions")
    transaction_id = uuid4()
    connection.execute(
        text(
            f"INSERT INTO {transaction} "
            "(id, user_id, category_id, type, description, amount_kyat, transaction_date) "
            "VALUES (:id, :user_id, '00000000-0000-0000-0000-000000000001', "
            "'income', 'Salary', 1000, DATE '2026-01-03')"
        ),
        {"id": transaction_id, "user_id": user_id},
    )
    connection.execute(
        text(
            f"UPDATE {transaction} SET description = 'Updated salary', "
            "revision = revision + 1 WHERE id = :id"
        ),
        {"id": transaction_id},
    )
    _expect_denied(
        connection,
        f"UPDATE {transaction} SET user_id = :other WHERE id = :id",
        {"id": transaction_id, "other": uuid4()},
    )
    connection.execute(
        text(f"DELETE FROM {transaction} WHERE id = :id"),
        {"id": transaction_id},
    )

    entry_id, goal_id, plan_id, day_id = uuid4(), uuid4(), uuid4(), uuid4()
    connection.execute(
        text(
            f"INSERT INTO {_qualified(schema, 'savings_goals')} "
            "(id, user_id, name, target_kyat) VALUES (:id, :user_id, 'Goal', 1000)"
        ),
        {"id": goal_id, "user_id": user_id},
    )
    connection.execute(
        text(
            f"INSERT INTO {_qualified(schema, 'savings_plans')} "
            "(id, user_id, goal_id, start_date, duration_days, daily_amount_kyat) "
            "VALUES (:id, :user_id, :goal_id, DATE '2026-01-01', 1, 100)"
        ),
        {"id": plan_id, "user_id": user_id, "goal_id": goal_id},
    )
    connection.execute(
        text(
            f"INSERT INTO {_qualified(schema, 'savings_plan_days')} "
            "(id, user_id, goal_id, plan_id, day_number, planned_date, "
            "base_amount_kyat, carried_forward_kyat, required_amount_kyat) "
            "VALUES (:id, :user_id, :goal_id, :plan_id, 1, DATE '2026-01-01', 100, 0, 100)"
        ),
        {"id": day_id, "user_id": user_id, "goal_id": goal_id, "plan_id": plan_id},
    )
    connection.execute(
        text(
            f"INSERT INTO {_qualified(schema, 'savings_entries')} "
            "(id, user_id, goal_id, plan_day_id, entry_type, amount_kyat, entry_date) "
            "VALUES (:id, :user_id, :goal_id, :day_id, 'contribution', 100, DATE '2026-01-01')"
        ),
        {"id": entry_id, "user_id": user_id, "goal_id": goal_id, "day_id": day_id},
    )
    savings_correction_id = uuid4()
    connection.execute(
        text(
            f"INSERT INTO {_qualified(schema, 'savings_entries')} "
            "(id, user_id, goal_id, entry_type, amount_kyat, entry_date, "
            "correction_of_entry_id, correction_reason) VALUES "
            "(:id, :user_id, :goal_id, 'withdrawal', 100, DATE '2026-01-01', "
            ":original, 'Correction')"
        ),
        {
            "id": savings_correction_id,
            "user_id": user_id,
            "goal_id": goal_id,
            "original": entry_id,
        },
    )
    _expect_denied(
        connection,
        f"INSERT INTO {_qualified(schema, 'savings_entries')} "
        "(id, user_id, goal_id, entry_type, amount_kyat, entry_date, "
        "correction_of_entry_id, correction_reason) VALUES "
        "(:id, :user_id, :goal_id, 'withdrawal', 200, DATE '2026-01-01', "
        ":original, 'Wrong amount')",
        {
            "id": uuid4(),
            "user_id": user_id,
            "goal_id": goal_id,
            "original": entry_id,
        },
    )
    _expect_denied(
        connection,
        f"DELETE FROM {_qualified(schema, 'savings_entries')} WHERE id = :id",
        {"id": savings_correction_id},
    )
    _expect_denied(
        connection,
        f"UPDATE {_qualified(schema, 'savings_entries')} "
        "SET amount_kyat = 200 WHERE id = :id",
        {"id": entry_id},
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
    replacement_day_id = uuid4()
    connection.execute(
        text(
            f"INSERT INTO {_qualified(schema, 'savings_plan_days')} "
            "(id, user_id, goal_id, plan_id, day_number, planned_date, "
            "base_amount_kyat, carried_forward_kyat, required_amount_kyat) "
            "VALUES (:id, :user_id, :goal_id, :plan_id, 1, "
            "DATE '2026-02-01', 100, 0, 100)"
        ),
        {
            "id": replacement_day_id,
            "user_id": user_id,
            "goal_id": goal_id,
            "plan_id": plan_id,
        },
    )
    connection.commit()
    terminal_day_id = replacement_day_id
    connection.execute(
        text(
            f"UPDATE {_qualified(schema, 'savings_plan_days')} "
            "SET status = 'completed', completion_date = DATE '2026-02-01', "
            "actual_amount_kyat = 100, reconciled_contributions_kyat = 100, "
            "unpaid_carry_forward_kyat = 0 WHERE id = :id"
        ),
        {"id": terminal_day_id},
    )
    _expect_denied(
        connection,
        f"UPDATE {_qualified(schema, 'savings_plan_days')} "
        "SET status = 'pending', completion_date = NULL, "
        "actual_amount_kyat = NULL, reconciled_contributions_kyat = NULL, "
        "unpaid_carry_forward_kyat = NULL WHERE id = :id",
        {"id": terminal_day_id},
    )
    _expect_denied(
        connection,
        f"DELETE FROM {_qualified(schema, 'savings_entries')} WHERE id = :id",
        {"id": entry_id},
    )
    connection.execute(
        text(
            f"UPDATE {_qualified(schema, 'users')} "
            "SET status = 'closed' WHERE id = :id"
        ),
        {"id": user_id},
    )
    _expect_denied(
        connection,
        f"UPDATE {_qualified(schema, 'users')} "
        "SET status = 'active' WHERE id = :id",
        {"id": user_id},
    )

    trip_id, budget_id, spending_id = uuid4(), uuid4(), uuid4()
    connection.execute(
        text(
            f"INSERT INTO {_qualified(schema, 'trips')} "
            "(id, user_id, name, destination, start_date, end_date) "
            "VALUES (:id, :user_id, 'Trip', 'Place', DATE '2026-03-01', DATE '2026-03-02')"
        ),
        {"id": trip_id, "user_id": user_id},
    )
    connection.execute(
        text(
            f"INSERT INTO {_qualified(schema, 'trip_category_budgets')} "
            "(id, user_id, trip_id, category, budget_kyat) "
            "VALUES (:id, :user_id, :trip_id, 'Food', 1000)"
        ),
        {"id": budget_id, "user_id": user_id, "trip_id": trip_id},
    )
    connection.execute(
        text(
            f"INSERT INTO {_qualified(schema, 'trip_spending_entries')} "
            "(id, user_id, trip_id, category_budget_id, category, description, "
            "amount_kyat, spending_date) VALUES "
            "(:id, :user_id, :trip_id, :budget_id, 'Food', 'Meal', 100, DATE '2026-03-01')"
        ),
        {
            "id": spending_id,
            "user_id": user_id,
            "trip_id": trip_id,
            "budget_id": budget_id,
        },
    )
    connection.execute(
        text(
            f"UPDATE {_qualified(schema, 'trip_spending_entries')} "
            "SET category_budget_id = NULL WHERE id = :id"
        ),
        {"id": spending_id},
    )
    connection.execute(
        text(
            f"DELETE FROM {_qualified(schema, 'trip_category_budgets')} "
            "WHERE id = :id"
        ),
        {"id": budget_id},
    )
    second_budget_id, second_spending_id = uuid4(), uuid4()
    connection.execute(
        text(
            f"INSERT INTO {_qualified(schema, 'trip_category_budgets')} "
            "(id, user_id, trip_id, category, budget_kyat) "
            "VALUES (:id, :user_id, :trip_id, 'Lodging', 1000)"
        ),
        {"id": second_budget_id, "user_id": user_id, "trip_id": trip_id},
    )
    connection.execute(
        text(
            f"INSERT INTO {_qualified(schema, 'trip_spending_entries')} "
            "(id, user_id, trip_id, category_budget_id, category, description, "
            "amount_kyat, spending_date) VALUES "
            "(:id, :user_id, :trip_id, :budget_id, 'Lodging', 'Night', 200, DATE '2026-03-01')"
        ),
        {
            "id": second_spending_id,
            "user_id": user_id,
            "trip_id": trip_id,
            "budget_id": second_budget_id,
        },
    )
    connection.execute(
        text(f"DELETE FROM {_qualified(schema, 'trips')} WHERE id = :id"),
        {"id": trip_id},
    )
    assert connection.execute(
        text(
            f"SELECT count(*) FROM {_qualified(schema, 'trip_spending_entries')} "
            "WHERE trip_id = :id"
        ),
        {"id": trip_id},
    ).scalar_one() == 0
    connection.commit()


def test_runtime_cannot_ddl_or_grant_and_cleanup_is_narrow(
    restricted_runtime_db,
) -> None:
    connection, schema, probe_schema = restricted_runtime_db
    _expect_denied(
        connection,
        f'CREATE TABLE "{schema}".runtime_forbidden (id integer)',
    )
    _expect_denied(
        connection,
        f'ALTER TABLE {_qualified(schema, "users")} ADD COLUMN forbidden integer',
    )
    probe_table = f"{probe_schema}.{RUNTIME_GRANT_PROBE}"
    assert connection.execute(
        text("SELECT has_schema_privilege('finpath_app', :schema, 'USAGE')"),
        {"schema": probe_schema},
    ).scalar_one(), "Precondition failed: runtime lacks USAGE on the probe schema"
    assert not connection.execute(
        text("SELECT has_table_privilege('finpath_app', :table, 'SELECT')"),
        {"table": probe_table},
    ).scalar_one(), "Precondition failed: runtime already holds SELECT on the probe"
    assert not connection.execute(
        text(
            "SELECT has_table_privilege("
            "'finpath_app', :table, 'SELECT WITH GRANT OPTION')"
        ),
        {"table": probe_table},
    ).scalar_one(), "Precondition failed: runtime already holds grant option"
    _expect_denied(
        connection,
        f"GRANT SELECT ON {_qualified(probe_schema, RUNTIME_GRANT_PROBE)} TO PUBLIC",
        sqlstate="42501",
    )
    for table_schema, table in (
        (probe_schema, RUNTIME_GRANT_PROBE),
        (schema, "users"),
    ):
        assert not connection.execute(
            text(
                """
                SELECT EXISTS (
                    SELECT 1
                    FROM pg_catalog.aclexplode(
                        COALESCE(
                            relation.relacl,
                            pg_catalog.acldefault('r', relation.relowner)
                        )
                    ) AS acl
                    WHERE acl.grantee = 0 AND acl.privilege_type = 'SELECT'
                )
                FROM pg_class AS relation
                JOIN pg_namespace AS namespace
                  ON namespace.oid = relation.relnamespace
                WHERE namespace.nspname = :schema
                  AND relation.relname = :table
                """
            ),
            {"schema": table_schema, "table": table},
        ).scalar_one(), f"PUBLIC unexpectedly gained SELECT on {table}"
    _expect_denied(
        connection,
        "CREATE ROLE runtime_forbidden",
    )

    user_id = uuid4()
    connection.execute(
        text(
            f"INSERT INTO {_qualified(schema, 'users')} "
            "(id, email, password_hash, timezone) "
            "VALUES (:id, 'cleanup@example.test', 'hash', 'Asia/Yangon')"
        ),
        {"id": user_id},
    )
    loan_id, repayment_id, goal_id, entry_id, audit_id = (
        uuid4(), uuid4(), uuid4(), uuid4(), uuid4()
    )
    connection.execute(
        text(
            f"INSERT INTO {_qualified(schema, 'loans')} "
            "(id, user_id, name, principal_kyat, start_date) "
            "VALUES (:id, :user_id, 'Retained loan', 5000, DATE '2026-01-01')"
        ),
        {"id": loan_id, "user_id": user_id},
    )
    connection.execute(
        text(
            f"INSERT INTO {_qualified(schema, 'loan_repayments')} "
            "(id, user_id, loan_id, amount_kyat, repayment_date) "
            "VALUES (:id, :user_id, :loan_id, 1000, DATE '2026-01-02')"
        ),
        {"id": repayment_id, "user_id": user_id, "loan_id": loan_id},
    )
    connection.execute(
        text(
            f"INSERT INTO {_qualified(schema, 'savings_goals')} "
            "(id, user_id, name, target_kyat) VALUES (:id, :user_id, 'Retained goal', 1000)"
        ),
        {"id": goal_id, "user_id": user_id},
    )
    connection.execute(
        text(
            f"INSERT INTO {_qualified(schema, 'savings_entries')} "
            "(id, user_id, goal_id, entry_type, amount_kyat, entry_date) "
            "VALUES (:id, :user_id, :goal_id, 'contribution', 100, DATE '2026-01-01')"
        ),
        {"id": entry_id, "user_id": user_id, "goal_id": goal_id},
    )
    connection.execute(
        text(
            f"INSERT INTO {_qualified(schema, 'admin_audit_logs')} "
            "(id, actor_type, action, target_type) "
            "VALUES (:id, 'system', 'retained', 'user')"
        ),
        {"id": audit_id},
    )
    expired_token, live_token = uuid4(), uuid4()
    connection.execute(
        text(
            f"INSERT INTO {_qualified(schema, 'auth_action_tokens')} "
            "(id, user_id, token_type, token_hash, expires_at) "
            "VALUES (:expired, :user_id, 'password_reset', :expired_hash, "
            "now() - interval '1 minute'), "
            "(:live, :user_id, 'password_reset', :live_hash, "
            "now() + interval '1 day')"
        ),
        {
            "expired": expired_token,
            "live": live_token,
            "user_id": user_id,
            "expired_hash": "1" * 64,
            "live_hash": "2" * 64,
        },
    )
    expired_session = uuid4()
    recent_session = uuid4()
    within_window_session = uuid4()
    live_session = uuid4()
    connection.execute(
        text(
            f"INSERT INTO {_qualified(schema, 'auth_sessions')} "
            "(id, user_id, session_family_id, refresh_token_hash, refresh_expires_at) "
            "VALUES (:expired, :user_id, :expired, :expired_hash, "
            "now() - interval '31 days'), "
            "(:recent, :user_id, :recent, :recent_hash, "
            "now() - interval '1 minute'), "
            "(:window, :user_id, :window, :window_hash, "
            "now() - interval '29 days'), "
            "(:live, :user_id, :live, :live_hash, now() + interval '1 day')"
        ),
        {
            "expired": expired_session,
            "recent": recent_session,
            "window": within_window_session,
            "live": live_session,
            "user_id": user_id,
            "expired_hash": "expired-session-hash",
            "recent_hash": "recent-session-hash",
            "window_hash": "window-session-hash",
            "live_hash": "live-session-hash",
        },
    )

    in_progress_id, recent_id, expired_id, unexpired_id = (
        uuid4(), uuid4(), uuid4(), uuid4()
    )
    rows = (
        (
            in_progress_id,
            "in_progress",
            None,
            "now() - interval '1 minute'",
            "3" * 64,
        ),
        (
            recent_id,
            "completed",
            "now() - interval '1 hour'",
            "now() - interval '1 minute'",
            "4" * 64,
        ),
        (
            expired_id,
            "completed",
            "now() - interval '25 hours'",
            "now() - interval '1 minute'",
            "5" * 64,
        ),
        (
            # Completed more than 24 hours ago but not yet expired: the
            # cleanup predicate requires BOTH conditions, so this is retained.
            unexpired_id,
            "completed",
            "now() - interval '25 hours'",
            "now() + interval '1 hour'",
            "6" * 64,
        ),
    )
    for record_id, state, completed_at, expiry, key_hash in rows:
        connection.execute(
            text(
                f"INSERT INTO {_qualified(schema, 'idempotency_records')} "
                "(id, principal_scope_hash, http_method, route_key, key_hash, "
                "request_hash, expires_at) "
                f"VALUES (:id, :scope, 'POST', '/test', :key, :request, {expiry})"
            ),
            {
                "id": record_id,
                "scope": "a" * 64,
                "key": key_hash,
                "request": "b" * 64,
            },
        )
        if state == "completed":
            connection.execute(
                text(
                    f"UPDATE {_qualified(schema, 'idempotency_records')} "
                    "SET state = 'completed', response_status = 200, "
                    "response_body = '{}'::jsonb, response_headers = '{}'::jsonb, "
                    f"completed_at = {completed_at} WHERE id = :id"
                ),
                {"id": record_id},
            )

    affected = connection.execute(
        text(
            f"SELECT {_qualified(schema, 'cleanup_expired_operational_records')}()"
        )
    ).scalar_one()
    assert affected == 3
    for table, deleted_id, retained_id in (
        ("auth_action_tokens", expired_token, live_token),
        ("auth_sessions", expired_session, live_session),
    ):
        assert connection.execute(
            text(
                f"SELECT count(*) FROM {_qualified(schema, table)} "
                "WHERE id = :id"
            ),
            {"id": deleted_id},
        ).scalar_one() == 0
        assert connection.execute(
            text(
                f"SELECT count(*) FROM {_qualified(schema, table)} "
                "WHERE id = :id"
            ),
            {"id": retained_id},
        ).scalar_one() == 1
    # Sessions inside the 30-day reuse-detection window survive even though
    # their refresh token has already expired.
    for retained_session in (recent_session, within_window_session):
        assert connection.execute(
            text(
                f"SELECT count(*) FROM {_qualified(schema, 'auth_sessions')} "
                "WHERE id = :id"
            ),
            {"id": retained_session},
        ).scalar_one() == 1
    idem = _qualified(schema, "idempotency_records")
    assert connection.execute(
        text(f"SELECT count(*) FROM {idem} WHERE id = :id"),
        {"id": in_progress_id},
    ).scalar_one() == 1
    assert connection.execute(
        text(f"SELECT count(*) FROM {idem} WHERE id = :id"),
        {"id": recent_id},
    ).scalar_one() == 1
    assert connection.execute(
        text(f"SELECT count(*) FROM {idem} WHERE id = :id"),
        {"id": expired_id},
    ).scalar_one() == 0
    assert connection.execute(
        text(f"SELECT count(*) FROM {idem} WHERE id = :id"),
        {"id": unexpired_id},
    ).scalar_one() == 1
    _expect_denied(
        connection,
        f"DELETE FROM {_qualified(schema, 'users')} WHERE id = :id",
        {"id": user_id},
    )
    for table, record_id in (
        ("loan_repayments", repayment_id),
        ("savings_entries", entry_id),
        ("admin_audit_logs", audit_id),
    ):
        _expect_denied(
            connection,
            f"DELETE FROM {_qualified(schema, table)} WHERE id = :id",
            {"id": record_id},
        )
        assert connection.execute(
            text(
                f"SELECT count(*) FROM {_qualified(schema, table)} "
                "WHERE id = :id"
            ),
            {"id": record_id},
        ).scalar_one() == 1
