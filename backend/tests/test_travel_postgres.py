"""PostgreSQL tests for trips, planned expenses, spending, and imports."""

from __future__ import annotations

import asyncio
from typing import Any

import httpx

from tests.tx_support import (
    TransactionEnv,
    category_id,
    create_transaction,
    headers,
    login,
    new_key,
    register_verify_login,
    set_starting_balance,
    transaction_payload,
)


def trip_payload(
    *,
    name: str = "Lisbon getaway",
    destination: str = "Lisbon, Portugal",
    start_date: str = "2026-11-14",
    end_date: str = "2026-11-19",
) -> dict[str, Any]:
    return {
        "name": name,
        "destination": destination,
        "start_date": start_date,
        "end_date": end_date,
    }


def budget_payload(
    *,
    category: str = "Flights",
    budget_kyat: int = 1_800_000,
) -> dict[str, Any]:
    return {"category": category, "budget_kyat": budget_kyat}


def spending_payload(
    *,
    description: str = "Flight deposit",
    category: str = "Flights",
    amount_kyat: int = 450_000,
    spending_date: str = "2026-10-20",
    category_budget_id: str | None = None,
) -> dict[str, Any]:
    return {
        "description": description,
        "category": category,
        "amount_kyat": amount_kyat,
        "spending_date": spending_date,
        "category_budget_id": category_budget_id,
    }


async def create_trip(
    client: httpx.AsyncClient,
    token: str,
    payload: dict[str, Any],
    *,
    key: str | None = None,
) -> httpx.Response:
    return await client.post(
        "/api/v1/trips",
        json=payload,
        headers=headers(token=token, idem=key or new_key("trip")),
    )


async def create_budget(
    client: httpx.AsyncClient,
    token: str,
    trip_id: str,
    payload: dict[str, Any],
    *,
    key: str | None = None,
) -> httpx.Response:
    return await client.post(
        f"/api/v1/trips/{trip_id}/budgets",
        json=payload,
        headers=headers(token=token, idem=key or new_key("budget")),
    )


async def create_spending(
    client: httpx.AsyncClient,
    token: str,
    trip_id: str,
    payload: dict[str, Any],
    *,
    key: str | None = None,
) -> httpx.Response:
    return await client.post(
        f"/api/v1/trips/{trip_id}/spending",
        json=payload,
        headers=headers(token=token, idem=key or new_key("spend")),
    )


async def read_trip(
    client: httpx.AsyncClient,
    token: str,
    trip_id: str,
) -> httpx.Response:
    return await client.get(
        f"/api/v1/trips/{trip_id}",
        headers=headers(token=token),
    )


async def read_budget(
    client: httpx.AsyncClient,
    token: str,
    trip_id: str,
    budget_id: str,
) -> httpx.Response:
    return await client.get(
        f"/api/v1/trips/{trip_id}/budgets/{budget_id}",
        headers=headers(token=token),
    )


async def read_spending(
    client: httpx.AsyncClient,
    token: str,
    trip_id: str,
    spending_id: str,
) -> httpx.Response:
    return await client.get(
        f"/api/v1/trips/{trip_id}/spending/{spending_id}",
        headers=headers(token=token),
    )


def _count(env: TransactionEnv, table: str, user_id: str) -> int:
    row = env.one(
        f"SELECT count(*) AS total FROM {table} WHERE user_id = :user_id",
        {"user_id": user_id},
    )
    assert row is not None
    return int(row["total"])


def test_trip_create_list_detail_and_ownership(tx_env: TransactionEnv):
    async def scenario() -> None:
        async with tx_env.client() as client:
            _, first = await register_verify_login(client, tx_env)
            _, second = await register_verify_login(client, tx_env)
            first_token = first["access_token"]
            second_token = second["access_token"]
            first_user_id = first["user"]["id"]

            created = await create_trip(
                client,
                first_token,
                trip_payload(name="  Lisbon getaway  ", destination="  Lisbon  "),
            )
            assert created.status_code == 201, created.text
            trip = created.json()["data"]
            assert trip["name"] == "Lisbon getaway"
            assert trip["destination"] == "Lisbon"
            assert trip["start_date"] == "2026-11-14"
            assert trip["end_date"] == "2026-11-19"
            assert trip["status"] == "active"
            assert trip["budget_kyat"] == 0
            assert trip["spending_kyat"] == 0
            assert "user_id" not in trip
            assert created.headers["etag"] == '"1"'
            trip_id = trip["id"]

            row = tx_env.one(
                "SELECT user_id FROM trips WHERE id = :id",
                {"id": trip_id},
            )
            assert row is not None
            assert str(row["user_id"]) == first_user_id

            listing = await client.get(
                "/api/v1/trips",
                headers=headers(token=first_token),
            )
            assert listing.status_code == 200
            assert [item["id"] for item in listing.json()["data"]] == [trip_id]

            detail = await read_trip(client, first_token, trip_id)
            assert detail.status_code == 200
            assert detail.headers["etag"] == '"1"'

            other_listing = await client.get(
                "/api/v1/trips",
                headers=headers(token=second_token),
            )
            assert other_listing.status_code == 200
            assert other_listing.json()["data"] == []

            hidden = await read_trip(client, second_token, trip_id)
            assert hidden.status_code == 404
            assert hidden.json()["error"]["code"] == "not_found"

            updated = await client.patch(
                f"/api/v1/trips/{trip_id}",
                json={
                    "name": "Lisbon and Sintra",
                    "end_date": "2026-11-21",
                },
                headers=headers(token=first_token, if_match='"1"'),
            )
            assert updated.status_code == 200, updated.text
            assert updated.json()["data"]["name"] == "Lisbon and Sintra"
            assert updated.json()["data"]["end_date"] == "2026-11-21"
            assert updated.headers["etag"] == '"2"'

            missing_precondition = await client.patch(
                f"/api/v1/trips/{trip_id}",
                json={"name": "No If-Match"},
                headers=headers(token=first_token),
            )
            assert missing_precondition.status_code == 428

            stale = await client.patch(
                f"/api/v1/trips/{trip_id}",
                json={"name": "Stale write"},
                headers=headers(token=first_token, if_match='"1"'),
            )
            assert stale.status_code == 412

    asyncio.run(scenario())


def test_budgets_and_spending_totals_and_over_budget(tx_env: TransactionEnv):
    async def scenario() -> None:
        async with tx_env.client() as client:
            _, session = await register_verify_login(client, tx_env)
            token = session["access_token"]

            created = await create_trip(client, token, trip_payload())
            trip_id = created.json()["data"]["id"]

            flights = await create_budget(
                client,
                token,
                trip_id,
                budget_payload(category="Flights", budget_kyat=1_800_000),
            )
            food = await create_budget(
                client,
                token,
                trip_id,
                budget_payload(category="Food", budget_kyat=600_000),
            )
            assert flights.status_code == 201, flights.text
            assert food.status_code == 201, food.text
            flights_id = flights.json()["data"]["id"]

            first_spend = await create_spending(
                client,
                token,
                trip_id,
                spending_payload(
                    description="Airfare",
                    category="Flights",
                    amount_kyat=1_000_000,
                    category_budget_id=flights_id,
                ),
            )
            second_spend = await create_spending(
                client,
                token,
                trip_id,
                spending_payload(
                    description="Seat upgrade",
                    category="Flights",
                    amount_kyat=900_000,
                    category_budget_id=flights_id,
                ),
            )
            unbudgeted = await create_spending(
                client,
                token,
                trip_id,
                spending_payload(
                    description="Snacks",
                    category="Food",
                    amount_kyat=25_000,
                    category_budget_id=None,
                ),
            )
            assert first_spend.status_code == 201, first_spend.text
            assert second_spend.status_code == 201, second_spend.text
            assert unbudgeted.status_code == 201, unbudgeted.text

            detail = await read_trip(client, token, trip_id)
            assert detail.status_code == 200
            assert detail.json()["data"]["budget_kyat"] == 2_400_000
            assert detail.json()["data"]["spending_kyat"] == 1_925_000

            budgets = await client.get(
                f"/api/v1/trips/{trip_id}/budgets",
                headers=headers(token=token),
            )
            assert budgets.status_code == 200
            assert [item["category"] for item in budgets.json()["data"]] == [
                "Flights",
                "Food",
            ]

            spending = await client.get(
                f"/api/v1/trips/{trip_id}/spending",
                headers=headers(token=token),
            )
            assert spending.status_code == 200
            assert len(spending.json()["data"]) == 3
            assert {
                item["category_budget_id"] for item in spending.json()["data"]
            } == {flights_id, None}

            # Renaming the planned expense also updates the attached spending
            # category snapshot, while unbudgeted spending is untouched.
            renamed = await client.patch(
                f"/api/v1/trips/{trip_id}/budgets/{flights_id}",
                json={"category": "Air travel", "budget_kyat": 2_000_000},
                headers=headers(token=token, if_match='"1"'),
            )
            assert renamed.status_code == 200, renamed.text
            assert renamed.json()["data"]["category"] == "Air travel"
            assert renamed.headers["etag"] == '"2"'

            spending_after = await client.get(
                f"/api/v1/trips/{trip_id}/spending",
                headers=headers(token=token),
            )
            categories = {
                item["description"]: item["category"]
                for item in spending_after.json()["data"]
            }
            assert categories["Airfare"] == "Air travel"
            assert categories["Snacks"] == "Food"

            detail_after = await read_trip(client, token, trip_id)
            assert detail_after.json()["data"]["budget_kyat"] == 2_600_000
            assert detail_after.json()["data"]["spending_kyat"] == 1_925_000

            # Spending may exceed the planned amount; the API reports it rather
            # than blocking the entry.
            over_budget = await create_spending(
                client,
                token,
                trip_id,
                spending_payload(
                    description="Extra legroom",
                    category="Flights",
                    amount_kyat=500_000,
                    category_budget_id=flights_id,
                ),
            )
            assert over_budget.status_code == 201, over_budget.text
            assert (
                await read_trip(client, token, trip_id)
            ).json()["data"]["spending_kyat"] == 2_425_000

    asyncio.run(scenario())


def test_delete_budget_detaches_spending_and_preserves_category(
    tx_env: TransactionEnv,
):
    async def scenario() -> None:
        async with tx_env.client() as client:
            _, session = await register_verify_login(client, tx_env)
            token = session["access_token"]
            created = await create_trip(client, token, trip_payload())
            trip_id = created.json()["data"]["id"]
            budget = await create_budget(client, token, trip_id, budget_payload())
            budget_id = budget.json()["data"]["id"]
            spending = await create_spending(
                client,
                token,
                trip_id,
                spending_payload(category_budget_id=budget_id),
            )
            spending_id = spending.json()["data"]["id"]

            deleted = await client.request(
                "DELETE",
                f"/api/v1/trips/{trip_id}/budgets/{budget_id}",
                headers=headers(
                    token=token,
                    idem=new_key("delete-budget"),
                    if_match='"1"',
                ),
            )
            assert deleted.status_code == 204, deleted.text

            detached = await read_spending(client, token, trip_id, spending_id)
            assert detached.status_code == 200
            assert detached.json()["data"]["category_budget_id"] is None
            assert detached.json()["data"]["category"] == "Flights"

            budgets = await client.get(
                f"/api/v1/trips/{trip_id}/budgets",
                headers=headers(token=token),
            )
            assert budgets.json()["data"] == []

            detail = await read_trip(client, token, trip_id)
            assert detail.json()["data"]["budget_kyat"] == 0
            assert detail.json()["data"]["spending_kyat"] == 450_000

    asyncio.run(scenario())


def test_invalid_amounts_dates_and_budget_references(tx_env: TransactionEnv):
    async def scenario() -> None:
        async with tx_env.client() as client:
            _, session = await register_verify_login(client, tx_env)
            token = session["access_token"]

            reversed_dates = await create_trip(
                client,
                token,
                trip_payload(start_date="2026-11-19", end_date="2026-11-14"),
            )
            assert reversed_dates.status_code == 422

            bad_date = await create_trip(
                client,
                token,
                trip_payload(start_date="2026-02-30", end_date="2026-03-02"),
            )
            assert bad_date.status_code == 422

            created = await create_trip(client, token, trip_payload())
            trip_id = created.json()["data"]["id"]

            negative_budget = await create_budget(
                client,
                token,
                trip_id,
                {"category": "Venue", "budget_kyat": -1},
            )
            assert negative_budget.status_code == 422
            decimal_budget = await create_budget(
                client,
                token,
                trip_id,
                {"category": "Venue", "budget_kyat": 100.5},
            )
            assert decimal_budget.status_code == 422
            unsafe_budget = await create_budget(
                client,
                token,
                trip_id,
                {"category": "Venue", "budget_kyat": 9_007_199_254_740_992},
            )
            assert unsafe_budget.status_code == 422

            zero_spending = await create_spending(
                client,
                token,
                trip_id,
                spending_payload(amount_kyat=0),
            )
            assert zero_spending.status_code == 422
            decimal_spending = await create_spending(
                client,
                token,
                trip_id,
                spending_payload(amount_kyat=250.5),
            )
            assert decimal_spending.status_code == 422
            over_limit = await create_spending(
                client,
                token,
                trip_id,
                spending_payload(amount_kyat=1_000_000_001),
            )
            assert over_limit.status_code == 422
            assert over_limit.json()["error"]["code"] == "amount_limit_exceeded"

            foreign_budget = await create_spending(
                client,
                token,
                trip_id,
                spending_payload(
                    category_budget_id="00000000-0000-0000-0000-000000000001"
                ),
            )
            assert foreign_budget.status_code == 422

            duplicate_budget = await create_budget(
                client, token, trip_id, budget_payload(category="Food")
            )
            assert duplicate_budget.status_code == 201
            duplicate_again = await create_budget(
                client, token, trip_id, budget_payload(category="  food  ")
            )
            assert duplicate_again.status_code == 409
            assert (
                duplicate_again.json()["error"]["code"]
                == "budget_category_exists"
            )

            assert _count(tx_env, "trip_spending_entries", session["user"]["id"]) == 0
            assert _count(tx_env, "trip_category_budgets", session["user"]["id"]) == 1

    asyncio.run(scenario())


def test_import_creates_records_and_skips_reimport_duplicates(
    tx_env: TransactionEnv,
):
    import_payload = {
        "trips": [
            {
                "name": "Kyoto spring trip",
                "destination": "Kyoto, Japan",
                "start_date": "2027-03-08",
                "end_date": "2027-03-16",
                "budgets": [
                    {"category": "Flights", "budget_kyat": 3_200_000},
                    {"category": "Lodging", "budget_kyat": 4_000_000},
                ],
                "spending": [
                    {
                        "description": "Flight deposit",
                        "category": "Flights",
                        "amount_kyat": 840_000,
                        "spending_date": "2026-10-21",
                        "budget_category": "Flights",
                    },
                    {
                        "description": "Travel insurance",
                        "category": "Other",
                        "amount_kyat": 50_000,
                        "spending_date": "2026-10-22",
                    },
                ],
            }
        ]
    }

    async def scenario() -> None:
        async with tx_env.client() as client:
            _, session = await register_verify_login(client, tx_env)
            token = session["access_token"]
            user_id = session["user"]["id"]

            first = await client.post(
                "/api/v1/trips/import",
                json=import_payload,
                headers=headers(token=token, idem=new_key("import")),
            )
            assert first.status_code == 200, first.text
            assert first.json()["data"] == {
                "trips_created": 1,
                "trips_skipped_duplicates": 0,
                "budgets_created": 2,
                "budgets_skipped_duplicates": 0,
                "spending_created": 2,
                "spending_skipped_duplicates": 0,
            }

            trips = await client.get("/api/v1/trips", headers=headers(token=token))
            trip = trips.json()["data"][0]
            assert trip["budget_kyat"] == 7_200_000
            assert trip["spending_kyat"] == 890_000

            second = await client.post(
                "/api/v1/trips/import",
                json=import_payload,
                headers=headers(token=token, idem=new_key("import")),
            )
            assert second.status_code == 200, second.text
            assert second.json()["data"] == {
                "trips_created": 0,
                "trips_skipped_duplicates": 1,
                "budgets_created": 0,
                "budgets_skipped_duplicates": 2,
                "spending_created": 0,
                "spending_skipped_duplicates": 2,
            }
            assert _count(tx_env, "trips", user_id) == 1
            assert _count(tx_env, "trip_category_budgets", user_id) == 2
            assert _count(tx_env, "trip_spending_entries", user_id) == 2

            trip_id = trip["id"]
            spending = await client.get(
                f"/api/v1/trips/{trip_id}/spending",
                headers=headers(token=token),
            )
            linked = [
                item
                for item in spending.json()["data"]
                if item["description"] == "Flight deposit"
            ]
            assert len(linked) == 1
            assert linked[0]["category_budget_id"] is not None

    asyncio.run(scenario())


def test_invalid_import_is_atomic_and_reports_field(tx_env: TransactionEnv):
    invalid_payload = {
        "trips": [
            {
                "name": "Invalid import",
                "destination": "Nowhere",
                "start_date": "2027-01-01",
                "end_date": "2027-01-03",
                "budgets": [{"category": "Flights", "budget_kyat": 100_000}],
                "spending": [
                    {
                        "description": "Valid entry",
                        "category": "Flights",
                        "amount_kyat": 50_000,
                        "spending_date": "2026-12-01",
                    },
                    {
                        "description": "Too large",
                        "category": "Flights",
                        "amount_kyat": 1_000_000_001,
                        "spending_date": "2026-12-02",
                    },
                ],
            }
        ]
    }

    async def scenario() -> None:
        async with tx_env.client() as client:
            _, session = await register_verify_login(client, tx_env)
            token = session["access_token"]
            user_id = session["user"]["id"]

            response = await client.post(
                "/api/v1/trips/import",
                json=invalid_payload,
                headers=headers(token=token, idem=new_key("import")),
            )
            assert response.status_code == 422, response.text
            assert response.json()["error"]["code"] == "amount_limit_exceeded"
            field_errors = response.json()["error"]["field_errors"]
            assert field_errors[0]["field"] == "trips[0].spending[1].amount_kyat"

            # Nothing from the invalid file was silently saved.
            assert _count(tx_env, "trips", user_id) == 0
            assert _count(tx_env, "trip_category_budgets", user_id) == 0
            assert _count(tx_env, "trip_spending_entries", user_id) == 0

            malformed = {
                "trips": [
                    {
                        "name": "Bad date",
                        "destination": "Nowhere",
                        "start_date": "2027-02-30",
                        "end_date": "2027-03-01",
                    }
                ]
            }
            bad_response = await client.post(
                "/api/v1/trips/import",
                json=malformed,
                headers=headers(token=token, idem=new_key("import")),
            )
            assert bad_response.status_code == 422
            assert _count(tx_env, "trips", user_id) == 0

            unknown_budget = {
                "trips": [
                    {
                        "name": "Unknown budget",
                        "destination": "Nowhere",
                        "start_date": "2027-01-01",
                        "end_date": "2027-01-03",
                        "spending": [
                            {
                                "description": "Orphan",
                                "category": "Flights",
                                "amount_kyat": 1_000,
                                "spending_date": "2026-12-01",
                                "budget_category": "Flights",
                            }
                        ],
                    }
                ]
            }
            orphan_response = await client.post(
                "/api/v1/trips/import",
                json=unknown_budget,
                headers=headers(token=token, idem=new_key("import")),
            )
            assert orphan_response.status_code == 422
            assert _count(tx_env, "trips", user_id) == 0

    asyncio.run(scenario())


def test_trip_delete_requires_confirmation_and_cancels(
    tx_env: TransactionEnv,
):
    async def scenario() -> None:
        async with tx_env.client() as client:
            _, session = await register_verify_login(client, tx_env)
            token = session["access_token"]
            user_id = session["user"]["id"]
            created = await create_trip(client, token, trip_payload())
            trip_id = created.json()["data"]["id"]
            budget = await create_budget(client, token, trip_id, budget_payload())
            budget_id = budget.json()["data"]["id"]
            await create_spending(
                client,
                token,
                trip_id,
                spending_payload(category_budget_id=budget_id),
            )

            no_confirmation = await client.request(
                "DELETE",
                f"/api/v1/trips/{trip_id}",
                json={"confirm": False},
                headers=headers(
                    token=token,
                    idem=new_key("delete-trip"),
                    if_match='"1"',
                ),
            )
            assert no_confirmation.status_code == 422

            current = await read_trip(client, token, trip_id)
            deleted = await client.request(
                "DELETE",
                f"/api/v1/trips/{trip_id}",
                json={"confirm": True},
                headers=headers(
                    token=token,
                    idem=new_key("delete-trip"),
                    if_match=current.headers["etag"],
                ),
            )
            assert deleted.status_code == 204, deleted.text

            listing = await client.get(
                "/api/v1/trips",
                headers=headers(token=token),
            )
            assert listing.json()["data"] == []
            # Cancellation preserves the trip and all real spending history.
            assert _count(tx_env, "trips", user_id) == 1
            assert _count(tx_env, "trip_category_budgets", user_id) == 1
            assert _count(tx_env, "trip_spending_entries", user_id) == 1
            cancelled = await read_trip(client, token, trip_id)
            assert cancelled.status_code == 200
            assert cancelled.json()["data"]["status"] == "cancelled"

    asyncio.run(scenario())


def test_records_survive_new_login_and_read_from_database(tx_env: TransactionEnv):
    async def scenario() -> None:
        async with tx_env.client() as client:
            email, session = await register_verify_login(client, tx_env)
            token = session["access_token"]
            created = await create_trip(client, token, trip_payload())
            trip_id = created.json()["data"]["id"]
            await create_budget(client, token, trip_id, budget_payload())
            created_spending = await create_spending(
                client,
                token,
                trip_id,
                spending_payload(),
            )
            spending_id = created_spending.json()["data"]["id"]

            relogged = await login(client, email)
            assert relogged.status_code == 200, relogged.text
            new_token = relogged.json()["data"]["access_token"]

            trips = await client.get(
                "/api/v1/trips",
                headers=headers(token=new_token),
            )
            assert trips.status_code == 200
            assert [item["id"] for item in trips.json()["data"]] == [trip_id]

            spending = await read_spending(
                client,
                new_token,
                trip_id,
                spending_id,
            )
            assert spending.status_code == 200
            assert spending.json()["data"]["amount_kyat"] == 450_000

            row = tx_env.one(
                "SELECT amount_kyat, spending_date "
                "FROM trip_spending_entries WHERE id = :id",
                {"id": spending_id},
            )
            assert row is not None
            assert row["amount_kyat"] == 450_000

    asyncio.run(scenario())


async def dashboard_overview(
    client: httpx.AsyncClient,
    token: str,
) -> dict[str, Any]:
    response = await client.get(
        "/api/v1/dashboard/overview",
        headers=headers(token=token),
    )
    assert response.status_code == 200, response.text
    return response.json()["data"]


def configure_balance(
    env: TransactionEnv,
    session: dict[str, Any],
    *,
    starting_balance_kyat: int = 2_000_000,
    effective_date: str = "2000-01-01",
) -> None:
    set_starting_balance(
        env,
        user_id=session["user"]["id"],
        starting_balance_kyat=starting_balance_kyat,
        effective_date=effective_date,
    )


async def complete_trip(
    client: httpx.AsyncClient,
    token: str,
    trip_id: str,
    *,
    key: str | None = None,
    if_match: str | None = None,
) -> httpx.Response:
    etag = if_match
    if etag is None:
        current = await read_trip(client, token, trip_id)
        etag = current.headers["etag"]
    return await client.post(
        f"/api/v1/trips/{trip_id}/complete",
        headers=headers(
            token=token,
            idem=key or new_key("complete"),
            if_match=etag,
        ),
    )


def test_trip_allocation_immediately_affects_dashboard(tx_env: TransactionEnv):
    async def scenario() -> None:
        async with tx_env.client() as client:
            _, session = await register_verify_login(client, tx_env)
            token = session["access_token"]
            configure_balance(tx_env, session)
            groceries = category_id(tx_env, "expense", "Groceries")
            expense = await create_transaction(
                client,
                token,
                transaction_payload(
                    groceries,
                    amount_kyat=300_000,
                    transaction_date="2026-11-01",
                ),
            )
            assert expense.status_code == 201, expense.text

            created = await create_trip(client, token, trip_payload())
            trip_id = created.json()["data"]["id"]
            await create_budget(
                client,
                token,
                trip_id,
                budget_payload(budget_kyat=500_000),
            )

            data = await dashboard_overview(client, token)
            assert data["all_time_expense_kyat"] == 300_000
            assert data["travel_expense_kyat"] == 500_000
            assert data["total_expense_kyat"] == 800_000
            assert data["account_balance"]["amount_kyat"] == 1_200_000

    asyncio.run(scenario())


def test_spending_within_budget_does_not_double_count(
    tx_env: TransactionEnv,
):
    async def scenario() -> None:
        async with tx_env.client() as client:
            _, session = await register_verify_login(client, tx_env)
            token = session["access_token"]
            configure_balance(tx_env, session)

            created = await create_trip(client, token, trip_payload())
            trip_id = created.json()["data"]["id"]
            budget = await create_budget(
                client,
                token,
                trip_id,
                budget_payload(budget_kyat=500_000),
            )
            budget_id = budget.json()["data"]["id"]

            before = await dashboard_overview(client, token)
            assert before["travel_expense_kyat"] == 500_000
            assert before["account_balance"]["amount_kyat"] == 1_500_000

            spending = await create_spending(
                client,
                token,
                trip_id,
                spending_payload(
                    amount_kyat=300_000,
                    category_budget_id=budget_id,
                ),
            )
            assert spending.status_code == 201, spending.text

            after = await dashboard_overview(client, token)
            assert after["travel_expense_kyat"] == 500_000
            assert after["total_expense_kyat"] == 500_000
            assert after["account_balance"]["amount_kyat"] == 1_500_000

    asyncio.run(scenario())


def test_spending_above_budget_adds_only_excess(tx_env: TransactionEnv):
    async def scenario() -> None:
        async with tx_env.client() as client:
            _, session = await register_verify_login(client, tx_env)
            token = session["access_token"]
            configure_balance(tx_env, session)

            created = await create_trip(client, token, trip_payload())
            trip_id = created.json()["data"]["id"]
            budget = await create_budget(
                client,
                token,
                trip_id,
                budget_payload(budget_kyat=500_000),
            )
            await create_spending(
                client,
                token,
                trip_id,
                spending_payload(
                    amount_kyat=600_000,
                    category_budget_id=budget.json()["data"]["id"],
                ),
            )

            data = await dashboard_overview(client, token)
            assert data["travel_expense_kyat"] == 600_000
            assert data["total_expense_kyat"] == 600_000
            assert data["account_balance"]["amount_kyat"] == 1_400_000

    asyncio.run(scenario())


def test_complete_trip_returns_unused_budget(tx_env: TransactionEnv):
    async def scenario() -> None:
        async with tx_env.client() as client:
            _, session = await register_verify_login(client, tx_env)
            token = session["access_token"]
            configure_balance(tx_env, session)

            created = await create_trip(client, token, trip_payload())
            trip_id = created.json()["data"]["id"]
            budget = await create_budget(
                client,
                token,
                trip_id,
                budget_payload(budget_kyat=500_000),
            )
            await create_spending(
                client,
                token,
                trip_id,
                spending_payload(
                    amount_kyat=300_000,
                    category_budget_id=budget.json()["data"]["id"],
                ),
            )
            before = await dashboard_overview(client, token)
            assert before["travel_expense_kyat"] == 500_000
            assert before["account_balance"]["amount_kyat"] == 1_500_000

            completed = await complete_trip(client, token, trip_id)
            assert completed.status_code == 200, completed.text
            assert completed.json()["data"]["status"] == "completed"

            after = await dashboard_overview(client, token)
            assert after["travel_expense_kyat"] == 300_000
            assert after["total_expense_kyat"] == 300_000
            assert after["account_balance"]["amount_kyat"] == 1_700_000

    asyncio.run(scenario())


def test_complete_over_budget_trip_preserves_actual(
    tx_env: TransactionEnv,
):
    async def scenario() -> None:
        async with tx_env.client() as client:
            _, session = await register_verify_login(client, tx_env)
            token = session["access_token"]
            configure_balance(tx_env, session)

            created = await create_trip(client, token, trip_payload())
            trip_id = created.json()["data"]["id"]
            budget = await create_budget(
                client,
                token,
                trip_id,
                budget_payload(budget_kyat=500_000),
            )
            await create_spending(
                client,
                token,
                trip_id,
                spending_payload(
                    amount_kyat=600_000,
                    category_budget_id=budget.json()["data"]["id"],
                ),
            )
            before = await dashboard_overview(client, token)
            assert before["account_balance"]["amount_kyat"] == 1_400_000

            completed = await complete_trip(client, token, trip_id)
            assert completed.status_code == 200, completed.text

            after = await dashboard_overview(client, token)
            # No allocation remains, but the full 600,000 actual expense does.
            assert after["travel_expense_kyat"] == 600_000
            assert after["account_balance"]["amount_kyat"] == 1_400_000

    asyncio.run(scenario())


def test_editing_budget_recalculates_without_losing_spending(
    tx_env: TransactionEnv,
):
    async def scenario() -> None:
        async with tx_env.client() as client:
            _, session = await register_verify_login(client, tx_env)
            token = session["access_token"]
            configure_balance(tx_env, session)

            created = await create_trip(client, token, trip_payload())
            trip_id = created.json()["data"]["id"]
            budget = await create_budget(
                client,
                token,
                trip_id,
                budget_payload(budget_kyat=500_000),
            )
            budget_id = budget.json()["data"]["id"]
            await create_spending(
                client,
                token,
                trip_id,
                spending_payload(
                    amount_kyat=300_000,
                    category_budget_id=budget_id,
                ),
            )

            current = await read_budget(client, token, trip_id, budget_id)
            lowered = await client.patch(
                f"/api/v1/trips/{trip_id}/budgets/{budget_id}",
                json={"budget_kyat": 200_000},
                headers=headers(
                    token=token,
                    if_match=current.headers["etag"],
                ),
            )
            assert lowered.status_code == 200, lowered.text
            data = await dashboard_overview(client, token)
            # Actual spending (300,000) is greater than the reduced budget.
            assert data["travel_expense_kyat"] == 300_000
            assert data["account_balance"]["amount_kyat"] == 1_700_000

            current = await read_budget(client, token, trip_id, budget_id)
            raised = await client.patch(
                f"/api/v1/trips/{trip_id}/budgets/{budget_id}",
                json={"budget_kyat": 700_000},
                headers=headers(
                    token=token,
                    if_match=current.headers["etag"],
                ),
            )
            assert raised.status_code == 200, raised.text
            data = await dashboard_overview(client, token)
            assert data["travel_expense_kyat"] == 700_000
            assert data["account_balance"]["amount_kyat"] == 1_300_000

            spending = await client.get(
                f"/api/v1/trips/{trip_id}/spending",
                headers=headers(token=token),
            )
            assert spending.status_code == 200
            assert [item["amount_kyat"] for item in spending.json()["data"]] == [
                300_000
            ]

    asyncio.run(scenario())


def test_delete_releases_unused_allocation_and_keeps_spending(
    tx_env: TransactionEnv,
):
    async def scenario() -> None:
        async with tx_env.client() as client:
            _, session = await register_verify_login(client, tx_env)
            token = session["access_token"]
            user_id = session["user"]["id"]
            configure_balance(tx_env, session)

            created = await create_trip(client, token, trip_payload())
            trip_id = created.json()["data"]["id"]
            budget = await create_budget(
                client,
                token,
                trip_id,
                budget_payload(budget_kyat=500_000),
            )
            await create_spending(
                client,
                token,
                trip_id,
                spending_payload(
                    amount_kyat=300_000,
                    category_budget_id=budget.json()["data"]["id"],
                ),
            )
            before = await dashboard_overview(client, token)
            assert before["travel_expense_kyat"] == 500_000

            current = await read_trip(client, token, trip_id)
            deleted = await client.request(
                "DELETE",
                f"/api/v1/trips/{trip_id}",
                json={"confirm": True},
                headers=headers(
                    token=token,
                    idem=new_key("delete-trip"),
                    if_match=current.headers["etag"],
                ),
            )
            assert deleted.status_code == 204, deleted.text

            after = await dashboard_overview(client, token)
            assert after["travel_expense_kyat"] == 300_000
            assert after["account_balance"]["amount_kyat"] == 1_700_000
            assert _count(tx_env, "trip_spending_entries", user_id) == 1

    asyncio.run(scenario())


def test_repeated_completion_is_idempotent(tx_env: TransactionEnv):
    async def scenario() -> None:
        async with tx_env.client() as client:
            _, session = await register_verify_login(client, tx_env)
            token = session["access_token"]
            configure_balance(tx_env, session)

            created = await create_trip(client, token, trip_payload())
            trip_id = created.json()["data"]["id"]
            await create_budget(
                client,
                token,
                trip_id,
                budget_payload(budget_kyat=500_000),
            )
            current = await read_trip(client, token, trip_id)
            first_key = new_key("complete")
            first = await complete_trip(
                client,
                token,
                trip_id,
                key=first_key,
                if_match=current.headers["etag"],
            )
            assert first.status_code == 200, first.text
            after_first = await dashboard_overview(client, token)

            replay = await complete_trip(
                client,
                token,
                trip_id,
                key=first_key,
                if_match=current.headers["etag"],
            )
            assert replay.status_code == 200, replay.text
            again = await complete_trip(
                client,
                token,
                trip_id,
                key=new_key("complete"),
                if_match=current.headers["etag"],
            )
            assert again.status_code == 200, again.text
            after_repeat = await dashboard_overview(client, token)

            assert (
                after_repeat["travel_expense_kyat"]
                == after_first["travel_expense_kyat"]
            )
            assert (
                after_repeat["account_balance"]["amount_kyat"]
                == after_first["account_balance"]["amount_kyat"]
            )
            detail = await read_trip(client, token, trip_id)
            assert detail.json()["data"]["status"] == "completed"

    asyncio.run(scenario())


def test_write_endpoints_accept_weak_if_match_revision_tokens(
    tx_env: TransactionEnv,
):
    malformed_tags = (
        'W/"not-a-revision"',
        "W/1",
        'w/"1"',
        'W/"1", "2"',
        'W/"',
    )

    async def scenario() -> None:
        async with tx_env.client() as client:
            _, session = await register_verify_login(client, tx_env)
            token = session["access_token"]
            configure_balance(tx_env, session)

            # Trip PATCH, completion, and deletion.
            trip = await create_trip(client, token, trip_payload())
            assert trip.status_code == 201, trip.text
            trip_id = trip.json()["data"]["id"]
            trip_weak = f'W/{trip.headers["etag"]}'

            missing = await client.patch(
                f"/api/v1/trips/{trip_id}",
                json={"name": "Missing"},
                headers=headers(token=token),
            )
            assert missing.status_code == 428
            assert missing.json()["error"]["code"] == "precondition_required"

            stale = await client.patch(
                f"/api/v1/trips/{trip_id}",
                json={"name": "Stale"},
                headers=headers(token=token, if_match='W/"99"'),
            )
            assert stale.status_code == 412
            assert stale.json()["error"]["code"] == "stale_revision"

            for malformed in malformed_tags:
                response = await client.patch(
                    f"/api/v1/trips/{trip_id}",
                    json={"name": "Malformed"},
                    headers=headers(token=token, if_match=malformed),
                )
                assert response.status_code == 412, (malformed, response.text)

            updated = await client.patch(
                f"/api/v1/trips/{trip_id}",
                json={"name": "Weakly updated"},
                headers=headers(token=token, if_match=trip_weak),
            )
            assert updated.status_code == 200, updated.text
            assert updated.json()["data"]["name"] == "Weakly updated"
            assert updated.headers["etag"] == '"2"'
            updated_trip_weak = f'W/{updated.headers["etag"]}'

            stale_weak = await client.patch(
                f"/api/v1/trips/{trip_id}",
                json={"name": "Stale weak"},
                headers=headers(token=token, if_match=trip_weak),
            )
            assert stale_weak.status_code == 412
            assert stale_weak.json()["error"]["code"] == "stale_revision"

            stale_completion = await client.post(
                f"/api/v1/trips/{trip_id}/complete",
                headers=headers(
                    token=token,
                    idem=new_key("weak-complete-stale"),
                    if_match=trip_weak,
                ),
            )
            assert stale_completion.status_code == 412
            assert stale_completion.json()["error"]["code"] == "stale_revision"

            completed = await client.post(
                f"/api/v1/trips/{trip_id}/complete",
                headers=headers(
                    token=token,
                    idem=new_key("weak-complete"),
                    if_match=updated_trip_weak,
                ),
            )
            assert completed.status_code == 200, completed.text
            assert completed.json()["data"]["status"] == "completed"
            assert completed.headers["etag"] == '"3"'

            missing_completion_trip = await create_trip(
                client,
                token,
                trip_payload(name="Missing completion"),
            )
            missing_completion_id = missing_completion_trip.json()["data"]["id"]
            missing_completion = await client.post(
                f"/api/v1/trips/{missing_completion_id}/complete",
                headers=headers(
                    token=token,
                    idem=new_key("weak-complete-missing"),
                ),
            )
            assert missing_completion.status_code == 428
            assert missing_completion.json()["error"]["code"] == (
                "precondition_required"
            )

            delete_trip_created = await create_trip(
                client,
                token,
                trip_payload(name="Weak delete"),
            )
            delete_trip_id = delete_trip_created.json()["data"]["id"]
            delete_trip_weak = f'W/{delete_trip_created.headers["etag"]}'

            delete_missing = await client.request(
                "DELETE",
                f"/api/v1/trips/{delete_trip_id}",
                json={"confirm": True},
                headers=headers(token=token, idem=new_key("weak-delete-trip")),
            )
            assert delete_missing.status_code == 428

            delete_stale = await client.request(
                "DELETE",
                f"/api/v1/trips/{delete_trip_id}",
                json={"confirm": True},
                headers=headers(
                    token=token,
                    idem=new_key("weak-delete-trip-stale"),
                    if_match='W/"99"',
                ),
            )
            assert delete_stale.status_code == 412
            assert delete_stale.json()["error"]["code"] == "stale_revision"

            deleted = await client.request(
                "DELETE",
                f"/api/v1/trips/{delete_trip_id}",
                json={"confirm": True},
                headers=headers(
                    token=token,
                    idem=new_key("weak-delete-trip-ok"),
                    if_match=delete_trip_weak,
                ),
            )
            assert deleted.status_code == 204, deleted.text
            cancelled = await read_trip(client, token, delete_trip_id)
            assert cancelled.json()["data"]["status"] == "cancelled"

            # Planned-expense PATCH and DELETE.
            budget_trip = await create_trip(
                client,
                token,
                trip_payload(name="Budget trip"),
            )
            budget_trip_id = budget_trip.json()["data"]["id"]
            budget = await create_budget(
                client,
                token,
                budget_trip_id,
                budget_payload(),
            )
            assert budget.status_code == 201, budget.text
            budget_id = budget.json()["data"]["id"]
            budget_weak = f'W/{budget.headers["etag"]}'

            budget_missing = await client.patch(
                f"/api/v1/trips/{budget_trip_id}/budgets/{budget_id}",
                json={"budget_kyat": 2_000_000},
                headers=headers(token=token),
            )
            assert budget_missing.status_code == 428

            budget_stale = await client.patch(
                f"/api/v1/trips/{budget_trip_id}/budgets/{budget_id}",
                json={"budget_kyat": 2_000_000},
                headers=headers(token=token, if_match='W/"99"'),
            )
            assert budget_stale.status_code == 412

            for malformed in malformed_tags:
                response = await client.patch(
                    f"/api/v1/trips/{budget_trip_id}/budgets/{budget_id}",
                    json={"budget_kyat": 2_000_000},
                    headers=headers(token=token, if_match=malformed),
                )
                assert response.status_code == 412, (malformed, response.text)

            budget_updated = await client.patch(
                f"/api/v1/trips/{budget_trip_id}/budgets/{budget_id}",
                json={"category": "Air travel", "budget_kyat": 2_000_000},
                headers=headers(token=token, if_match=budget_weak),
            )
            assert budget_updated.status_code == 200, budget_updated.text
            assert budget_updated.json()["data"]["category"] == "Air travel"
            assert budget_updated.json()["data"]["budget_kyat"] == 2_000_000
            assert budget_updated.headers["etag"] == '"2"'
            updated_budget_weak = f'W/{budget_updated.headers["etag"]}'

            budget_stale_weak = await client.patch(
                f"/api/v1/trips/{budget_trip_id}/budgets/{budget_id}",
                json={"budget_kyat": 2_100_000},
                headers=headers(token=token, if_match=budget_weak),
            )
            assert budget_stale_weak.status_code == 412

            budget_delete_missing = await client.request(
                "DELETE",
                f"/api/v1/trips/{budget_trip_id}/budgets/{budget_id}",
                headers=headers(
                    token=token,
                    idem=new_key("weak-delete-budget"),
                ),
            )
            assert budget_delete_missing.status_code == 428

            budget_delete_stale = await client.request(
                "DELETE",
                f"/api/v1/trips/{budget_trip_id}/budgets/{budget_id}",
                headers=headers(
                    token=token,
                    idem=new_key("weak-delete-budget-stale"),
                    if_match='W/"99"',
                ),
            )
            assert budget_delete_stale.status_code == 412

            budget_deleted = await client.request(
                "DELETE",
                f"/api/v1/trips/{budget_trip_id}/budgets/{budget_id}",
                headers=headers(
                    token=token,
                    idem=new_key("weak-delete-budget-ok"),
                    if_match=updated_budget_weak,
                ),
            )
            assert budget_deleted.status_code == 204, budget_deleted.text
            deleted_budget = await read_budget(
                client,
                token,
                budget_trip_id,
                budget_id,
            )
            assert deleted_budget.status_code == 404

            # Spending PATCH and DELETE.
            spending_trip = await create_trip(
                client,
                token,
                trip_payload(name="Spending trip"),
            )
            spending_trip_id = spending_trip.json()["data"]["id"]
            spending = await create_spending(
                client,
                token,
                spending_trip_id,
                spending_payload(),
            )
            assert spending.status_code == 201, spending.text
            spending_id = spending.json()["data"]["id"]
            spending_weak = f'W/{spending.headers["etag"]}'

            spending_missing = await client.patch(
                f"/api/v1/trips/{spending_trip_id}/spending/{spending_id}",
                json={"amount_kyat": 500_000},
                headers=headers(token=token),
            )
            assert spending_missing.status_code == 428

            spending_stale = await client.patch(
                f"/api/v1/trips/{spending_trip_id}/spending/{spending_id}",
                json={"amount_kyat": 500_000},
                headers=headers(token=token, if_match='W/"99"'),
            )
            assert spending_stale.status_code == 412

            for malformed in malformed_tags:
                response = await client.patch(
                    f"/api/v1/trips/{spending_trip_id}/spending/{spending_id}",
                    json={"amount_kyat": 500_000},
                    headers=headers(token=token, if_match=malformed),
                )
                assert response.status_code == 412, (malformed, response.text)

            spending_updated = await client.patch(
                f"/api/v1/trips/{spending_trip_id}/spending/{spending_id}",
                json={
                    "description": "Weakly updated",
                    "amount_kyat": 500_000,
                },
                headers=headers(token=token, if_match=spending_weak),
            )
            assert spending_updated.status_code == 200, spending_updated.text
            assert spending_updated.json()["data"]["description"] == (
                "Weakly updated"
            )
            assert spending_updated.json()["data"]["amount_kyat"] == 500_000
            assert spending_updated.headers["etag"] == '"2"'
            updated_spending_weak = f'W/{spending_updated.headers["etag"]}'

            spending_stale_weak = await client.patch(
                f"/api/v1/trips/{spending_trip_id}/spending/{spending_id}",
                json={"amount_kyat": 550_000},
                headers=headers(token=token, if_match=spending_weak),
            )
            assert spending_stale_weak.status_code == 412

            spending_delete_missing = await client.request(
                "DELETE",
                f"/api/v1/trips/{spending_trip_id}/spending/{spending_id}",
                headers=headers(
                    token=token,
                    idem=new_key("weak-delete-spending"),
                ),
            )
            assert spending_delete_missing.status_code == 428

            spending_delete_stale = await client.request(
                "DELETE",
                f"/api/v1/trips/{spending_trip_id}/spending/{spending_id}",
                headers=headers(
                    token=token,
                    idem=new_key("weak-delete-spending-stale"),
                    if_match='W/"99"',
                ),
            )
            assert spending_delete_stale.status_code == 412

            spending_deleted = await client.request(
                "DELETE",
                f"/api/v1/trips/{spending_trip_id}/spending/{spending_id}",
                headers=headers(
                    token=token,
                    idem=new_key("weak-delete-spending-ok"),
                    if_match=updated_spending_weak,
                ),
            )
            assert spending_deleted.status_code == 204, spending_deleted.text
            deleted_spending = await read_spending(
                client,
                token,
                spending_trip_id,
                spending_id,
            )
            assert deleted_spending.status_code == 404

    asyncio.run(scenario())


def test_import_does_not_duplicate_accounting(tx_env: TransactionEnv):
    async def scenario() -> None:
        async with tx_env.client() as client:
            _, session = await register_verify_login(client, tx_env)
            token = session["access_token"]
            configure_balance(tx_env, session)
            document = {
                "trips": [
                    {
                        "name": "Imported trip",
                        "destination": "Bagan",
                        "start_date": "2027-01-10",
                        "end_date": "2027-01-14",
                        "budgets": [
                            {"category": "Lodging", "budget_kyat": 500_000}
                        ],
                        "spending": [
                            {
                                "description": "Hotel",
                                "category": "Lodging",
                                "amount_kyat": 200_000,
                                "spending_date": "2026-12-01",
                                "budget_category": "Lodging",
                            }
                        ],
                    }
                ]
            }
            first = await client.post(
                "/api/v1/trips/import",
                json=document,
                headers=headers(token=token, idem=new_key("import")),
            )
            assert first.status_code == 200, first.text
            assert first.json()["data"]["trips_created"] == 1
            after_first = await dashboard_overview(client, token)
            assert after_first["travel_expense_kyat"] == 500_000

            second = await client.post(
                "/api/v1/trips/import",
                json=document,
                headers=headers(token=token, idem=new_key("import")),
            )
            assert second.status_code == 200, second.text
            assert second.json()["data"]["trips_created"] == 0
            assert second.json()["data"]["trips_skipped_duplicates"] == 1
            after_second = await dashboard_overview(client, token)
            assert (
                after_second["travel_expense_kyat"]
                == after_first["travel_expense_kyat"]
            )
            assert (
                after_second["account_balance"]["amount_kyat"]
                == after_first["account_balance"]["amount_kyat"]
            )

    asyncio.run(scenario())


def test_multiple_trips_recognize_each_trip_once(
    tx_env: TransactionEnv,
):
    async def scenario() -> None:
        async with tx_env.client() as client:
            _, session = await register_verify_login(client, tx_env)
            token = session["access_token"]
            configure_balance(tx_env, session)

            first = await create_trip(
                client,
                token,
                trip_payload(name="Trip A", destination="A"),
            )
            first_id = first.json()["data"]["id"]
            first_budget = await create_budget(
                client,
                token,
                first_id,
                budget_payload(budget_kyat=500_000),
            )
            await create_spending(
                client,
                token,
                first_id,
                spending_payload(
                    amount_kyat=300_000,
                    category_budget_id=first_budget.json()["data"]["id"],
                ),
            )

            second = await create_trip(
                client,
                token,
                trip_payload(name="Trip B", destination="B"),
            )
            second_id = second.json()["data"]["id"]
            second_budget = await create_budget(
                client,
                token,
                second_id,
                budget_payload(budget_kyat=200_000),
            )
            await create_spending(
                client,
                token,
                second_id,
                spending_payload(
                    amount_kyat=400_000,
                    category_budget_id=second_budget.json()["data"]["id"],
                ),
            )

            third = await create_trip(
                client,
                token,
                trip_payload(name="Trip C", destination="C"),
            )
            third_id = third.json()["data"]["id"]
            third_budget = await create_budget(
                client,
                token,
                third_id,
                budget_payload(budget_kyat=100_000),
            )
            await create_spending(
                client,
                token,
                third_id,
                spending_payload(
                    amount_kyat=50_000,
                    category_budget_id=third_budget.json()["data"]["id"],
                ),
            )
            completed = await complete_trip(client, token, third_id)
            assert completed.status_code == 200, completed.text

            data = await dashboard_overview(client, token)
            # A: max(500k, 300k) + B: max(200k, 400k) + C: actual 50k
            assert data["travel_expense_kyat"] == 950_000
            assert data["total_expense_kyat"] == 950_000
            assert data["account_balance"]["amount_kyat"] == 1_050_000

    asyncio.run(scenario())
