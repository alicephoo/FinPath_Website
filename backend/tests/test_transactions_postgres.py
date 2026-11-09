"""PostgreSQL tests for transaction CRUD, filtering, pagination, and idempotency."""

from __future__ import annotations

import asyncio
from uuid import UUID, uuid4

from sqlalchemy import text

from tests.tx_support import (
    ORIGIN,
    TransactionEnv,
    category_id,
    create_transaction,
    headers,
    insert_bulk_transactions,
    new_key,
    register_verify_login,
    transaction_payload,
)


def _db_transaction(env: TransactionEnv, transaction_id: str):
    return env.one(
        "SELECT id, user_id, category_id, type, description, amount_kyat, "
        "transaction_date, revision FROM transactions WHERE id = :id",
        {"id": transaction_id},
    )


def test_create_income_and_expense_with_authenticated_ownership(tx_env: TransactionEnv):
    salary = category_id(tx_env, "income", "Salary")
    groceries = category_id(tx_env, "expense", "Groceries")

    async def scenario() -> None:
        async with tx_env.client() as client:
            _, session = await register_verify_login(client, tx_env)
            token = session["access_token"]
            user_id = session["user"]["id"]

            income = await create_transaction(
                client,
                token,
                transaction_payload(
                    salary,
                    description="  November salary  ",
                    transaction_type="income",
                    amount_kyat=500_000,
                    transaction_date="2026-11-01",
                ),
            )
            assert income.status_code == 201, income.text
            body = income.json()["data"]
            assert body["description"] == "November salary"
            assert body["type"] == "income"
            assert body["category_id"] == str(salary)
            assert body["category"] == "Salary"
            assert body["amount_kyat"] == 500_000
            assert body["transaction_date"] == "2026-11-01"
            assert "user_id" not in body
            assert income.headers["etag"] == '"1"'
            assert UUID(body["id"])

            expense = await create_transaction(
                client,
                token,
                transaction_payload(
                    groceries,
                    description="Market",
                    amount_kyat=12_500,
                    transaction_date="2026-11-03",
                ),
            )
            assert expense.status_code == 201, expense.text
            assert expense.json()["data"]["type"] == "expense"
            assert expense.json()["data"]["category"] == "Groceries"
            assert expense.headers["etag"] == '"1"'

            row = _db_transaction(tx_env, body["id"])
            assert row is not None
            assert str(row["user_id"]) == user_id
            assert row["amount_kyat"] == 500_000
            assert row["revision"] == 1

    asyncio.run(scenario())


def test_create_validation_rejects_invalid_fields_and_amounts(tx_env: TransactionEnv):
    income = category_id(tx_env, "income", "Salary")
    expense = category_id(tx_env, "expense", "Groceries")

    async def scenario() -> None:
        async with tx_env.client() as client:
            _, session = await register_verify_login(client, tx_env)
            token = session["access_token"]
            valid = transaction_payload(expense)

            unauthenticated = await client.post(
                "/api/v1/transactions", json=valid
            )
            assert unauthenticated.status_code == 401
            assert unauthenticated.json()["error"]["code"] == (
                "authentication_required"
            )

            missing_key = await client.post(
                "/api/v1/transactions",
                json=valid,
                headers=headers(token=token),
            )
            assert missing_key.status_code == 422
            assert missing_key.json()["error"]["code"] == "validation_error"

            short_key = await client.post(
                "/api/v1/transactions",
                json=valid,
                headers=headers(token=token, idem="too-short"),
            )
            assert short_key.status_code == 422
            assert short_key.json()["error"]["code"] == "validation_error"

            cases: list[tuple[dict, int, str | None]] = [
                (
                    transaction_payload(uuid4()),
                    422,
                    "invalid_category",
                ),
                (
                    transaction_payload(income, transaction_type="expense"),
                    422,
                    "category_type_mismatch",
                ),
                (
                    {
                        key: value
                        for key, value in valid.items()
                        if key != "description"
                    },
                    422,
                    None,
                ),
                (transaction_payload(expense, amount_kyat=0), 422, None),
                (transaction_payload(expense, amount_kyat=-1), 422, None),
                (transaction_payload(expense, amount_kyat=1.5), 422, None),
                (transaction_payload(expense, amount_kyat=1000.0), 422, None),
                (
                    transaction_payload(
                        expense, amount_kyat=1_000_000_001
                    ),
                    422,
                    "amount_limit_exceeded",
                ),
                (
                    transaction_payload(expense, transaction_date="2026-02-30"),
                    422,
                    None,
                ),
                (
                    transaction_payload(
                        expense, transaction_date="2026-11-03T00:00:00"
                    ),
                    422,
                    None,
                ),
                (
                    transaction_payload(expense, description="   "),
                    422,
                    None,
                ),
                (
                    transaction_payload(expense, description="x" * 81),
                    422,
                    None,
                ),
                (
                    {**valid, "user_id": str(uuid4())},
                    422,
                    None,
                ),
                (
                    {**valid, "unknown": "value"},
                    422,
                    None,
                ),
            ]
            for payload, status, field_code in cases:
                response = await create_transaction(client, token, payload)
                assert response.status_code == status, (payload, response.text)
                if field_code is not None:
                    errors = response.json()["error"]["field_errors"]
                    assert any(
                        error["code"] == field_code for error in errors
                    ), (payload, errors)

            # An inactive category is not assignable to a new transaction.
            tx_env.execute(
                "UPDATE transaction_categories SET is_active = false "
                "WHERE id = :id",
                {"id": category_id(tx_env, "expense", "Other")},
            )
            try:
                inactive = await create_transaction(
                    client,
                    token,
                    transaction_payload(category_id(tx_env, "expense", "Other")),
                )
                assert inactive.status_code == 422
                assert any(
                    error["code"] == "invalid_category"
                    for error in inactive.json()["error"]["field_errors"]
                )
            finally:
                tx_env.execute(
                    "UPDATE transaction_categories SET is_active = true "
                    "WHERE id = :id",
                    {"id": category_id(tx_env, "expense", "Other")},
                )

            count = tx_env.one(
                "SELECT count(*) AS count FROM transactions WHERE user_id = :id",
                {"id": session["user"]["id"]},
            )
            assert count is not None and count["count"] == 0

    asyncio.run(scenario())


def test_create_idempotency_replay_and_payload_conflict(tx_env: TransactionEnv):
    groceries = category_id(tx_env, "expense", "Groceries")
    key = new_key("idem")

    async def scenario() -> None:
        async with tx_env.client() as client:
            _, session = await register_verify_login(client, tx_env)
            token = session["access_token"]
            payload = transaction_payload(groceries, description="Market")

            first = await create_transaction(client, token, payload, key=key)
            assert first.status_code == 201, first.text
            second = await create_transaction(client, token, payload, key=key)
            assert second.status_code == 201, second.text
            assert second.json() == first.json()
            assert second.headers["etag"] == first.headers["etag"]

            conflict = await create_transaction(
                client,
                token,
                {**payload, "amount_kyat": 99_999},
                key=key,
            )
            assert conflict.status_code == 409
            assert conflict.json()["error"]["code"] == "idempotency_key_reused"

            rows = tx_env.all(
                "SELECT id FROM transactions WHERE user_id = :id",
                {"id": session["user"]["id"]},
            )
            assert len(rows) == 1
            record = tx_env.one(
                "SELECT state, response_status, response_body, response_headers "
                "FROM idempotency_records "
                "WHERE owner_user_id = :id AND route_key = '/api/v1/transactions'",
                {"id": session["user"]["id"]},
            )
            assert record is not None
            assert record["state"] == "completed"
            assert record["response_status"] == 201
            assert record["response_body"]["data"]["id"] == first.json()["data"]["id"]
            assert record["response_headers"] == {"ETag": '"1"'}

    asyncio.run(scenario())


def test_create_idempotency_concurrent_duplicates_create_one_row(tx_env: TransactionEnv):
    groceries = category_id(tx_env, "expense", "Groceries")
    key = new_key("concurrent")

    async def scenario() -> None:
        async with tx_env.client() as client:
            _, session = await register_verify_login(client, tx_env)
            token = session["access_token"]
            payload = transaction_payload(groceries, description="Concurrent")

            async with tx_env.client() as first_client, tx_env.client() as second_client:
                responses = await asyncio.gather(
                    create_transaction(first_client, token, payload, key=key),
                    create_transaction(second_client, token, payload, key=key),
                )
            assert [response.status_code for response in responses] == [201, 201]
            assert responses[0].json() == responses[1].json()

            rows = tx_env.all(
                "SELECT id FROM transactions WHERE user_id = :id",
                {"id": session["user"]["id"]},
            )
            assert len(rows) == 1

    asyncio.run(scenario())


def test_failed_creation_rolls_back_in_progress_idempotency_record(tx_env: TransactionEnv):
    groceries = category_id(tx_env, "expense", "Groceries")
    key = new_key("retry")

    async def scenario() -> None:
        async with tx_env.client() as client:
            _, session = await register_verify_login(client, tx_env)
            token = session["access_token"]

            failed = await create_transaction(
                client,
                token,
                transaction_payload(uuid4()),
                key=key,
            )
            assert failed.status_code == 422
            assert tx_env.one(
                "SELECT id FROM idempotency_records WHERE owner_user_id = :id",
                {"id": session["user"]["id"]},
            ) is None

            retried = await create_transaction(
                client,
                token,
                transaction_payload(groceries, description="Retry"),
                key=key,
            )
            assert retried.status_code == 201, retried.text

    asyncio.run(scenario())


def test_retrieve_own_and_hide_other_users_transactions(tx_env: TransactionEnv):
    groceries = category_id(tx_env, "expense", "Groceries")

    async def scenario() -> None:
        async with tx_env.client() as client:
            _, first_session = await register_verify_login(client, tx_env)
            _, second_session = await register_verify_login(client, tx_env)
            first_token = first_session["access_token"]
            second_token = second_session["access_token"]

            created = await create_transaction(
                client,
                first_token,
                transaction_payload(groceries, description="Private"),
            )
            assert created.status_code == 201
            transaction_id = created.json()["data"]["id"]

            own = await client.get(
                f"/api/v1/transactions/{transaction_id}",
                headers=headers(token=first_token),
            )
            assert own.status_code == 200
            assert own.json() == created.json()
            assert own.headers["etag"] == '"1"'

            other = await client.get(
                f"/api/v1/transactions/{transaction_id}",
                headers=headers(token=second_token),
            )
            assert other.status_code == 404
            assert other.json()["error"]["code"] == "not_found"
            assert "Private" not in other.text

            missing = await client.get(
                f"/api/v1/transactions/{uuid4()}",
                headers=headers(token=first_token),
            )
            assert missing.status_code == 404

            unauth = await client.get(f"/api/v1/transactions/{transaction_id}")
            assert unauth.status_code == 401

            other_list = await client.get(
                "/api/v1/transactions", headers=headers(token=second_token)
            )
            assert other_list.status_code == 200
            assert other_list.json()["data"] == []

    asyncio.run(scenario())


def test_list_filters_search_sorting_and_pagination(tx_env: TransactionEnv):
    salary = category_id(tx_env, "income", "Salary")
    groceries = category_id(tx_env, "expense", "Groceries")
    transport = category_id(tx_env, "expense", "Transport")

    async def scenario() -> None:
        async with tx_env.client() as client:
            _, session = await register_verify_login(client, tx_env)
            token = session["access_token"]
            payloads = [
                transaction_payload(
                    salary,
                    description="October salary",
                    transaction_type="income",
                    amount_kyat=100_000,
                    transaction_date="2026-10-01",
                ),
                transaction_payload(
                    groceries,
                    description="Market groceries",
                    amount_kyat=20_000,
                    transaction_date="2026-10-15",
                ),
                transaction_payload(
                    transport,
                    description="Taxi to market",
                    amount_kyat=5_000,
                    transaction_date="2026-10-20",
                ),
                transaction_payload(
                    groceries,
                    description="Market groceries again",
                    amount_kyat=7_000,
                    transaction_date="2026-10-20",
                ),
                transaction_payload(
                    groceries,
                    description="November groceries",
                    amount_kyat=9_000,
                    transaction_date="2026-11-02",
                ),
            ]
            created_ids: list[str] = []
            for payload in payloads:
                response = await create_transaction(client, token, payload)
                assert response.status_code == 201, response.text
                created_ids.append(response.json()["data"]["id"])

            async def listing(query: str) -> dict:
                response = await client.get(
                    f"/api/v1/transactions{query}",
                    headers=headers(token=token),
                )
                assert response.status_code == 200, response.text
                return response.json()

            income_only = await listing("?type=income")
            assert [item["type"] for item in income_only["data"]] == ["income"]
            assert len(income_only["data"]) == 1

            category_only = await listing(f"?category_id={groceries}")
            assert {item["category"] for item in category_only["data"]} == {
                "Groceries"
            }
            assert len(category_only["data"]) == 3

            inclusive = await listing(
                "?from=2026-10-15&to=2026-10-20"
            )
            assert len(inclusive["data"]) == 3
            assert {
                item["transaction_date"] for item in inclusive["data"]
            } == {"2026-10-15", "2026-10-20"}

            search = await listing("?q=market")
            assert len(search["data"]) == 3
            # Search is description-only; a category-name match must not count.
            category_search = await listing("?q=Transport")
            assert category_search["data"] == []

            empty = await listing("?q=does-not-exist")
            assert empty["data"] == []
            assert empty["pagination"]["next_cursor"] is None

            dates = [item["transaction_date"] for item in (await listing(""))["data"]]
            assert dates == sorted(dates, reverse=True)

            first_page = await listing("?limit=2")
            assert len(first_page["data"]) == 2
            assert first_page["pagination"]["limit"] == 2
            assert first_page["pagination"]["next_cursor"] is not None

            seen: list[str] = []
            cursor = None
            for _ in range(10):
                query = "?limit=2"
                if cursor is not None:
                    query += f"&cursor={cursor}"
                page = await listing(query)
                seen.extend(item["id"] for item in page["data"])
                cursor = page["pagination"]["next_cursor"]
                if cursor is None:
                    break
            assert len(seen) == len(payloads)
            assert len(set(seen)) == len(payloads)
            assert set(seen) == set(created_ids)

            reversed_range = await client.get(
                "/api/v1/transactions?from=2026-11-01&to=2026-10-01",
                headers=headers(token=token),
            )
            assert reversed_range.status_code == 422
            assert reversed_range.json()["error"]["code"] == "invalid_date_range"

            malformed_range = await client.get(
                "/api/v1/transactions?from=2026-13-01",
                headers=headers(token=token),
            )
            assert malformed_range.status_code == 422
            assert malformed_range.json()["error"]["code"] == "invalid_date_range"

            for query in ("?limit=0", "?limit=101", "?limit=abc"):
                invalid = await client.get(
                    f"/api/v1/transactions{query}",
                    headers=headers(token=token),
                )
                assert invalid.status_code == 422, query

            invalid_cursor = await client.get(
                "/api/v1/transactions?cursor=not-a-cursor",
                headers=headers(token=token),
            )
            assert invalid_cursor.status_code == 422
            assert any(
                error["code"] == "invalid_cursor"
                for error in invalid_cursor.json()["error"]["field_errors"]
            )

    asyncio.run(scenario())


def test_large_history_is_paginated_in_the_database(tx_env: TransactionEnv):
    groceries = category_id(tx_env, "expense", "Groceries")

    async def scenario() -> None:
        async with tx_env.client() as client:
            _, session = await register_verify_login(client, tx_env)
            token = session["access_token"]
            user_id = session["user"]["id"]
            insert_bulk_transactions(
                tx_env,
                user_id=user_id,
                category=groceries,
                count=130,
            )

            first = await client.get(
                "/api/v1/transactions?limit=100",
                headers=headers(token=token),
            )
            assert first.status_code == 200, first.text
            first_body = first.json()
            assert len(first_body["data"]) == 100
            assert first_body["pagination"]["next_cursor"] is not None

            second = await client.get(
                "/api/v1/transactions?limit=100&cursor="
                + first_body["pagination"]["next_cursor"],
                headers=headers(token=token),
            )
            assert second.status_code == 200, second.text
            second_body = second.json()
            assert len(second_body["data"]) == 30
            assert second_body["pagination"]["next_cursor"] is None

            ids = {item["id"] for item in first_body["data"] + second_body["data"]}
            assert len(ids) == 130

    asyncio.run(scenario())


def test_update_partial_and_full_with_revision_preconditions(tx_env: TransactionEnv):
    groceries = category_id(tx_env, "expense", "Groceries")
    salary = category_id(tx_env, "income", "Salary")

    async def scenario() -> None:
        async with tx_env.client() as client:
            _, session = await register_verify_login(client, tx_env)
            token = session["access_token"]
            created = await create_transaction(
                client,
                token,
                transaction_payload(groceries, description="Market"),
            )
            transaction_id = created.json()["data"]["id"]
            created_at = created.json()["data"]["created_at"]

            missing = await client.patch(
                f"/api/v1/transactions/{transaction_id}",
                json={"description": "Updated"},
                headers=headers(token=token),
            )
            assert missing.status_code == 428
            assert missing.json()["error"]["code"] == "precondition_required"

            stale = await client.patch(
                f"/api/v1/transactions/{transaction_id}",
                json={"description": "Updated"},
                headers=headers(token=token, if_match='"99"'),
            )
            assert stale.status_code == 412
            assert stale.json()["error"]["code"] == "stale_revision"

            malformed = await client.patch(
                f"/api/v1/transactions/{transaction_id}",
                json={"description": "Updated"},
                headers=headers(token=token, if_match="not-an-etag"),
            )
            assert malformed.status_code == 412

            empty = await client.patch(
                f"/api/v1/transactions/{transaction_id}",
                json={},
                headers=headers(token=token, if_match='"1"'),
            )
            assert empty.status_code == 422

            partial = await client.patch(
                f"/api/v1/transactions/{transaction_id}",
                json={"description": "  Updated market  "},
                headers=headers(token=token, if_match='"1"'),
            )
            assert partial.status_code == 200, partial.text
            partial_body = partial.json()["data"]
            assert partial_body["description"] == "Updated market"
            assert partial_body["amount_kyat"] == 12500
            assert partial_body["type"] == "expense"
            assert partial_body["category_id"] == str(groceries)
            assert partial_body["created_at"] == created_at
            assert partial.headers["etag"] == '"2"'

            full = await client.patch(
                f"/api/v1/transactions/{transaction_id}",
                json={
                    "description": "Salary corrected",
                    "type": "income",
                    "category_id": str(salary),
                    "amount_kyat": 250_000,
                    "transaction_date": "2026-11-04",
                },
                headers=headers(token=token, if_match='"2"'),
            )
            assert full.status_code == 200, full.text
            full_body = full.json()["data"]
            assert full_body["description"] == "Salary corrected"
            assert full_body["type"] == "income"
            assert full_body["category"] == "Salary"
            assert full_body["amount_kyat"] == 250_000
            assert full_body["transaction_date"] == "2026-11-04"
            assert full.headers["etag"] == '"3"'

            row = _db_transaction(tx_env, transaction_id)
            assert row is not None
            assert row["revision"] == 3
            assert row["amount_kyat"] == 250_000

    asyncio.run(scenario())


def test_update_rejects_invalid_values_and_leaves_data_unchanged(tx_env: TransactionEnv):
    groceries = category_id(tx_env, "expense", "Groceries")
    salary = category_id(tx_env, "income", "Salary")

    async def scenario() -> None:
        async with tx_env.client() as client:
            _, session = await register_verify_login(client, tx_env)
            token = session["access_token"]
            created = await create_transaction(
                client,
                token,
                transaction_payload(groceries, description="Original"),
            )
            transaction_id = created.json()["data"]["id"]

            invalid_cases = [
                {"amount_kyat": 0},
                {"amount_kyat": -5},
                {"amount_kyat": 1.5},
                {"amount_kyat": 1_000_000_001},
                {"category_id": str(uuid4())},
                {"category_id": str(salary)},
                {"type": "income"},
                {"description": "   "},
                {"transaction_date": "2026-11-03T00:00:00"},
                {"transaction_date": "2026-02-30"},
                {"user_id": str(uuid4())},
            ]
            for index, change in enumerate(invalid_cases):
                response = await client.patch(
                    f"/api/v1/transactions/{transaction_id}",
                    json=change,
                    headers=headers(token=token, if_match='"1"'),
                )
                assert response.status_code == 422, (index, change, response.text)
                row = _db_transaction(tx_env, transaction_id)
                assert row is not None
                assert row["description"] == "Original"
                assert row["amount_kyat"] == 12500
                assert row["revision"] == 1

    asyncio.run(scenario())


def test_update_cannot_modify_another_users_transaction(tx_env: TransactionEnv):
    groceries = category_id(tx_env, "expense", "Groceries")

    async def scenario() -> None:
        async with tx_env.client() as client:
            _, first_session = await register_verify_login(client, tx_env)
            _, second_session = await register_verify_login(client, tx_env)
            created = await create_transaction(
                client,
                first_session["access_token"],
                transaction_payload(groceries, description="Owner only"),
            )
            transaction_id = created.json()["data"]["id"]

            response = await client.patch(
                f"/api/v1/transactions/{transaction_id}",
                json={"description": "Hijacked"},
                headers=headers(
                    token=second_session["access_token"], if_match='"1"'
                ),
            )
            assert response.status_code == 404
            row = _db_transaction(tx_env, transaction_id)
            assert row is not None
            assert row["description"] == "Owner only"
            assert row["revision"] == 1

    asyncio.run(scenario())


def test_delete_requires_preconditions_and_replays_idempotently(tx_env: TransactionEnv):
    groceries = category_id(tx_env, "expense", "Groceries")
    key = new_key("delete")

    async def scenario() -> None:
        async with tx_env.client() as client:
            _, session = await register_verify_login(client, tx_env)
            token = session["access_token"]
            created = await create_transaction(
                client,
                token,
                transaction_payload(groceries, description="Delete me"),
            )
            transaction_id = created.json()["data"]["id"]

            missing_key = await client.delete(
                f"/api/v1/transactions/{transaction_id}",
                headers=headers(token=token, if_match='"1"'),
            )
            assert missing_key.status_code == 422

            missing_match = await client.delete(
                f"/api/v1/transactions/{transaction_id}",
                headers=headers(token=token, idem=key),
            )
            assert missing_match.status_code == 428
            assert _db_transaction(tx_env, transaction_id) is not None

            stale = await client.delete(
                f"/api/v1/transactions/{transaction_id}",
                headers=headers(token=token, idem=key, if_match='"9"'),
            )
            assert stale.status_code == 412
            assert _db_transaction(tx_env, transaction_id) is not None

            deleted = await client.delete(
                f"/api/v1/transactions/{transaction_id}",
                headers=headers(token=token, idem=key, if_match='"1"'),
            )
            assert deleted.status_code == 204, deleted.text
            assert deleted.content == b""
            assert _db_transaction(tx_env, transaction_id) is None

            replay = await client.delete(
                f"/api/v1/transactions/{transaction_id}",
                headers=headers(token=token, idem=key, if_match='"1"'),
            )
            assert replay.status_code == 204
            assert replay.content == b""

            reused = await client.delete(
                f"/api/v1/transactions/{transaction_id}",
                headers=headers(token=token, idem=key, if_match='"2"'),
            )
            assert reused.status_code == 409
            assert reused.json()["error"]["code"] == "idempotency_key_reused"

            different_key = await client.delete(
                f"/api/v1/transactions/{transaction_id}",
                headers=headers(
                    token=token, idem=new_key("delete-again"), if_match='"1"'
                ),
            )
            assert different_key.status_code == 404

            record = tx_env.one(
                "SELECT state, response_status FROM idempotency_records "
                "WHERE owner_user_id = :id AND http_method = 'DELETE'",
                {"id": session["user"]["id"]},
            )
            assert record is not None
            assert record["state"] == "completed"
            assert record["response_status"] == 204

    asyncio.run(scenario())


def test_delete_cannot_remove_another_users_transaction(tx_env: TransactionEnv):
    groceries = category_id(tx_env, "expense", "Groceries")

    async def scenario() -> None:
        async with tx_env.client() as client:
            _, first_session = await register_verify_login(client, tx_env)
            _, second_session = await register_verify_login(client, tx_env)
            created = await create_transaction(
                client,
                first_session["access_token"],
                transaction_payload(groceries, description="Keep me"),
            )
            transaction_id = created.json()["data"]["id"]

            response = await client.delete(
                f"/api/v1/transactions/{transaction_id}",
                headers=headers(
                    token=second_session["access_token"],
                    idem=new_key("other-delete"),
                    if_match='"1"',
                ),
            )
            assert response.status_code == 404
            assert _db_transaction(tx_env, transaction_id) is not None

    asyncio.run(scenario())


def test_transaction_categories_are_active_and_read_only(tx_env: TransactionEnv):
    async def scenario() -> None:
        async with tx_env.client() as client:
            _, session = await register_verify_login(client, tx_env)
            token = session["access_token"]

            all_categories = await client.get(
                "/api/v1/transaction-categories",
                headers=headers(token=token),
            )
            assert all_categories.status_code == 200
            data = all_categories.json()["data"]
            assert len(data) == 11
            assert {item["type"] for item in data} == {"income", "expense"}
            assert all(set(item) == {"id", "name", "type"} for item in data)

            income = await client.get(
                "/api/v1/transaction-categories?type=income",
                headers=headers(token=token),
            )
            assert income.status_code == 200
            assert {item["type"] for item in income.json()["data"]} == {"income"}
            assert len(income.json()["data"]) == 3

            invalid = await client.get(
                "/api/v1/transaction-categories?type=transfer",
                headers=headers(token=token),
            )
            assert invalid.status_code == 422

            unauth = await client.get("/api/v1/transaction-categories")
            assert unauth.status_code == 401

    asyncio.run(scenario())


def test_suspended_and_closed_users_are_blocked(tx_env: TransactionEnv):
    async def scenario() -> None:
        async with tx_env.client() as client:
            email, session = await register_verify_login(client, tx_env)
            token = session["access_token"]
            user_id = session["user"]["id"]

            tx_env.execute(
                "UPDATE users SET status = 'suspended', revision = revision + 1 "
                "WHERE id = :id",
                {"id": user_id},
            )
            suspended = await client.get(
                "/api/v1/transactions", headers=headers(token=token)
            )
            assert suspended.status_code == 403
            assert suspended.json()["error"]["code"] == "account_suspended"

            tx_env.execute(
                "UPDATE users SET status = 'closed', revision = revision + 1 "
                "WHERE id = :id",
                {"id": user_id},
            )
            closed = await client.get(
                "/api/v1/transactions", headers=headers(token=token)
            )
            assert closed.status_code == 403
            assert closed.json()["error"]["code"] == "account_closed"

            assert email.endswith("@example.test")

    asyncio.run(scenario())
