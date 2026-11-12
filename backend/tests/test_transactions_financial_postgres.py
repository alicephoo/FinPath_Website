"""PostgreSQL tests for transaction balances and database security boundaries."""

from __future__ import annotations

import asyncio
from uuid import UUID, uuid4

import pytest
from sqlalchemy import text
from sqlalchemy.exc import DBAPIError
from sqlalchemy.orm import Session

from app.api.errors import ApiError
from app.services.transactions import calculate_account_balance
from tests.tx_support import (
    TransactionEnv,
    category_id,
    create_transaction,
    headers,
    new_key,
    register_verify_login,
    set_starting_balance,
    transaction_payload,
)


def _balance(tx_env: TransactionEnv, user_id: str):
    with Session(tx_env.runtime_engine) as session:
        return calculate_account_balance(
            session, user_id=UUID(user_id), as_of_date=None
        )


def test_balance_is_not_configured_until_both_settings_are_present(
    tx_env: TransactionEnv,
):
    async def scenario() -> None:
        async with tx_env.client() as client:
            _, session = await register_verify_login(client, tx_env)
            result = _balance(tx_env, session["user"]["id"])
            assert result.status == "not_configured"
            assert result.starting_balance_kyat is None
            assert result.effective_date is None
            assert result.amount_kyat is None

    asyncio.run(scenario())


def test_balance_uses_strict_effective_date_boundary_and_exact_arithmetic(
    tx_env: TransactionEnv,
):
    salary = category_id(tx_env, "income", "Salary")
    groceries = category_id(tx_env, "expense", "Groceries")

    async def scenario() -> None:
        async with tx_env.client() as client:
            _, session = await register_verify_login(client, tx_env)
            token = session["access_token"]
            user_id = session["user"]["id"]
            set_starting_balance(
                tx_env,
                user_id=user_id,
                starting_balance_kyat=100_000,
                effective_date="2026-11-03",
            )

            for payload in (
                transaction_payload(
                    salary,
                    description="Before boundary",
                    transaction_type="income",
                    amount_kyat=999_999,
                    transaction_date="2026-11-02",
                ),
                transaction_payload(
                    groceries,
                    description="On boundary",
                    amount_kyat=888_888,
                    transaction_date="2026-11-03",
                ),
                transaction_payload(
                    salary,
                    description="After boundary",
                    transaction_type="income",
                    amount_kyat=500_000,
                    transaction_date="2026-11-04",
                ),
                transaction_payload(
                    groceries,
                    description="Later expense",
                    amount_kyat=200_000,
                    transaction_date="2026-11-05",
                ),
            ):
                response = await create_transaction(client, token, payload)
                assert response.status_code == 201, response.text

            result = _balance(tx_env, user_id)
            assert result.status == "configured"
            assert result.starting_balance_kyat == 100_000
            assert result.effective_date.isoformat() == "2026-11-03"
            # Only the 500,000 income and 200,000 expense are strictly after
            # the effective date.
            assert result.amount_kyat == 400_000

    asyncio.run(scenario())


def test_balance_allows_negative_results(tx_env: TransactionEnv):
    groceries = category_id(tx_env, "expense", "Groceries")

    async def scenario() -> None:
        async with tx_env.client() as client:
            _, session = await register_verify_login(client, tx_env)
            token = session["access_token"]
            user_id = session["user"]["id"]
            set_starting_balance(
                tx_env,
                user_id=user_id,
                starting_balance_kyat=100,
                effective_date="2026-01-01",
            )
            response = await create_transaction(
                client,
                token,
                transaction_payload(
                    groceries,
                    description="Overdraft",
                    amount_kyat=250,
                    transaction_date="2026-01-02",
                ),
            )
            assert response.status_code == 201

            assert _balance(tx_env, user_id).amount_kyat == -150

    asyncio.run(scenario())


def test_balance_recalculates_after_transaction_edits_and_deletes(
    tx_env: TransactionEnv,
):
    salary = category_id(tx_env, "income", "Salary")
    groceries = category_id(tx_env, "expense", "Groceries")

    async def scenario() -> None:
        async with tx_env.client() as client:
            _, session = await register_verify_login(client, tx_env)
            token = session["access_token"]
            user_id = session["user"]["id"]
            set_starting_balance(
                tx_env,
                user_id=user_id,
                starting_balance_kyat=100_000,
                effective_date="2026-01-01",
            )

            income = await create_transaction(
                client,
                token,
                transaction_payload(
                    salary,
                    description="Salary",
                    transaction_type="income",
                    amount_kyat=50_000,
                    transaction_date="2026-01-02",
                ),
            )
            assert income.status_code == 201, income.text
            assert _balance(tx_env, user_id).amount_kyat == 150_000

            expense = await create_transaction(
                client,
                token,
                transaction_payload(
                    groceries,
                    description="Market",
                    amount_kyat=20_000,
                    transaction_date="2026-01-03",
                ),
            )
            assert expense.status_code == 201, expense.text
            assert _balance(tx_env, user_id).amount_kyat == 130_000

            edited = await client.patch(
                f"/api/v1/transactions/{expense.json()['data']['id']}",
                json={"amount_kyat": 30_000},
                headers=headers(
                    token=token,
                    if_match=expense.headers["etag"],
                ),
            )
            assert edited.status_code == 200, edited.text
            assert _balance(tx_env, user_id).amount_kyat == 120_000

            deleted_expense = await client.delete(
                f"/api/v1/transactions/{expense.json()['data']['id']}",
                headers=headers(
                    token=token,
                    idem=new_key("delete"),
                    if_match=edited.headers["etag"],
                ),
            )
            assert deleted_expense.status_code == 204, deleted_expense.text
            assert _balance(tx_env, user_id).amount_kyat == 150_000

            deleted_income = await client.delete(
                f"/api/v1/transactions/{income.json()['data']['id']}",
                headers=headers(
                    token=token,
                    idem=new_key("delete"),
                    if_match=income.headers["etag"],
                ),
            )
            assert deleted_income.status_code == 204, deleted_income.text
            assert _balance(tx_env, user_id).amount_kyat == 100_000

    asyncio.run(scenario())


def test_balance_includes_loan_cash_flow_and_savings_allocation(
    tx_env: TransactionEnv,
):
    salary = category_id(tx_env, "income", "Salary")

    async def scenario() -> None:
        async with tx_env.client() as client:
            _, session = await register_verify_login(client, tx_env)
            token = session["access_token"]
            user_id = session["user"]["id"]
            set_starting_balance(
                tx_env,
                user_id=user_id,
                starting_balance_kyat=0,
                effective_date="2026-01-01",
            )

            loan_id = uuid4()
            corrected_repayment_id = uuid4()
            savings_goal_id = uuid4()
            trip_id = uuid4()
            tx_env.execute(
                "INSERT INTO loans (id, user_id, name, principal_kyat, start_date) "
                "VALUES (:id, :user_id, 'Loan', 100000, DATE '2026-01-02')",
                {"id": loan_id, "user_id": user_id},
            )
            tx_env.execute(
                "INSERT INTO loan_repayments "
                "(id, user_id, loan_id, amount_kyat, repayment_date) "
                "VALUES (:id, :user_id, :loan_id, 25000, DATE '2026-01-03')",
                {"id": uuid4(), "user_id": user_id, "loan_id": loan_id},
            )
            # A corrected repayment is no longer a cash outflow.
            tx_env.execute(
                "INSERT INTO loan_repayments "
                "(id, user_id, loan_id, amount_kyat, repayment_date) "
                "VALUES (:id, :user_id, :loan_id, 10000, DATE '2026-01-04')",
                {
                    "id": corrected_repayment_id,
                    "user_id": user_id,
                    "loan_id": loan_id,
                },
            )
            tx_env.execute(
                "INSERT INTO loan_repayment_corrections "
                "(id, user_id, loan_id, repayment_id, type, amount_kyat, "
                "reason, created_by_user_id) "
                "VALUES (:id, :user_id, :loan_id, :repayment_id, 'reversal', "
                "10000, 'Duplicate entry', :user_id)",
                {
                    "id": uuid4(),
                    "user_id": user_id,
                    "loan_id": loan_id,
                    "repayment_id": corrected_repayment_id,
                },
            )
            tx_env.execute(
                "INSERT INTO savings_goals (id, user_id, name, target_kyat) "
                "VALUES (:id, :user_id, 'Goal', 50000)",
                {"id": savings_goal_id, "user_id": user_id},
            )
            tx_env.execute(
                "INSERT INTO savings_entries "
                "(id, user_id, goal_id, entry_type, amount_kyat, entry_date) "
                "VALUES (:id, :user_id, :goal_id, 'contribution', 15000, "
                "DATE '2026-01-04')",
                {"id": uuid4(), "user_id": user_id, "goal_id": savings_goal_id},
            )
            tx_env.execute(
                "INSERT INTO trips "
                "(id, user_id, name, destination, start_date, end_date) "
                "VALUES (:id, :user_id, 'Trip', 'Yangon', DATE '2026-01-05', "
                "DATE '2026-01-06')",
                {"id": trip_id, "user_id": user_id},
            )
            tx_env.execute(
                "INSERT INTO trip_spending_entries "
                "(id, user_id, trip_id, category, description, amount_kyat, "
                "spending_date) VALUES (:id, :user_id, :trip_id, 'Food', "
                "'Lunch', 30000, DATE '2026-01-05')",
                {"id": uuid4(), "user_id": user_id, "trip_id": trip_id},
            )

            # Borrowed principal increases the balance; the unreversed
            # repayment decreases it once. The active savings goal reserves its
            # 50,000 target, while the 15,000 contribution does not deduct
            # twice. The active trip has no allocation, so its actual 30,000
            # spending is the recognized travel expense.
            # 100,000 - 25,000 - 50,000 - 30,000 = -5,000.
            assert _balance(tx_env, user_id).amount_kyat == -5_000

            response = await create_transaction(
                client,
                token,
                transaction_payload(
                    salary,
                    description="Salary",
                    transaction_type="income",
                    amount_kyat=100_000,
                    transaction_date="2026-01-07",
                ),
            )
            assert response.status_code == 201
            assert _balance(tx_env, user_id).amount_kyat == 95_000

    asyncio.run(scenario())


def test_balance_reconciles_loan_principal_edits_and_repayment_corrections(
    tx_env: TransactionEnv,
):
    async def scenario() -> None:
        async with tx_env.client() as client:
            _, session = await register_verify_login(client, tx_env)
            token = session["access_token"]
            user_id = session["user"]["id"]
            set_starting_balance(
                tx_env,
                user_id=user_id,
                starting_balance_kyat=0,
                effective_date="2026-01-01",
            )

            created = await client.post(
                "/api/v1/loans",
                json={
                    "name": "Working loan",
                    "principal_kyat": 100_000,
                    "start_date": "2026-01-02",
                },
                headers=headers(token=token, idem=new_key("loan")),
            )
            assert created.status_code == 201, created.text
            loan_id = created.json()["data"]["id"]
            assert _balance(tx_env, user_id).amount_kyat == 100_000

            edited = await client.patch(
                f"/api/v1/loans/{loan_id}",
                json={"principal_kyat": 60_000},
                headers=headers(
                    token=token,
                    if_match=created.headers["etag"],
                ),
            )
            assert edited.status_code == 200, edited.text
            assert _balance(tx_env, user_id).amount_kyat == 60_000

            repayment = await client.post(
                f"/api/v1/loans/{loan_id}/repayments",
                json={
                    "amount_kyat": 10_000,
                    "repayment_date": "2026-01-03",
                    "note": "Cash payment",
                },
                headers=headers(token=token, idem=new_key("repay")),
            )
            assert repayment.status_code == 201, repayment.text
            repayment_id = repayment.json()["data"]["id"]
            # Repayments are loan cash flow and are not also transaction
            # expenses, so the balance counts this outflow exactly once.
            assert _balance(tx_env, user_id).amount_kyat == 50_000

            correction = await client.post(
                f"/api/v1/loans/{loan_id}/repayments/{repayment_id}/corrections",
                json={"reason": "Recorded twice"},
                headers=headers(token=token, idem=new_key("correction")),
            )
            assert correction.status_code == 201, correction.text
            # A fully reversed repayment is no longer a cash outflow.
            assert _balance(tx_env, user_id).amount_kyat == 60_000

    asyncio.run(scenario())


def test_balance_applies_effective_date_boundary_to_loan_cash_flow(
    tx_env: TransactionEnv,
):
    async def scenario() -> None:
        async with tx_env.client() as client:
            _, session = await register_verify_login(client, tx_env)
            user_id = session["user"]["id"]
            set_starting_balance(
                tx_env,
                user_id=user_id,
                starting_balance_kyat=0,
                effective_date="2026-06-10",
            )

            loans = {
                "before": (uuid4(), 100_000, "2026-06-09"),
                "on": (uuid4(), 200_000, "2026-06-10"),
                "after": (uuid4(), 300_000, "2026-06-11"),
            }
            for loan_id, principal, start_date in loans.values():
                tx_env.execute(
                    "INSERT INTO loans "
                    "(id, user_id, name, principal_kyat, start_date) "
                    "VALUES (:id, :user_id, 'Loan', :principal, :start_date)",
                    {
                        "id": loan_id,
                        "user_id": user_id,
                        "principal": principal,
                        "start_date": start_date,
                    },
                )

            for loan_id, amount, repayment_date in (
                (loans["before"][0], 10_000, "2026-06-09"),
                (loans["on"][0], 20_000, "2026-06-10"),
                (loans["after"][0], 30_000, "2026-06-11"),
            ):
                tx_env.execute(
                    "INSERT INTO loan_repayments "
                    "(id, user_id, loan_id, amount_kyat, repayment_date) "
                    "VALUES (:id, :user_id, :loan_id, :amount, :repayment_date)",
                    {
                        "id": uuid4(),
                        "user_id": user_id,
                        "loan_id": loan_id,
                        "amount": amount,
                        "repayment_date": repayment_date,
                    },
                )

            # Only the loan and repayment strictly after 2026-06-10 count.
            assert _balance(tx_env, user_id).amount_kyat == 270_000

    asyncio.run(scenario())


def test_balance_reports_aggregate_out_of_range(tx_env: TransactionEnv):
    salary = category_id(tx_env, "income", "Salary")

    async def scenario() -> None:
        async with tx_env.client() as client:
            _, session = await register_verify_login(client, tx_env)
            token = session["access_token"]
            user_id = session["user"]["id"]
            set_starting_balance(
                tx_env,
                user_id=user_id,
                starting_balance_kyat=9_223_372_036_854_775_807,
                effective_date="2026-01-01",
            )
            response = await create_transaction(
                client,
                token,
                transaction_payload(
                    salary,
                    description="Overflow",
                    transaction_type="income",
                    amount_kyat=1,
                    transaction_date="2026-01-02",
                ),
            )
            assert response.status_code == 201

            with pytest.raises(ApiError) as raised:
                _balance(tx_env, user_id)
            assert raised.value.status_code == 500
            assert raised.value.code == "aggregate_out_of_range"

    asyncio.run(scenario())


def _expect_denied(connection, statement: str, parameters=None) -> None:
    connection.rollback()
    transaction = connection.begin()
    try:
        connection.execute(text(statement), parameters or {})
    except DBAPIError as exc:
        transaction.rollback()
        sqlstate = getattr(getattr(exc, "orig", None), "sqlstate", None)
        assert sqlstate == "42501", (sqlstate, statement)
    else:
        transaction.rollback()
        raise AssertionError(f"Operation unexpectedly succeeded: {statement}")


def test_runtime_transaction_privileges_and_registration_boundary(
    tx_env: TransactionEnv,
):
    groceries = category_id(tx_env, "expense", "Groceries")

    async def scenario() -> str:
        async with tx_env.client() as client:
            _, session = await register_verify_login(client, tx_env)
            created = await create_transaction(
                client,
                session["access_token"],
                transaction_payload(groceries, description="Privilege probe"),
            )
            assert created.status_code == 201
            return created.json()["data"]["id"]

    transaction_id = asyncio.run(scenario())

    with tx_env.runtime_engine.connect() as connection:
        assert connection.execute(text("SELECT current_user")).scalar_one() == (
            "finpath_app"
        )

        privileges = connection.execute(
            text(
                """
                SELECT
                    has_table_privilege(
                        'finpath_app', 'public.transactions', 'SELECT'
                    ),
                    has_table_privilege(
                        'finpath_app', 'public.transactions', 'INSERT'
                    ),
                    has_table_privilege(
                        'finpath_app', 'public.transactions', 'UPDATE'
                    ),
                    has_table_privilege(
                        'finpath_app', 'public.transactions', 'DELETE'
                    ),
                    has_table_privilege(
                        'finpath_app', 'public.transaction_categories', 'SELECT'
                    ),
                    has_table_privilege(
                        'finpath_app', 'public.transaction_categories', 'INSERT'
                    ),
                    has_table_privilege(
                        'finpath_app', 'public.transaction_categories', 'UPDATE'
                    ),
                    has_table_privilege(
                        'finpath_app', 'public.transaction_categories', 'DELETE'
                    ),
                    has_table_privilege(
                        'finpath_app', 'public.user_roles', 'SELECT'
                    ),
                    has_table_privilege(
                        'finpath_app', 'public.user_roles', 'INSERT'
                    ),
                    has_table_privilege(
                        'finpath_app', 'public.user_roles', 'UPDATE'
                    ),
                    has_table_privilege(
                        'finpath_app', 'public.user_roles', 'DELETE'
                    )
                """
            )
        ).one()
        assert tuple(privileges) == (
            True,
            False,
            False,
            True,
            True,
            False,
            False,
            False,
            True,
            False,
            False,
            False,
        )

        insert_columns = {
            "id",
            "user_id",
            "category_id",
            "type",
            "description",
            "amount_kyat",
            "transaction_date",
        }
        update_columns = {
            "category_id",
            "type",
            "description",
            "amount_kyat",
            "transaction_date",
            "updated_at",
            "revision",
        }
        for column in (
            "id",
            "user_id",
            "category_id",
            "type",
            "description",
            "amount_kyat",
            "transaction_date",
            "created_at",
            "updated_at",
            "revision",
        ):
            assert connection.execute(
                text(
                    "SELECT has_column_privilege("
                    "'finpath_app', 'public.transactions', :column, 'INSERT')"
                ),
                {"column": column},
            ).scalar_one() is (column in insert_columns), column
            assert connection.execute(
                text(
                    "SELECT has_column_privilege("
                    "'finpath_app', 'public.transactions', :column, 'UPDATE')"
                ),
                {"column": column},
            ).scalar_one() is (column in update_columns), column

        assert connection.execute(
            text(
                "SELECT count(*) FROM pg_auth_members AS membership "
                "JOIN pg_roles AS member ON member.oid = membership.member "
                "WHERE member.rolname = 'finpath_app'"
            )
        ).scalar_one() == 0
        assert not connection.execute(
            text(
                "SELECT pg_has_role('finpath_app', 'finpath_migrator', 'MEMBER')"
            )
        ).scalar_one()

        public_privileges = connection.execute(
            text(
                """
                SELECT count(*)
                FROM pg_class AS relation
                CROSS JOIN LATERAL aclexplode(
                    COALESCE(
                        relation.relacl,
                        acldefault('r', relation.relowner)
                    )
                ) AS acl
                WHERE relation.relname IN (
                    'transactions', 'transaction_categories', 'user_roles'
                )
                  AND acl.grantee = 0
                  AND acl.privilege_type IN (
                      'SELECT', 'INSERT', 'UPDATE', 'DELETE'
                  )
                """
            )
        ).scalar_one()
        assert public_privileges == 0

        _expect_denied(
            connection,
            "UPDATE transaction_categories SET is_active = false",
        )
        _expect_denied(
            connection,
            "UPDATE transactions SET user_id = :other WHERE id = :id",
            {"other": uuid4(), "id": transaction_id},
        )
        _expect_denied(
            connection,
            "INSERT INTO user_roles (user_id, role_id) VALUES (:user_id, 2)",
            {"user_id": uuid4()},
        )

    with tx_env.registration_engine.connect() as connection:
        assert connection.execute(text("SELECT current_user")).scalar_one() == (
            "finpath_registration"
        )
        assert not connection.execute(
            text(
                "SELECT has_table_privilege("
                "'finpath_registration', 'public.transactions', 'SELECT')"
            )
        ).scalar_one()
        assert not connection.execute(
            text(
                "SELECT has_table_privilege("
                "'finpath_registration', 'public.transactions', 'INSERT')"
            )
        ).scalar_one()
        assert not connection.execute(
            text(
                "SELECT has_table_privilege("
                "'finpath_registration', 'public.transaction_categories', 'SELECT')"
            )
        ).scalar_one()
        assert connection.execute(
            text(
                "SELECT has_column_privilege("
                "'finpath_registration', 'public.user_roles', 'user_id', 'INSERT')"
            )
        ).scalar_one()
        assert connection.execute(
            text(
                "SELECT has_column_privilege("
                "'finpath_registration', 'public.user_roles', 'role_id', 'INSERT')"
            )
        ).scalar_one()
        assert not connection.execute(
            text(
                "SELECT has_table_privilege("
                "'finpath_registration', 'public.user_roles', 'UPDATE')"
            )
        ).scalar_one()

    # The security probe must not have altered the transaction.
    row = tx_env.one(
        "SELECT description FROM transactions WHERE id = :id",
        {"id": transaction_id},
    )
    assert row is not None
    assert row["description"] == "Privilege probe"
