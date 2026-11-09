import sqlite3

import pytest
from sqlalchemy import (
    BigInteger,
    CHAR,
    CheckConstraint,
    Date,
    DateTime,
    SmallInteger,
    UniqueConstraint,
)
from sqlalchemy.dialects.postgresql import JSONB, UUID as PostgreSQLUUID
from sqlalchemy.dialects.postgresql import dialect
from sqlalchemy.schema import CreateIndex, CreateTable

from app.db.base import Base
import app.models

EXPECTED_TABLES = {
    "users",
    "roles",
    "user_roles",
    "admin_audit_logs",
    "auth_sessions",
    "auth_action_tokens",
    "idempotency_records",
    "user_financial_settings",
    "transaction_categories",
    "transactions",
    "loans",
    "loan_repayments",
    "loan_repayment_corrections",
    "savings_goals",
    "savings_plans",
    "savings_plan_days",
    "savings_entries",
    "trips",
    "trip_category_budgets",
    "trip_spending_entries",
}

EXPECTED_NULLABLE_COLUMNS = {
    "users": {"email_verified_at", "pending_email"},
    "roles": {"description"},
    "user_roles": {"granted_by_user_id"},
    "admin_audit_logs": {
        "actor_user_id",
        "target_user_id",
        "target_id",
        "details",
    },
    "auth_sessions": {
        "last_refreshed_at",
        "revoked_at",
        "replaced_by_session_id",
    },
    "auth_action_tokens": {"target_email", "consumed_at"},
    "idempotency_records": {
        "owner_user_id",
        "response_status",
        "response_body",
        "response_headers",
        "completed_at",
    },
    "user_financial_settings": {"starting_balance_kyat", "effective_date"},
    "transaction_categories": set(),
    "transactions": set(),
    "loans": set(),
    "loan_repayments": {"note"},
    "loan_repayment_corrections": set(),
    "savings_goals": {"tone", "archived_at"},
    "savings_plans": set(),
    "savings_plan_days": {
        "completion_date",
        "actual_amount_kyat",
        "reconciled_contributions_kyat",
        "unpaid_carry_forward_kyat",
    },
    "savings_entries": {
        "plan_day_id",
        "note",
        "correction_of_entry_id",
        "correction_reason",
    },
    "trips": set(),
    "trip_category_budgets": set(),
    "trip_spending_entries": {"category_budget_id"},
}


def _foreign_keys(table_name: str) -> set[tuple[tuple[str, ...], str, str | None]]:
    return {
        (
            tuple(element.parent.name for element in constraint.elements),
            constraint.elements[0].column.table.name,
            constraint.ondelete,
        )
        for constraint in Base.metadata.tables[table_name].foreign_key_constraints
    }


def _check_names(table_name: str) -> set[str]:
    return {
        constraint.name
        for constraint in Base.metadata.tables[table_name].constraints
        if isinstance(constraint, CheckConstraint)
    }


def _constraint_names(table_name: str) -> set[str]:
    return {
        constraint.name
        for constraint in Base.metadata.tables[table_name].constraints
    }


def test_architecture_model_tables_are_registered() -> None:
    assert set(Base.metadata.tables) == EXPECTED_TABLES
    assert len(Base.metadata.tables) == 20


def test_user_status_check_allows_only_approved_statuses() -> None:
    constraint = next(
        check
        for check in Base.metadata.tables["users"].constraints
        if isinstance(check, CheckConstraint)
        and check.name == "ck_users_status"
    )
    assert str(constraint.sqltext) == (
        "status IN ('active', 'suspended', 'closed')"
    )

    connection = sqlite3.connect(":memory:")
    connection.execute(
        "CREATE TABLE user_status_test ("
        "status TEXT NOT NULL, "
        f"CHECK ({constraint.sqltext}))"
    )
    for status in ("active", "suspended", "closed"):
        connection.execute(
            "INSERT INTO user_status_test (status) VALUES (?)",
            (status,),
        )

    with pytest.raises(sqlite3.IntegrityError):
        connection.execute(
            "INSERT INTO user_status_test (status) VALUES (?)",
            ("pending",),
        )
    connection.close()


def test_required_columns_have_explicit_architecture_nullability() -> None:
    for table_name, table in Base.metadata.tables.items():
        actual_nullable = {
            column.name for column in table.columns if column.nullable
        }
        assert actual_nullable == EXPECTED_NULLABLE_COLUMNS[table_name], table_name


def test_primary_keys_and_financial_column_types() -> None:
    assert isinstance(Base.metadata.tables["roles"].c.id.type, SmallInteger)
    assert set(Base.metadata.tables["user_roles"].primary_key.columns.keys()) == {
        "user_id",
        "role_id",
    }

    for table in Base.metadata.tables.values():
        if "id" in table.c and table.name != "roles":
            assert isinstance(table.c.id.type, PostgreSQLUUID), table.name

        for column in table.columns:
            if column.name.endswith("_kyat"):
                assert isinstance(column.type, BigInteger), (table.name, column.name)


def test_financial_dates_and_event_timestamps_use_distinct_types() -> None:
    expected_dates = {
        "user_financial_settings": {"effective_date"},
        "transactions": {"transaction_date"},
        "loans": {"start_date"},
        "loan_repayments": {"repayment_date"},
        "savings_plans": {"start_date"},
        "savings_plan_days": {"planned_date", "completion_date"},
        "savings_entries": {"entry_date"},
        "trips": {"start_date", "end_date"},
        "trip_spending_entries": {"spending_date"},
    }

    for table_name, column_names in expected_dates.items():
        for column_name in column_names:
            column_type = Base.metadata.tables[table_name].c[column_name].type
            assert isinstance(column_type, Date)
            assert not isinstance(column_type, DateTime)

    expected_timestamps = {
        "users": {
            "email_verified_at",
            "created_at",
            "updated_at",
        },
        "user_roles": {"granted_at"},
        "admin_audit_logs": {"occurred_at"},
        "auth_sessions": {
            "created_at",
            "refresh_expires_at",
            "last_refreshed_at",
            "revoked_at",
        },
        "auth_action_tokens": {
            "created_at",
            "expires_at",
            "consumed_at",
        },
        "idempotency_records": {"created_at", "expires_at", "completed_at"},
        "user_financial_settings": {"updated_at"},
        "transactions": {"created_at", "updated_at"},
        "loans": {"created_at", "updated_at"},
        "loan_repayments": {"created_at"},
        "loan_repayment_corrections": {"created_at"},
        "savings_goals": {"archived_at", "created_at", "updated_at"},
        "savings_plans": {"created_at", "updated_at"},
        "savings_entries": {"created_at"},
        "trips": {"created_at", "updated_at"},
        "trip_category_budgets": {"created_at", "updated_at"},
        "trip_spending_entries": {"created_at", "updated_at"},
    }

    for table_name, column_names in expected_timestamps.items():
        for column_name in column_names:
            column_type = Base.metadata.tables[table_name].c[column_name].type
            assert isinstance(column_type, DateTime)
            assert column_type.timezone is True

    for table in Base.metadata.tables.values():
        for column in table.columns:
            if isinstance(column.type, DateTime):
                assert column.type.timezone is True, (
                    table.name,
                    column.name,
                )


def test_ownership_composite_foreign_keys_and_delete_actions() -> None:
    expected_foreign_keys = {
        "user_roles": {
            (("user_id",), "users", "RESTRICT"),
            (("role_id",), "roles", "RESTRICT"),
            (("granted_by_user_id",), "users", "SET NULL"),
        },
        "admin_audit_logs": {
            (("actor_user_id",), "users", "RESTRICT"),
            (("target_user_id",), "users", "SET NULL"),
        },
        "auth_sessions": {
            (("user_id",), "users", "CASCADE"),
            (("replaced_by_session_id",), "auth_sessions", "SET NULL"),
        },
        "auth_action_tokens": {(("user_id",), "users", "CASCADE")},
        "idempotency_records": {(("owner_user_id",), "users", "SET NULL")},
        "user_financial_settings": {(("user_id",), "users", "RESTRICT")},
        "transactions": {
            (("user_id",), "users", "RESTRICT"),
            (("category_id", "type"), "transaction_categories", "RESTRICT"),
        },
        "loans": {(("user_id",), "users", "RESTRICT")},
        "loan_repayments": {
            (("user_id",), "users", "RESTRICT"),
            (("loan_id", "user_id"), "loans", "RESTRICT"),
        },
        "loan_repayment_corrections": {
            (("user_id",), "users", "RESTRICT"),
            (("created_by_user_id",), "users", "RESTRICT"),
            (("loan_id", "user_id"), "loans", "RESTRICT"),
            (
                ("repayment_id", "loan_id", "user_id"),
                "loan_repayments",
                "RESTRICT",
            ),
        },
        "savings_goals": {(("user_id",), "users", "RESTRICT")},
        "savings_plans": {
            (("user_id",), "users", "RESTRICT"),
            (("goal_id", "user_id"), "savings_goals", "RESTRICT"),
        },
        "savings_plan_days": {
            (("user_id",), "users", "RESTRICT"),
            (("goal_id", "user_id"), "savings_goals", "RESTRICT"),
            (("plan_id", "goal_id", "user_id"), "savings_plans", "RESTRICT"),
        },
        "savings_entries": {
            (("user_id",), "users", "RESTRICT"),
            (("goal_id", "user_id"), "savings_goals", "RESTRICT"),
            (
                ("user_id", "goal_id", "plan_day_id"),
                "savings_plan_days",
                None,
            ),
            (
                ("user_id", "goal_id", "correction_of_entry_id"),
                "savings_entries",
                None,
            ),
        },
        "trips": {(("user_id",), "users", "RESTRICT")},
        "trip_category_budgets": {
            (("user_id",), "users", "RESTRICT"),
            (("trip_id", "user_id"), "trips", "CASCADE"),
        },
        "trip_spending_entries": {
            (("user_id",), "users", "RESTRICT"),
            (("trip_id", "user_id"), "trips", "CASCADE"),
            (
                ("trip_id", "category_budget_id"),
                "trip_category_budgets",
                None,
            ),
        },
    }

    for table_name, expected in expected_foreign_keys.items():
        assert _foreign_keys(table_name) == expected, table_name

    grantor_fk = next(
        fk
        for fk in Base.metadata.tables["user_roles"].foreign_key_constraints
        if {element.parent.name for element in fk.elements}
        == {"granted_by_user_id"}
    )
    assert grantor_fk.ondelete == "SET NULL"


def test_required_unique_checks_and_indexes_are_present() -> None:
    required_indexes = {
        "users": {
            "uq_users_email_lower",
            "uq_users_pending_email_lower",
            "ix_users_status",
        },
        "user_roles": {"ix_user_roles_role_id_user_id"},
        "admin_audit_logs": {
            "ix_admin_audit_logs_actor_user_id_occurred_at",
            "ix_admin_audit_logs_target_user_id_occurred_at",
        },
        "auth_sessions": {
            "ix_auth_sessions_user_id_revoked_at_refresh_expires_at",
            "ix_auth_sessions_session_family_id_refresh_expires_at",
        },
        "auth_action_tokens": {
            "ix_auth_action_tokens_user_id_token_type_expires_at",
        },
        "idempotency_records": {"ix_idempotency_records_expires_at"},
        "transaction_categories": {
            "uq_transaction_categories_type_name_lower",
        },
        "transactions": {
            "ix_transactions_user_id_transaction_date_id",
            "ix_transactions_user_id_category_id_transaction_date",
        },
        "loans": {"ix_loans_user_id_start_date"},
        "loan_repayments": {
            "ix_loan_repayments_user_id_loan_id_repayment_date",
        },
        "loan_repayment_corrections": {
            "ix_loan_repayment_corrections_user_id_loan_id_created_at",
        },
        "savings_goals": {
            "ix_savings_goals_user_id_status_created_at",
        },
        "savings_plan_days": {
            "ix_savings_plan_days_user_id_goal_id_planned_date",
        },
        "savings_entries": {
            "uq_savings_entries_correction_of_entry_id",
            "ix_savings_entries_user_id_goal_id_entry_date_id",
            "ix_savings_entries_user_id_goal_id_plan_day_id",
        },
        "trips": {"ix_trips_user_id_start_date_end_date"},
        "trip_category_budgets": {
            "ix_trip_category_budgets_user_id_trip_id",
            "uq_trip_category_budgets_trip_category_lower",
        },
        "trip_spending_entries": {
            "ix_trip_spending_entries_user_id_trip_id_spending_date",
            "ix_trip_spending_entries_trip_id_category_budget_id",
        },
    }

    for table_name, expected in required_indexes.items():
        actual = {
            index.name for index in Base.metadata.tables[table_name].indexes
        }
        assert expected <= actual, table_name

    expected_checks = {
        "users": {"ck_users_status"},
        "admin_audit_logs": {
            "ck_admin_audit_logs_actor_type",
            "ck_admin_audit_logs_actor_user_id_matches_actor_type",
        },
        "auth_action_tokens": {
            "ck_auth_action_tokens_token_type",
            "ck_auth_action_tokens_target_email_for_email_change",
        },
        "idempotency_records": {
            "ck_idempotency_records_state",
            "ck_idempotency_records_response_matches_state",
        },
        "user_financial_settings": {
            "ck_user_financial_settings_starting_balance_and_date_set_together",
        },
        "transaction_categories": {"ck_transaction_categories_type"},
        "transactions": {
            "ck_transactions_type",
            "ck_transactions_amount_positive",
        },
        "loans": {"ck_loans_principal_positive"},
        "loan_repayments": {"ck_loan_repayments_amount_positive"},
        "loan_repayment_corrections": {
            "ck_loan_repayment_corrections_type",
            "ck_loan_repayment_corrections_amount_positive",
            "ck_loan_repayment_corrections_created_by_user_matches_owner",
        },
        "savings_goals": {
            "ck_savings_goals_target_positive",
            "ck_savings_goals_status",
            "ck_savings_goals_archive_timestamp_matches_status",
        },
        "savings_plans": {
            "ck_savings_plans_duration_days",
            "ck_savings_plans_daily_amount_positive",
        },
        "savings_plan_days": {
            "ck_savings_plan_days_day_number",
            "ck_savings_plan_days_base_amount_positive",
            "ck_savings_plan_days_carried_forward_nonnegative",
            "ck_savings_plan_days_required_amount_positive",
            "ck_savings_plan_days_status",
            "ck_savings_plan_days_terminal_snapshot_matches_status",
            "ck_savings_plan_days_reconciled_contributions_nonnegative",
            "ck_savings_plan_days_unpaid_carry_forward_nonnegative",
        },
        "savings_entries": {
            "ck_savings_entries_entry_type",
            "ck_savings_entries_amount_positive",
            "ck_savings_entries_correction_fields_consistent",
            "ck_savings_entries_correction_reason_nonempty",
            "ck_savings_entries_plan_day_only_for_contributions",
        },
        "trips": {"ck_trips_date_order", "ck_trips_status_valid"},
        "trip_category_budgets": {
            "ck_trip_category_budgets_budget_nonnegative",
        },
        "trip_spending_entries": {
            "ck_trip_spending_entries_amount_positive",
        },
    }
    for table_name, expected in expected_checks.items():
        assert expected <= _check_names(table_name), table_name

    expected_uniques = {
        "roles": {("name",)},
        "auth_sessions": {
            ("replaced_by_session_id",),
            ("refresh_token_hash",),
        },
        "auth_action_tokens": {("token_hash",)},
        "idempotency_records": {
            (
                "principal_scope_hash",
                "http_method",
                "route_key",
                "key_hash",
            ),
        },
        "transaction_categories": {("id", "type")},
        "transactions": {("id", "user_id")},
        "loans": {("id", "user_id")},
        "loan_repayments": {("id", "loan_id", "user_id")},
        "loan_repayment_corrections": {("repayment_id",)},
        "savings_goals": {("id", "user_id")},
        "savings_plans": {
            ("goal_id",),
            ("id", "goal_id", "user_id"),
        },
        "savings_plan_days": {
            ("plan_id", "day_number"),
            ("plan_id", "planned_date"),
            ("user_id", "goal_id", "id"),
        },
        "savings_entries": {("user_id", "goal_id", "id")},
        "trips": {("id", "user_id")},
        "trip_category_budgets": {("trip_id", "id")},
    }
    for table_name, expected in expected_uniques.items():
        actual = {
            tuple(column.name for column in constraint.columns)
            for constraint in Base.metadata.tables[table_name].constraints
            if isinstance(constraint, UniqueConstraint)
        }
        assert expected <= actual, table_name


def test_revision_columns_are_bigint_and_pg_ddl_compiles() -> None:
    for table_name in [
        "users",
        "user_financial_settings",
        "transactions",
        "loans",
        "loan_repayments",
        "savings_goals",
        "savings_plans",
        "trips",
        "trip_category_budgets",
        "trip_spending_entries",
    ]:
        revision = Base.metadata.tables[table_name].c.revision
        assert isinstance(revision.type, BigInteger), table_name
        assert revision.server_default is not None, table_name

    pg_dialect = dialect()
    for table in Base.metadata.sorted_tables:
        str(CreateTable(table).compile(dialect=pg_dialect))
        for index in table.indexes:
            str(CreateIndex(index).compile(dialect=pg_dialect))


def test_idempotency_record_fields_constraints_and_types() -> None:
    table = Base.metadata.tables["idempotency_records"]

    assert isinstance(table.c.id.type, PostgreSQLUUID)
    assert isinstance(table.c.principal_scope_hash.type, CHAR)
    assert isinstance(table.c.key_hash.type, CHAR)
    assert isinstance(table.c.request_hash.type, CHAR)
    assert table.c.principal_scope_hash.type.length == 64
    assert table.c.key_hash.type.length == 64
    assert table.c.request_hash.type.length == 64
    assert table.c.http_method.type.length == 8
    assert table.c.route_key.type.length == 200
    assert isinstance(table.c.response_status.type, SmallInteger)
    assert isinstance(table.c.response_body.type, JSONB)
    assert isinstance(table.c.response_headers.type, JSONB)
    assert table.c.state.server_default is not None
    assert table.c.owner_user_id.nullable is True
    assert table.c.response_status.nullable is True
    assert table.c.response_body.nullable is True
    assert table.c.completed_at.nullable is True


def test_idempotency_state_check_enforces_complete_response_shape() -> None:
    constraint = next(
        check
        for check in Base.metadata.tables["idempotency_records"].constraints
        if isinstance(check, CheckConstraint)
        and check.name == "ck_idempotency_records_response_matches_state"
    )
    connection = sqlite3.connect(":memory:")
    connection.execute(
        "CREATE TABLE idempotency_state_test ("
        "state TEXT NOT NULL, response_status INTEGER, response_body TEXT, "
        "response_headers TEXT, completed_at TEXT, "
        f"CHECK ({constraint.sqltext}))"
    )

    connection.execute(
        "INSERT INTO idempotency_state_test (state) VALUES ('in_progress')"
    )
    connection.execute(
        "INSERT INTO idempotency_state_test VALUES "
        "('completed', 200, '{}', '{}', '2026-11-03T00:00:00Z')"
    )

    invalid_rows = [
        ("in_progress", 200, None, None, None),
        ("in_progress", None, "{}", None, None),
        ("in_progress", None, None, "{}", None),
        ("in_progress", None, None, None, "2026-11-03T00:00:00Z"),
        ("completed", None, "{}", "{}", "2026-11-03T00:00:00Z"),
        ("completed", 200, None, "{}", "2026-11-03T00:00:00Z"),
        ("completed", 200, "{}", None, "2026-11-03T00:00:00Z"),
        ("completed", 200, "{}", "{}", None),
    ]
    for row in invalid_rows:
        with pytest.raises(sqlite3.IntegrityError):
            connection.execute(
                "INSERT INTO idempotency_state_test VALUES (?, ?, ?, ?, ?)",
                row,
            )
        connection.rollback()
    connection.close()


def test_loan_repayment_correction_constraints_and_types() -> None:
    table = Base.metadata.tables["loan_repayment_corrections"]

    assert isinstance(table.c.id.type, PostgreSQLUUID)
    assert isinstance(table.c.amount_kyat.type, BigInteger)
    assert isinstance(table.c.created_at.type, DateTime)
    assert table.c.created_at.type.timezone is True
    assert table.c.reason.type.length == 200
    assert table.c.amount_kyat.nullable is False
    assert table.c.created_by_user_id.nullable is False

    repayment_fk = next(
        fk
        for fk in table.foreign_key_constraints
        if {element.parent.name for element in fk.elements}
        == {"repayment_id", "loan_id", "user_id"}
    )
    assert all(
        element.column.table.name == "loan_repayments"
        for element in repayment_fk.elements
    )
    assert repayment_fk.ondelete == "RESTRICT"

    loan_fk = next(
        fk
        for fk in table.foreign_key_constraints
        if {element.parent.name for element in fk.elements}
        == {"loan_id", "user_id"}
    )
    assert all(
        element.column.table.name == "loans"
        for element in loan_fk.elements
    )
    assert loan_fk.ondelete == "RESTRICT"


@pytest.mark.parametrize("amount", [1])
def test_loan_repayment_correction_positive_amount_is_valid(amount: int) -> None:
    constraint = next(
        check
        for check in Base.metadata.tables[
            "loan_repayment_corrections"
        ].constraints
        if isinstance(check, CheckConstraint)
        and check.name == "ck_loan_repayment_corrections_amount_positive"
    )
    connection = sqlite3.connect(":memory:")
    connection.execute(
        "CREATE TABLE correction_amount_test ("
        "amount_kyat INTEGER NOT NULL, "
        f"CHECK ({constraint.sqltext}))"
    )
    connection.execute(
        "INSERT INTO correction_amount_test (amount_kyat) VALUES (?)",
        (amount,),
    )
    connection.close()


@pytest.mark.parametrize("amount", [0, -1])
def test_loan_repayment_correction_nonpositive_amount_is_rejected(
    amount: int,
) -> None:
    constraint = next(
        check
        for check in Base.metadata.tables[
            "loan_repayment_corrections"
        ].constraints
        if isinstance(check, CheckConstraint)
        and check.name == "ck_loan_repayment_corrections_amount_positive"
    )
    connection = sqlite3.connect(":memory:")
    connection.execute(
        "CREATE TABLE correction_amount_test ("
        "amount_kyat INTEGER NOT NULL, "
        f"CHECK ({constraint.sqltext}))"
    )
    with pytest.raises(sqlite3.IntegrityError):
        connection.execute(
            "INSERT INTO correction_amount_test (amount_kyat) VALUES (?)",
            (amount,),
        )
    connection.close()


def test_one_plan_per_goal_constraint_matches_rebuild_lifecycle() -> None:
    plan_table = Base.metadata.tables["savings_plans"]
    goal_uniques = {
        tuple(column.name for column in constraint.columns)
        for constraint in plan_table.constraints
        if isinstance(constraint, UniqueConstraint)
    }

    assert ("goal_id",) in goal_uniques
    assert ("id", "goal_id", "user_id") in goal_uniques


def test_savings_correction_can_reference_another_correction() -> None:
    entries = Base.metadata.tables["savings_entries"]
    correction_fk = next(
        fk
        for fk in entries.foreign_key_constraints
        if {element.parent.name for element in fk.elements}
        == {"user_id", "goal_id", "correction_of_entry_id"}
    )
    correction_index = next(
        index
        for index in entries.indexes
        if index.name == "uq_savings_entries_correction_of_entry_id"
    )

    assert all(
        element.column.table is entries
        for element in correction_fk.elements
    )
    assert correction_index.unique is True
    assert str(correction_index.dialect_options["postgresql"]["where"]) == (
        "correction_of_entry_id IS NOT NULL"
    )


def test_trip_models_are_registered_once() -> None:
    assert Base.metadata.tables["trips"].c.user_id.nullable is False
    assert Base.metadata.tables["trips"].c.status.nullable is False
    assert Base.metadata.tables["trip_category_budgets"].c.trip_id.nullable is False
    assert (
        Base.metadata.tables["trip_spending_entries"].c.trip_id.nullable is False
    )
