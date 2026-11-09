"""PostgreSQL tests for the owner-scoped report and CSV endpoints."""

from __future__ import annotations

import asyncio
from datetime import date

from tests.tx_support import (
    TransactionEnv,
    category_id,
    create_transaction,
    headers,
    register_verify_login,
    transaction_payload,
)


async def fetch_report(client, token: str, path: str):
    return await client.get(path, headers=headers(token=token))


def test_report_endpoints_require_authentication(tx_env: TransactionEnv):
    async def scenario() -> None:
        async with tx_env.client() as client:
            for path in (
                "/api/v1/reports/cash-flow",
                "/api/v1/reports/expenses-by-category",
                "/api/v1/reports/export.csv",
            ):
                response = await client.get(path)
                assert response.status_code == 401, (path, response.text)
                assert response.json()["error"]["code"] == (
                    "authentication_required"
                )

    asyncio.run(scenario())


def test_cash_flow_respects_inclusive_ranges_and_empty_months(
    tx_env: TransactionEnv,
):
    async def scenario() -> None:
        async with tx_env.client() as client:
            _, session = await register_verify_login(client, tx_env)
            token = session["access_token"]

            salary = category_id(tx_env, "income", "Salary")
            groceries = category_id(tx_env, "expense", "Groceries")
            transport = category_id(tx_env, "expense", "Transport")

            for payload in (
                transaction_payload(
                    salary,
                    description="May salary",
                    transaction_type="income",
                    amount_kyat=100_000,
                    transaction_date="2026-05-01",
                ),
                transaction_payload(
                    groceries,
                    description="May groceries",
                    amount_kyat=20_000,
                    transaction_date="2026-05-15",
                ),
                transaction_payload(
                    transport,
                    description="June transport",
                    amount_kyat=5_000,
                    transaction_date="2026-06-01",
                ),
                transaction_payload(
                    groceries,
                    description="July groceries",
                    amount_kyat=30_000,
                    transaction_date="2026-07-31",
                ),
                transaction_payload(
                    salary,
                    description="August salary",
                    transaction_type="income",
                    amount_kyat=50_000,
                    transaction_date="2026-08-01",
                ),
                transaction_payload(
                    groceries,
                    description="Outside range",
                    amount_kyat=7_000,
                    transaction_date="2026-09-01",
                ),
            ):
                created = await create_transaction(client, token, payload)
                assert created.status_code == 201, created.text

            response = await fetch_report(
                client,
                token,
                "/api/v1/reports/cash-flow?from=2026-05-01&to=2026-08-31",
            )
            assert response.status_code == 200, response.text
            data = response.json()["data"]
            assert data["from"] == "2026-05-01"
            assert data["to"] == "2026-08-31"
            assert data["months"] == [
                {"month": "2026-05", "income_kyat": 100_000, "expense_kyat": 20_000},
                {"month": "2026-06", "income_kyat": 0, "expense_kyat": 5_000},
                {"month": "2026-07", "income_kyat": 0, "expense_kyat": 30_000},
                {"month": "2026-08", "income_kyat": 50_000, "expense_kyat": 0},
            ]

            single_day = await fetch_report(
                client,
                token,
                "/api/v1/reports/cash-flow?from=2026-05-15&to=2026-05-15",
            )
            assert single_day.status_code == 200
            assert single_day.json()["data"]["months"] == [
                {"month": "2026-05", "income_kyat": 0, "expense_kyat": 20_000},
            ]

            empty = await fetch_report(
                client,
                token,
                "/api/v1/reports/cash-flow?from=2030-01-01&to=2030-01-31",
            )
            assert empty.status_code == 200
            assert empty.json()["data"]["months"] == [
                {"month": "2030-01", "income_kyat": 0, "expense_kyat": 0},
            ]

            today = date.today()
            today_transaction = await create_transaction(
                client,
                token,
                transaction_payload(
                    salary,
                    description="Today income",
                    transaction_type="income",
                    amount_kyat=12_345,
                    transaction_date=today.isoformat(),
                ),
            )
            assert today_transaction.status_code == 201, today_transaction.text

            default_range = await fetch_report(
                client,
                token,
                "/api/v1/reports/cash-flow",
            )
            assert default_range.status_code == 200, default_range.text
            months = default_range.json()["data"]["months"]
            assert len(months) == 6
            assert months[-1]["month"] == today.strftime("%Y-%m")
            assert months[-1]["income_kyat"] == 12_345

            for invalid in (
                "?from=2026-05-02&to=2026-05-01",
                "?from=2026-05-01",
                "?to=2026-05-01",
                "?from=2026-02-30&to=2026-03-01",
            ):
                bad = await fetch_report(
                    client,
                    token,
                    f"/api/v1/reports/cash-flow{invalid}",
                )
                assert bad.status_code == 422, (invalid, bad.text)
                assert bad.json()["error"]["code"] == "invalid_date_range"

    asyncio.run(scenario())


def test_expenses_by_category_is_owner_scoped_and_expense_only(
    tx_env: TransactionEnv,
):
    async def scenario() -> None:
        async with tx_env.client() as client:
            _, first = await register_verify_login(client, tx_env)
            _, second = await register_verify_login(client, tx_env)
            first_token = first["access_token"]
            second_token = second["access_token"]

            salary = category_id(tx_env, "income", "Salary")
            groceries = category_id(tx_env, "expense", "Groceries")
            transport = category_id(tx_env, "expense", "Transport")

            for payload in (
                transaction_payload(
                    groceries,
                    description="First groceries one",
                    amount_kyat=20_000,
                    transaction_date="2026-05-15",
                ),
                transaction_payload(
                    groceries,
                    description="First groceries two",
                    amount_kyat=30_000,
                    transaction_date="2026-07-31",
                ),
                transaction_payload(
                    transport,
                    description="First transport",
                    amount_kyat=5_000,
                    transaction_date="2026-06-01",
                ),
                transaction_payload(
                    salary,
                    description="Ignored income",
                    transaction_type="income",
                    amount_kyat=999_000,
                    transaction_date="2026-06-15",
                ),
            ):
                created = await create_transaction(
                    client, first_token, payload
                )
                assert created.status_code == 201, created.text

            other = await create_transaction(
                client,
                second_token,
                transaction_payload(
                    groceries,
                    description="Second user groceries",
                    amount_kyat=777_000,
                    transaction_date="2026-06-15",
                ),
            )
            assert other.status_code == 201, other.text

            response = await fetch_report(
                client,
                first_token,
                (
                    "/api/v1/reports/expenses-by-category"
                    "?from=2026-05-01&to=2026-08-31"
                ),
            )
            assert response.status_code == 200, response.text
            data = response.json()["data"]
            assert data["from"] == "2026-05-01"
            assert data["to"] == "2026-08-31"
            assert data["categories"] == [
                {"category": "Groceries", "expense_kyat": 50_000},
                {"category": "Transport", "expense_kyat": 5_000},
            ]

            boundary = await fetch_report(
                client,
                first_token,
                (
                    "/api/v1/reports/expenses-by-category"
                    "?from=2026-07-31&to=2026-07-31"
                ),
            )
            assert boundary.status_code == 200
            assert boundary.json()["data"]["categories"] == [
                {"category": "Groceries", "expense_kyat": 30_000},
            ]

            empty = await fetch_report(
                client,
                first_token,
                (
                    "/api/v1/reports/expenses-by-category"
                    "?from=2030-01-01&to=2030-01-31"
                ),
            )
            assert empty.status_code == 200
            assert empty.json()["data"]["categories"] == []

            reversed_range = await fetch_report(
                client,
                first_token,
                (
                    "/api/v1/reports/expenses-by-category"
                    "?from=2026-08-31&to=2026-05-01"
                ),
            )
            assert reversed_range.status_code == 422
            assert reversed_range.json()["error"]["code"] == (
                "invalid_date_range"
            )

    asyncio.run(scenario())


def test_csv_export_is_owner_scoped_escaped_and_formula_safe(
    tx_env: TransactionEnv,
):
    async def scenario() -> None:
        async with tx_env.client() as client:
            _, first = await register_verify_login(client, tx_env)
            _, second = await register_verify_login(client, tx_env)
            first_token = first["access_token"]
            second_token = second["access_token"]

            groceries = category_id(tx_env, "expense", "Groceries")
            salary = category_id(tx_env, "income", "Salary")
            for payload in (
                transaction_payload(
                    groceries,
                    description='Coffee, "tea"',
                    amount_kyat=1_250,
                    transaction_date="2026-05-15",
                ),
                transaction_payload(
                    groceries,
                    description="=2+2",
                    amount_kyat=2_500,
                    transaction_date="2026-05-16",
                ),
                transaction_payload(
                    salary,
                    description="+SUM(A1:A2)",
                    transaction_type="income",
                    amount_kyat=10_000,
                    transaction_date="2026-05-17",
                ),
            ):
                created = await create_transaction(
                    client, first_token, payload
                )
                assert created.status_code == 201, created.text

            other = await create_transaction(
                client,
                second_token,
                transaction_payload(
                    groceries,
                    description="Other user secret record",
                    amount_kyat=888_888,
                    transaction_date="2026-05-18",
                ),
            )
            assert other.status_code == 201, other.text

            response = await client.get(
                "/api/v1/reports/export.csv",
                headers=headers(token=first_token),
            )
            assert response.status_code == 200, response.text
            assert response.headers["content-type"].startswith("text/csv")
            assert response.headers["content-disposition"] == (
                'attachment; filename="finpath-dashboard-report.csv"'
            )
            assert response.content.startswith(b"\xef\xbb\xbf")

            csv_text = response.content.decode("utf-8-sig")
            lines = csv_text.split("\r\n")
            assert lines[0] == (
                "Section,Record,Date or period,Category,Amount (MMK),"
                "Comparison (MMK),Status or notes"
            )
            assert (
                'Transaction,"Coffee, ""tea""",2026-05-15,Groceries,1250,,expense'
                in csv_text
            )
            assert "Transaction,'=2+2,2026-05-16,Groceries,2500,,expense" in csv_text
            assert (
                "Transaction,'+SUM(A1:A2),2026-05-17,Salary,10000,,income"
                in csv_text
            )
            assert "Other user secret record" not in csv_text
            assert "888888" not in csv_text

            # A user with no records still receives the header and summary
            # rows, and never receives another user's transaction rows.
            _, empty_user = await register_verify_login(client, tx_env)
            empty = await client.get(
                "/api/v1/reports/export.csv",
                headers=headers(token=empty_user["access_token"]),
            )
            assert empty.status_code == 200, empty.text
            empty_text = empty.content.decode("utf-8-sig")
            assert empty_text.startswith("Section,Record,")
            assert "\r\nTransaction," not in empty_text
            assert "Coffee" not in empty_text
            assert "Other user secret record" not in empty_text

    asyncio.run(scenario())
