"""PostgreSQL tests for the authenticated Financial Settings API."""

from __future__ import annotations

import asyncio
from datetime import date, timedelta
from typing import Any

from tests.tx_support import (
    TransactionEnv,
    category_id,
    create_transaction,
    headers,
    register_verify_login,
    transaction_payload,
)

SETTINGS_PATH = "/api/v1/users/me/financial-settings"
DASHBOARD_PATH = "/api/v1/dashboard/overview"

TODAY = date.today()
EFFECTIVE_DATE = TODAY - timedelta(days=10)
SECOND_EFFECTIVE_DATE = EFFECTIVE_DATE + timedelta(days=2)


async def put_settings(
    client,
    token: str,
    payload: dict[str, Any],
    *,
    if_match: str | None,
):
    return await client.put(
        SETTINGS_PATH,
        json=payload,
        headers=headers(token=token, if_match=if_match),
    )


async def get_dashboard(client, token: str):
    return await client.get(
        DASHBOARD_PATH,
        headers=headers(token=token),
    )


def test_financial_settings_requires_authentication(
    tx_env: TransactionEnv,
):
    async def scenario() -> None:
        async with tx_env.client() as client:
            get_response = await client.get(SETTINGS_PATH)
            assert get_response.status_code == 401
            assert get_response.json()["error"]["code"] == (
                "authentication_required"
            )

            put_response = await client.put(
                SETTINGS_PATH,
                json={
                    "starting_balance_kyat": 100_000,
                    "effective_date": EFFECTIVE_DATE.isoformat(),
                },
                headers={"If-Match": '"1"'},
            )
            assert put_response.status_code == 401
            assert put_response.json()["error"]["code"] == (
                "authentication_required"
            )

    asyncio.run(scenario())


def test_financial_settings_unconfigured_and_set(
    tx_env: TransactionEnv,
):
    async def scenario() -> None:
        async with tx_env.client() as client:
            _, session = await register_verify_login(client, tx_env)
            token = session["access_token"]
            user_id = session["user"]["id"]

            initial = await client.get(
                SETTINGS_PATH,
                headers=headers(token=token),
            )
            assert initial.status_code == 200, initial.text
            assert initial.headers["etag"] == '"1"'
            initial_data = initial.json()["data"]
            assert initial_data["status"] == "not_configured"
            assert initial_data["starting_balance_kyat"] is None
            assert initial_data["effective_date"] is None
            assert initial_data["amount_kyat"] is None
            assert initial_data["as_of_date"] == TODAY.isoformat()

            configured = await put_settings(
                client,
                token,
                {
                    "starting_balance_kyat": 100_000,
                    "effective_date": EFFECTIVE_DATE.isoformat(),
                },
                if_match='"1"',
            )
            assert configured.status_code == 200, configured.text
            assert configured.headers["etag"] == '"2"'
            configured_data = configured.json()["data"]
            assert configured_data["status"] == "configured"
            assert configured_data["starting_balance_kyat"] == 100_000
            assert configured_data["effective_date"] == (
                EFFECTIVE_DATE.isoformat()
            )
            assert configured_data["amount_kyat"] == 100_000
            assert configured_data["as_of_date"] == TODAY.isoformat()

            stored = tx_env.one(
                "SELECT starting_balance_kyat, effective_date, revision "
                "FROM user_financial_settings WHERE user_id = :user_id",
                {"user_id": user_id},
            )
            assert stored is not None
            assert stored["starting_balance_kyat"] == 100_000
            assert stored["effective_date"] == EFFECTIVE_DATE
            assert stored["revision"] == 2

            reread = await client.get(
                SETTINGS_PATH,
                headers=headers(token=token),
            )
            assert reread.status_code == 200, reread.text
            assert reread.headers["etag"] == '"2"'
            assert reread.json()["data"] == configured_data

    asyncio.run(scenario())


def test_financial_settings_update_negative_and_clear(
    tx_env: TransactionEnv,
):
    async def scenario() -> None:
        async with tx_env.client() as client:
            _, session = await register_verify_login(client, tx_env)
            token = session["access_token"]

            configured = await put_settings(
                client,
                token,
                {
                    "starting_balance_kyat": 100_000,
                    "effective_date": EFFECTIVE_DATE.isoformat(),
                },
                if_match='"1"',
            )
            assert configured.status_code == 200, configured.text
            assert configured.headers["etag"] == '"2"'

            updated = await put_settings(
                client,
                token,
                {
                    "starting_balance_kyat": -250_000,
                    "effective_date": SECOND_EFFECTIVE_DATE.isoformat(),
                },
                if_match='"2"',
            )
            assert updated.status_code == 200, updated.text
            assert updated.headers["etag"] == '"3"'
            updated_data = updated.json()["data"]
            assert updated_data["status"] == "configured"
            assert updated_data["starting_balance_kyat"] == -250_000
            assert updated_data["effective_date"] == (
                SECOND_EFFECTIVE_DATE.isoformat()
            )
            assert updated_data["amount_kyat"] == -250_000

            cleared = await put_settings(
                client,
                token,
                {
                    "starting_balance_kyat": None,
                    "effective_date": None,
                },
                if_match='"3"',
            )
            assert cleared.status_code == 200, cleared.text
            assert cleared.headers["etag"] == '"4"'
            cleared_data = cleared.json()["data"]
            assert cleared_data["status"] == "not_configured"
            assert cleared_data["starting_balance_kyat"] is None
            assert cleared_data["effective_date"] is None
            assert cleared_data["amount_kyat"] is None

            reread = await client.get(
                SETTINGS_PATH,
                headers=headers(token=token),
            )
            assert reread.status_code == 200, reread.text
            assert reread.headers["etag"] == '"4"'
            assert reread.json()["data"] == cleared_data

            dashboard = await get_dashboard(client, token)
            assert dashboard.status_code == 200, dashboard.text
            assert dashboard.json()["data"]["account_balance"]["status"] == (
                "not_configured"
            )

    asyncio.run(scenario())


def test_financial_settings_accepts_weak_etag_from_edge(
    tx_env: TransactionEnv,
):
    """The Pages edge weakens strong ETags to W/"N" in transit.

    The backend still emits the canonical strong tag, but must accept the weak
    form because the revision carried by W/"N" is N.
    """

    async def scenario() -> None:
        async with tx_env.client() as client:
            _, session = await register_verify_login(client, tx_env)
            token = session["access_token"]
            user_id = session["user"]["id"]

            initial = await client.get(
                SETTINGS_PATH,
                headers=headers(token=token),
            )
            assert initial.status_code == 200, initial.text
            # GET keeps the canonical strong form.
            assert initial.headers["etag"] == '"1"'
            assert initial.json()["data"]["status"] == "not_configured"

            configured = await put_settings(
                client,
                token,
                {
                    "starting_balance_kyat": 800_000,
                    "effective_date": EFFECTIVE_DATE.isoformat(),
                },
                if_match='W/"1"',
            )
            assert configured.status_code == 200, configured.text
            assert configured.headers["etag"] == '"2"'
            configured_data = configured.json()["data"]
            assert configured_data["status"] == "configured"
            assert configured_data["starting_balance_kyat"] == 800_000
            assert configured_data["effective_date"] == (
                EFFECTIVE_DATE.isoformat()
            )

            stored = tx_env.one(
                "SELECT starting_balance_kyat, effective_date, revision "
                "FROM user_financial_settings WHERE user_id = :user_id",
                {"user_id": user_id},
            )
            assert stored is not None
            assert stored["starting_balance_kyat"] == 800_000
            assert stored["effective_date"] == EFFECTIVE_DATE
            assert stored["revision"] == 2

            reread = await client.get(
                SETTINGS_PATH,
                headers=headers(token=token),
            )
            assert reread.status_code == 200, reread.text
            assert reread.headers["etag"] == '"2"'
            assert reread.json()["data"] == configured_data

            updated = await put_settings(
                client,
                token,
                {
                    "starting_balance_kyat": -250_000,
                    "effective_date": SECOND_EFFECTIVE_DATE.isoformat(),
                },
                if_match='W/"2"',
            )
            assert updated.status_code == 200, updated.text
            assert updated.headers["etag"] == '"3"'
            assert updated.json()["data"]["starting_balance_kyat"] == -250_000

            cleared = await put_settings(
                client,
                token,
                {
                    "starting_balance_kyat": None,
                    "effective_date": None,
                },
                if_match='W/"3"',
            )
            assert cleared.status_code == 200, cleared.text
            assert cleared.headers["etag"] == '"4"'
            assert cleared.json()["data"]["status"] == "not_configured"

    asyncio.run(scenario())


def test_financial_settings_weak_etag_still_rejects_stale_revision(
    tx_env: TransactionEnv,
):
    """Accepting W/"N" must not weaken optimistic concurrency."""

    async def scenario() -> None:
        async with tx_env.client() as client:
            _, session = await register_verify_login(client, tx_env)
            token = session["access_token"]

            # Two sessions observe revision 1.
            first_load = await client.get(
                SETTINGS_PATH,
                headers=headers(token=token),
            )
            second_load = await client.get(
                SETTINGS_PATH,
                headers=headers(token=token),
            )
            assert first_load.headers["etag"] == '"1"'
            assert second_load.headers["etag"] == '"1"'

            first_update = await put_settings(
                client,
                token,
                {
                    "starting_balance_kyat": 100_000,
                    "effective_date": EFFECTIVE_DATE.isoformat(),
                },
                if_match='W/"1"',
            )
            assert first_update.status_code == 200, first_update.text
            assert first_update.headers["etag"] == '"2"'

            stale = await put_settings(
                client,
                token,
                {
                    "starting_balance_kyat": 200_000,
                    "effective_date": EFFECTIVE_DATE.isoformat(),
                },
                if_match='W/"1"',
            )
            assert stale.status_code == 412, stale.text
            assert stale.json()["error"]["code"] == "stale_revision"

            resynced = await client.get(
                SETTINGS_PATH,
                headers=headers(token=token),
            )
            assert resynced.status_code == 200, resynced.text
            assert resynced.headers["etag"] == '"2"'
            assert resynced.json()["data"]["starting_balance_kyat"] == 100_000

            retried = await put_settings(
                client,
                token,
                {
                    "starting_balance_kyat": 200_000,
                    "effective_date": EFFECTIVE_DATE.isoformat(),
                },
                if_match='W/"2"',
            )
            assert retried.status_code == 200, retried.text
            assert retried.headers["etag"] == '"3"'
            assert retried.json()["data"]["starting_balance_kyat"] == 200_000

            for malformed in (
                'W/"not-a-revision"',
                'W/"3", "4"',
                "W/3",
                'w/"3"',
                "*",
                'W/"',
            ):
                rejected = await put_settings(
                    client,
                    token,
                    {
                        "starting_balance_kyat": 300_000,
                        "effective_date": EFFECTIVE_DATE.isoformat(),
                    },
                    if_match=malformed,
                )
                assert rejected.status_code == 412, (malformed, rejected.text)
                assert rejected.json()["error"]["code"] == "stale_revision"

            missing_precondition = await put_settings(
                client,
                token,
                {
                    "starting_balance_kyat": 300_000,
                    "effective_date": EFFECTIVE_DATE.isoformat(),
                },
                if_match=None,
            )
            assert missing_precondition.status_code == 428
            assert missing_precondition.json()["error"]["code"] == (
                "precondition_required"
            )

            unchanged = await client.get(
                SETTINGS_PATH,
                headers=headers(token=token),
            )
            assert unchanged.headers["etag"] == '"3"'
            assert unchanged.json()["data"]["starting_balance_kyat"] == 200_000

    asyncio.run(scenario())


def test_financial_settings_validation(
    tx_env: TransactionEnv,
):
    async def scenario() -> None:
        async with tx_env.client() as client:
            _, session = await register_verify_login(client, tx_env)
            token = session["access_token"]

            invalid_monetary_values = (
                1.5,
                "100",
                True,
                9_007_199_254_740_992,
                -9_007_199_254_740_992,
            )
            for value in invalid_monetary_values:
                response = await put_settings(
                    client,
                    token,
                    {
                        "starting_balance_kyat": value,
                        "effective_date": EFFECTIVE_DATE.isoformat(),
                    },
                    if_match='"1"',
                )
                assert response.status_code == 422, (value, response.text)
                assert response.json()["error"]["code"] in {
                    "validation_error",
                    "amount_not_safe_integer",
                }

            invalid_dates = (
                "2026-02-30",
                "2026-11-03T00:00:00",
                "11/03/2026",
                123,
            )
            for value in invalid_dates:
                response = await put_settings(
                    client,
                    token,
                    {
                        "starting_balance_kyat": 100_000,
                        "effective_date": value,
                    },
                    if_match='"1"',
                )
                assert response.status_code == 422, (value, response.text)
                assert response.json()["error"]["code"] == "validation_error"

            for payload in (
                {
                    "starting_balance_kyat": 100_000,
                    "effective_date": None,
                },
                {
                    "starting_balance_kyat": None,
                    "effective_date": EFFECTIVE_DATE.isoformat(),
                },
                {"starting_balance_kyat": 100_000},
                {"effective_date": EFFECTIVE_DATE.isoformat()},
                {
                    "starting_balance_kyat": 100_000,
                    "effective_date": EFFECTIVE_DATE.isoformat(),
                    "extra": "not allowed",
                },
            ):
                response = await put_settings(
                    client,
                    token,
                    payload,
                    if_match='"1"',
                )
                assert response.status_code == 422, (payload, response.text)
                assert response.json()["error"]["code"] == "validation_error"

            missing_precondition = await put_settings(
                client,
                token,
                {
                    "starting_balance_kyat": 100_000,
                    "effective_date": EFFECTIVE_DATE.isoformat(),
                },
                if_match=None,
            )
            assert missing_precondition.status_code == 428
            assert missing_precondition.json()["error"]["code"] == (
                "precondition_required"
            )

            for if_match in ('"not-a-revision"', '"99"'):
                response = await put_settings(
                    client,
                    token,
                    {
                        "starting_balance_kyat": 100_000,
                        "effective_date": EFFECTIVE_DATE.isoformat(),
                    },
                    if_match=if_match,
                )
                assert response.status_code == 412, (if_match, response.text)
                assert response.json()["error"]["code"] == "stale_revision"

            unchanged = await client.get(
                SETTINGS_PATH,
                headers=headers(token=token),
            )
            assert unchanged.status_code == 200, unchanged.text
            assert unchanged.headers["etag"] == '"1"'
            assert unchanged.json()["data"]["status"] == "not_configured"

    asyncio.run(scenario())


def test_financial_settings_ownership_isolation(
    tx_env: TransactionEnv,
):
    async def scenario() -> None:
        async with tx_env.client() as client:
            _, first = await register_verify_login(client, tx_env)
            _, second = await register_verify_login(client, tx_env)
            first_token = first["access_token"]
            second_token = second["access_token"]
            first_user_id = first["user"]["id"]
            second_user_id = second["user"]["id"]

            first_set = await put_settings(
                client,
                first_token,
                {
                    "starting_balance_kyat": 111_000,
                    "effective_date": EFFECTIVE_DATE.isoformat(),
                },
                if_match='"1"',
            )
            assert first_set.status_code == 200, first_set.text
            first_updated = await put_settings(
                client,
                first_token,
                {
                    "starting_balance_kyat": 112_000,
                    "effective_date": EFFECTIVE_DATE.isoformat(),
                },
                if_match='"2"',
            )
            assert first_updated.status_code == 200, first_updated.text
            assert first_updated.headers["etag"] == '"3"'

            second_initial = await client.get(
                SETTINGS_PATH,
                headers=headers(token=second_token),
            )
            assert second_initial.status_code == 200, second_initial.text
            assert second_initial.headers["etag"] == '"1"'
            assert second_initial.json()["data"]["status"] == (
                "not_configured"
            )

            second_set = await put_settings(
                client,
                second_token,
                {
                    "starting_balance_kyat": 222_000,
                    "effective_date": SECOND_EFFECTIVE_DATE.isoformat(),
                },
                if_match='"1"',
            )
            assert second_set.status_code == 200, second_set.text
            assert second_set.headers["etag"] == '"2"'

            # A stale ETag from another user must not modify this user's row.
            cross_user = await put_settings(
                client,
                second_token,
                {
                    "starting_balance_kyat": 999_000,
                    "effective_date": EFFECTIVE_DATE.isoformat(),
                },
                if_match='"3"',
            )
            assert cross_user.status_code == 412
            assert cross_user.json()["error"]["code"] == "stale_revision"

            first_reread = await client.get(
                SETTINGS_PATH,
                headers=headers(token=first_token),
            )
            assert first_reread.status_code == 200, first_reread.text
            assert first_reread.headers["etag"] == '"3"'
            assert first_reread.json()["data"]["starting_balance_kyat"] == (
                112_000
            )

            second_reread = await client.get(
                SETTINGS_PATH,
                headers=headers(token=second_token),
            )
            assert second_reread.status_code == 200, second_reread.text
            assert second_reread.headers["etag"] == '"2"'
            assert second_reread.json()["data"]["starting_balance_kyat"] == (
                222_000
            )

            first_row = tx_env.one(
                "SELECT starting_balance_kyat FROM user_financial_settings "
                "WHERE user_id = :user_id",
                {"user_id": first_user_id},
            )
            second_row = tx_env.one(
                "SELECT starting_balance_kyat FROM user_financial_settings "
                "WHERE user_id = :user_id",
                {"user_id": second_user_id},
            )
            assert first_row is not None
            assert second_row is not None
            assert first_row["starting_balance_kyat"] == 112_000
            assert second_row["starting_balance_kyat"] == 222_000

    asyncio.run(scenario())


def test_dashboard_uses_saved_financial_settings(
    tx_env: TransactionEnv,
):
    salary = category_id(tx_env, "income", "Salary")
    groceries = category_id(tx_env, "expense", "Groceries")

    async def scenario() -> None:
        async with tx_env.client() as client:
            _, session = await register_verify_login(client, tx_env)
            token = session["access_token"]

            for payload in (
                transaction_payload(
                    salary,
                    description="Before effective date",
                    transaction_type="income",
                    amount_kyat=999_999,
                    transaction_date=(
                        EFFECTIVE_DATE - timedelta(days=1)
                    ).isoformat(),
                ),
                transaction_payload(
                    salary,
                    description="On effective date",
                    transaction_type="income",
                    amount_kyat=888_888,
                    transaction_date=EFFECTIVE_DATE.isoformat(),
                ),
                transaction_payload(
                    salary,
                    description="After effective date",
                    transaction_type="income",
                    amount_kyat=50_000,
                    transaction_date=(
                        EFFECTIVE_DATE + timedelta(days=1)
                    ).isoformat(),
                ),
                transaction_payload(
                    groceries,
                    description="Later expense",
                    amount_kyat=20_000,
                    transaction_date=(
                        EFFECTIVE_DATE + timedelta(days=3)
                    ).isoformat(),
                ),
            ):
                created = await create_transaction(
                    client, token, payload
                )
                assert created.status_code == 201, created.text

            before = await get_dashboard(client, token)
            assert before.status_code == 200, before.text
            assert before.json()["data"]["account_balance"]["status"] == (
                "not_configured"
            )

            configured = await put_settings(
                client,
                token,
                {
                    "starting_balance_kyat": 100_000,
                    "effective_date": EFFECTIVE_DATE.isoformat(),
                },
                if_match='"1"',
            )
            assert configured.status_code == 200, configured.text
            assert configured.headers["etag"] == '"2"'
            assert configured.json()["data"]["amount_kyat"] == 130_000

            first_dashboard = await get_dashboard(client, token)
            assert first_dashboard.status_code == 200, first_dashboard.text
            first_balance = first_dashboard.json()["data"]["account_balance"]
            assert first_balance["status"] == "configured"
            assert first_balance["starting_balance_kyat"] == 100_000
            assert first_balance["effective_date"] == (
                EFFECTIVE_DATE.isoformat()
            )
            assert first_balance["amount_kyat"] == 130_000

            updated = await put_settings(
                client,
                token,
                {
                    "starting_balance_kyat": -250_000,
                    "effective_date": SECOND_EFFECTIVE_DATE.isoformat(),
                },
                if_match='"2"',
            )
            assert updated.status_code == 200, updated.text
            assert updated.headers["etag"] == '"3"'
            assert updated.json()["data"]["amount_kyat"] == -270_000

            second_dashboard = await get_dashboard(client, token)
            assert second_dashboard.status_code == 200, second_dashboard.text
            second_balance = second_dashboard.json()["data"]["account_balance"]
            assert second_balance["status"] == "configured"
            assert second_balance["starting_balance_kyat"] == -250_000
            assert second_balance["effective_date"] == (
                SECOND_EFFECTIVE_DATE.isoformat()
            )
            assert second_balance["amount_kyat"] == -270_000

            cleared = await put_settings(
                client,
                token,
                {
                    "starting_balance_kyat": None,
                    "effective_date": None,
                },
                if_match='"3"',
            )
            assert cleared.status_code == 200, cleared.text
            assert cleared.headers["etag"] == '"4"'

            cleared_dashboard = await get_dashboard(client, token)
            assert cleared_dashboard.status_code == 200, (
                cleared_dashboard.text
            )
            cleared_balance = cleared_dashboard.json()["data"][
                "account_balance"
            ]
            assert cleared_balance["status"] == "not_configured"
            assert cleared_balance["amount_kyat"] is None

    asyncio.run(scenario())
