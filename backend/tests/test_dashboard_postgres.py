"""PostgreSQL tests for the authenticated Dashboard API."""

from __future__ import annotations

import asyncio
from datetime import date, timedelta
from typing import Any

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

TODAY = date.today()
MONTH_START = TODAY.replace(day=1)
BOUNDARY_DATE = MONTH_START - timedelta(days=1)
OLDER_DATE = BOUNDARY_DATE - timedelta(days=1)
CURRENT_INCOME_DATE = MONTH_START
CURRENT_EXPENSE_DATE = MONTH_START + timedelta(days=1)


async def create_loan(
    client,
    token: str,
    *,
    name: str,
    principal_kyat: int,
    start_date: str,
):
    return await client.post(
        "/api/v1/loans",
        json={
            "name": name,
            "principal_kyat": principal_kyat,
            "start_date": start_date,
        },
        headers=headers(token=token, idem=new_key("loan")),
    )


async def record_repayment(
    client,
    token: str,
    loan_id: str,
    *,
    amount_kyat: int,
    repayment_date: str,
):
    return await client.post(
        f"/api/v1/loans/{loan_id}/repayments",
        json={
            "amount_kyat": amount_kyat,
            "repayment_date": repayment_date,
        },
        headers=headers(token=token, idem=new_key("repay")),
    )


async def create_goal(
    client,
    token: str,
    *,
    name: str,
    target_kyat: int,
):
    return await client.post(
        "/api/v1/savings/goals",
        json={"name": name, "target_kyat": target_kyat},
        headers=headers(token=token, idem=new_key("goal")),
    )


async def create_contribution(
    client,
    token: str,
    goal_id: str,
    *,
    amount_kyat: int,
    entry_date: str,
):
    return await client.post(
        f"/api/v1/savings/goals/{goal_id}/entries/contributions",
        json={"amount_kyat": amount_kyat, "entry_date": entry_date},
        headers=headers(token=token, idem=new_key("save")),
    )


def test_dashboard_requires_authentication(tx_env: TransactionEnv):
    async def scenario() -> None:
        async with tx_env.client() as client:
            response = await client.get("/api/v1/dashboard/overview")
            assert response.status_code == 401
            assert response.json()["error"]["code"] == (
                "authentication_required"
            )

    asyncio.run(scenario())


def test_dashboard_empty_data_and_not_configured(
    tx_env: TransactionEnv,
):
    async def scenario() -> None:
        async with tx_env.client() as client:
            _, session = await register_verify_login(client, tx_env)
            response = await client.get(
                "/api/v1/dashboard/overview",
                headers=headers(token=session["access_token"]),
            )
            assert response.status_code == 200, response.text
            data = response.json()["data"]

            balance = data["account_balance"]
            assert balance["status"] == "not_configured"
            assert balance["starting_balance_kyat"] is None
            assert balance["effective_date"] is None
            assert balance["amount_kyat"] is None
            assert balance["as_of_date"] == TODAY.isoformat()

            assert data["all_time_income_kyat"] == 0
            assert data["all_time_expense_kyat"] == 0
            assert data["travel_expense_kyat"] == 0
            assert data["total_expense_kyat"] == 0
            assert data["current_month_income_kyat"] == 0
            assert data["current_month_expense_kyat"] == 0
            assert data["outstanding_loan_principal_kyat"] == 0
            assert data["active_loan_count"] == 0
            assert data["loan_repayments_kyat"] == 0
            assert data["actual_savings_balance_kyat"] == 0
            assert data["savings_target_kyat"] == 0
            assert data["active_savings_goal_count"] == 0
            assert data["savings_goals"] == []
            assert data["recent_transactions"] == []

    asyncio.run(scenario())


def test_dashboard_summaries_recent_transactions_and_ownership(
    tx_env: TransactionEnv,
):
    salary = category_id(tx_env, "income", "Salary")
    groceries = category_id(tx_env, "expense", "Groceries")

    async def scenario() -> None:
        async with tx_env.client() as client:
            _, first = await register_verify_login(client, tx_env)
            _, second = await register_verify_login(client, tx_env)
            first_token = first["access_token"]
            second_token = second["access_token"]
            first_user_id = first["user"]["id"]

            set_starting_balance(
                tx_env,
                user_id=first_user_id,
                starting_balance_kyat=100_000,
                effective_date=BOUNDARY_DATE.isoformat(),
            )

            for payload in (
                transaction_payload(
                    salary,
                    description="Older expense month income",
                    transaction_type="income",
                    amount_kyat=999_999,
                    transaction_date=BOUNDARY_DATE.isoformat(),
                ),
                transaction_payload(
                    groceries,
                    description="Older expense",
                    amount_kyat=1_000,
                    transaction_date=OLDER_DATE.isoformat(),
                ),
                transaction_payload(
                    salary,
                    description="Current income",
                    transaction_type="income",
                    amount_kyat=50_000,
                    transaction_date=CURRENT_INCOME_DATE.isoformat(),
                ),
                transaction_payload(
                    groceries,
                    description="Current expense",
                    amount_kyat=20_000,
                    transaction_date=CURRENT_EXPENSE_DATE.isoformat(),
                ),
            ):
                created = await create_transaction(
                    client, first_token, payload
                )
                assert created.status_code == 201, created.text

            loan = await create_loan(
                client,
                first_token,
                name="Car loan",
                principal_kyat=100_000,
                start_date=MONTH_START.isoformat(),
            )
            assert loan.status_code == 201, loan.text
            loan_id = loan.json()["data"]["id"]
            repayment = await record_repayment(
                client,
                first_token,
                loan_id,
                amount_kyat=10_000,
                repayment_date=CURRENT_INCOME_DATE.isoformat(),
            )
            assert repayment.status_code == 201, repayment.text

            paid_loan = await create_loan(
                client,
                first_token,
                name="Paid loan",
                principal_kyat=50_000,
                start_date=MONTH_START.isoformat(),
            )
            assert paid_loan.status_code == 201, paid_loan.text
            paid_loan_id = paid_loan.json()["data"]["id"]
            paid_repayment = await record_repayment(
                client,
                first_token,
                paid_loan_id,
                amount_kyat=50_000,
                repayment_date=CURRENT_INCOME_DATE.isoformat(),
            )
            assert paid_repayment.status_code == 201, paid_repayment.text

            goal = await create_goal(
                client,
                first_token,
                name="Emergency fund",
                target_kyat=100_000,
            )
            assert goal.status_code == 201, goal.text
            goal_id = goal.json()["data"]["id"]
            contribution = await create_contribution(
                client,
                first_token,
                goal_id,
                amount_kyat=30_000,
                entry_date=CURRENT_INCOME_DATE.isoformat(),
            )
            assert contribution.status_code == 201, contribution.text

            empty_goal = await create_goal(
                client,
                first_token,
                name="New laptop",
                target_kyat=50_000,
            )
            assert empty_goal.status_code == 201, empty_goal.text

            # A second user's records must never appear in the first user's
            # dashboard totals or recent transactions.
            second_user_id = second["user"]["id"]
            set_starting_balance(
                tx_env,
                user_id=second_user_id,
                starting_balance_kyat=5_000,
                effective_date=BOUNDARY_DATE.isoformat(),
            )
            second_transaction = await create_transaction(
                client,
                second_token,
                transaction_payload(
                    salary,
                    description="Second user income",
                    transaction_type="income",
                    amount_kyat=777_777,
                    transaction_date=CURRENT_INCOME_DATE.isoformat(),
                ),
            )
            assert second_transaction.status_code == 201
            second_loan = await create_loan(
                client,
                second_token,
                name="Second loan",
                principal_kyat=200_000,
                start_date=MONTH_START.isoformat(),
            )
            assert second_loan.status_code == 201
            second_goal = await create_goal(
                client,
                second_token,
                name="Second goal",
                target_kyat=40_000,
            )
            assert second_goal.status_code == 201
            second_goal_id = second_goal.json()["data"]["id"]
            second_contribution = await create_contribution(
                client,
                second_token,
                second_goal_id,
                amount_kyat=5_000,
                entry_date=CURRENT_INCOME_DATE.isoformat(),
            )
            assert second_contribution.status_code == 201

            response = await client.get(
                "/api/v1/dashboard/overview?recent_limit=2",
                headers=headers(token=first_token),
            )
            assert response.status_code == 200, response.text
            data = response.json()["data"]

            balance = data["account_balance"]
            assert balance["status"] == "configured"
            assert balance["starting_balance_kyat"] == 100_000
            assert balance["effective_date"] == BOUNDARY_DATE.isoformat()
            # The boundary-date transaction is included because the starting
            # balance is the opening balance at the beginning of that date.
            # Only the older transaction is excluded.
            #
            # 100,000 starting
            # + 999,999 income dated exactly on the boundary
            # + 50,000 income - 20,000 expense
            # + 100,000 car-loan proceeds - 10,000 car-loan repayment
            # + 50,000 paid-loan proceeds - 50,000 paid-loan repayment
            # - 150,000 savings allocations (100,000 + 50,000 targets)
            assert balance["amount_kyat"] == 1_069_999
            assert balance["as_of_date"] == TODAY.isoformat()

            # All-time transaction totals include every transaction but no
            # loan repayment or savings allocation/contribution.
            assert data["all_time_income_kyat"] == 1_049_999
            assert data["all_time_expense_kyat"] == 21_000
            assert data["travel_expense_kyat"] == 0
            assert data["total_expense_kyat"] == 21_000
            assert data["current_month_income_kyat"] == 50_000
            assert data["current_month_expense_kyat"] == 20_000

            assert data["outstanding_loan_principal_kyat"] == 90_000
            assert data["active_loan_count"] == 1
            assert data["loan_repayments_kyat"] == 60_000

            assert data["actual_savings_balance_kyat"] == 30_000
            assert data["savings_target_kyat"] == 150_000
            assert data["active_savings_goal_count"] == 2
            goals = {item["name"]: item for item in data["savings_goals"]}
            assert set(goals) == {"Emergency fund", "New laptop"}
            assert goals["Emergency fund"]["actual_balance_kyat"] == 30_000
            assert goals["Emergency fund"]["remaining_kyat"] == 70_000
            assert goals["New laptop"]["actual_balance_kyat"] == 0
            assert goals["New laptop"]["remaining_kyat"] == 50_000

            recent = data["recent_transactions"]
            assert [item["description"] for item in recent] == [
                "Current expense",
                "Current income",
            ]
            assert all(item["amount_kyat"] > 0 for item in recent)

            default_response = await client.get(
                "/api/v1/dashboard/overview",
                headers=headers(token=first_token),
            )
            assert default_response.status_code == 200
            default_recent = default_response.json()["data"][
                "recent_transactions"
            ]
            assert [item["description"] for item in default_recent] == [
                "Current expense",
                "Current income",
                "Older expense month income",
                "Older expense",
            ]

            second_response = await client.get(
                "/api/v1/dashboard/overview",
                headers=headers(token=second_token),
            )
            assert second_response.status_code == 200
            second_data = second_response.json()["data"]
            assert second_data["account_balance"]["status"] == "configured"
            # 5,000 starting + 777,777 income + 200,000 loan proceeds
            # - 40,000 savings allocation target.
            assert second_data["account_balance"]["amount_kyat"] == 942_777
            assert second_data["all_time_income_kyat"] == 777_777
            assert second_data["all_time_expense_kyat"] == 0
            assert second_data["outstanding_loan_principal_kyat"] == 200_000
            assert second_data["active_loan_count"] == 1
            assert second_data["actual_savings_balance_kyat"] == 5_000
            assert second_data["savings_target_kyat"] == 40_000
            assert second_data["active_savings_goal_count"] == 1
            assert [
                item["description"]
                for item in second_data["recent_transactions"]
            ] == ["Second user income"]

    asyncio.run(scenario())


def test_dashboard_recent_limit_bounds(tx_env: TransactionEnv):
    async def scenario() -> None:
        async with tx_env.client() as client:
            _, session = await register_verify_login(client, tx_env)
            token = session["access_token"]

            too_small = await client.get(
                "/api/v1/dashboard/overview?recent_limit=0",
                headers=headers(token=token),
            )
            assert too_small.status_code == 422
            assert too_small.json()["error"]["code"] == "validation_error"

            too_large = await client.get(
                "/api/v1/dashboard/overview?recent_limit=51",
                headers=headers(token=token),
            )
            assert too_large.status_code == 422
            assert too_large.json()["error"]["code"] == "validation_error"

            maximum = await client.get(
                "/api/v1/dashboard/overview?recent_limit=50",
                headers=headers(token=token),
            )
            assert maximum.status_code == 200
            assert maximum.json()["data"]["recent_transactions"] == []

    asyncio.run(scenario())
