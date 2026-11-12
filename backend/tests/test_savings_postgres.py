"""PostgreSQL tests for savings goals, contributions, and ownership."""

from __future__ import annotations

import asyncio
from typing import Any
from uuid import UUID

from sqlalchemy.orm import Session

from app.services.transactions import calculate_account_balance
from tests.tx_support import (
    TransactionEnv,
    headers,
    new_key,
    register_verify_login,
    set_starting_balance,
)


def _balance(tx_env: TransactionEnv, user_id: str) -> int | None:
    with Session(tx_env.runtime_engine) as session:
        return calculate_account_balance(
            session, user_id=UUID(user_id), as_of_date=None
        ).amount_kyat


def goal_payload(
    *,
    name: str = "Emergency fund",
    target_kyat: int = 500_000,
    tone: str | None = "calm",
) -> dict[str, Any]:
    return {
        "name": name,
        "target_kyat": target_kyat,
        "tone": tone,
    }


def contribution_payload(
    *,
    amount_kyat: int = 25_000,
    entry_date: str = "2026-11-03",
    note: str | None = "Weekly deposit",
) -> dict[str, Any]:
    return {
        "amount_kyat": amount_kyat,
        "entry_date": entry_date,
        "note": note,
    }


async def create_goal(
    client,
    token: str,
    payload: dict[str, Any],
    *,
    key: str | None = None,
):
    return await client.post(
        "/api/v1/savings/goals",
        json=payload,
        headers=headers(token=token, idem=key or new_key("goal")),
    )


async def create_contribution(
    client,
    token: str,
    goal_id: str,
    payload: dict[str, Any],
    *,
    key: str | None = None,
):
    return await client.post(
        f"/api/v1/savings/goals/{goal_id}/entries/contributions",
        json=payload,
        headers=headers(token=token, idem=key or new_key("save")),
    )


async def create_withdrawal(
    client,
    token: str,
    goal_id: str,
    payload: dict[str, Any],
    *,
    key: str | None = None,
):
    return await client.post(
        f"/api/v1/savings/goals/{goal_id}/entries/withdrawals",
        json=payload,
        headers=headers(token=token, idem=key or new_key("withdraw")),
    )


def _db_goal(env: TransactionEnv, goal_id: str):
    return env.one(
        "SELECT id, user_id, name, target_kyat, tone, status, revision "
        "FROM savings_goals WHERE id = :id",
        {"id": goal_id},
    )


def test_create_list_and_view_goals_are_owner_scoped(tx_env: TransactionEnv):
    async def scenario() -> None:
        async with tx_env.client() as client:
            _, first = await register_verify_login(client, tx_env)
            _, second = await register_verify_login(client, tx_env)
            first_token = first["access_token"]
            second_token = second["access_token"]
            first_user_id = first["user"]["id"]

            created = await create_goal(
                client,
                first_token,
                goal_payload(
                    name="  Emergency fund  ",
                    target_kyat=500_000,
                    tone="  calm  ",
                ),
            )
            assert created.status_code == 201, created.text
            body = created.json()["data"]
            assert body["name"] == "Emergency fund"
            assert body["target_kyat"] == 500_000
            assert body["tone"] == "calm"
            assert body["status"] == "active"
            assert body["actual_balance_kyat"] == 0
            assert body["remaining_kyat"] == 500_000
            assert "user_id" not in body
            assert created.headers["etag"] == '"1"'
            goal_id = body["id"]

            row = _db_goal(tx_env, goal_id)
            assert row is not None
            assert str(row["user_id"]) == first_user_id
            assert row["name"] == "Emergency fund"
            assert row["target_kyat"] == 500_000
            assert row["tone"] == "calm"
            assert row["status"] == "active"
            assert row["revision"] == 1

            second_goal = await create_goal(
                client,
                first_token,
                goal_payload(name="New laptop", target_kyat=250_000),
            )
            assert second_goal.status_code == 201, second_goal.text

            listing = await client.get(
                "/api/v1/savings/goals",
                headers=headers(token=first_token),
            )
            assert listing.status_code == 200
            assert {item["name"] for item in listing.json()["data"]} == {
                "Emergency fund",
                "New laptop",
            }

            other_listing = await client.get(
                "/api/v1/savings/goals",
                headers=headers(token=second_token),
            )
            assert other_listing.status_code == 200
            assert other_listing.json()["data"] == []

            detail = await client.get(
                f"/api/v1/savings/goals/{goal_id}",
                headers=headers(token=first_token),
            )
            assert detail.status_code == 200
            assert detail.json()["data"]["id"] == goal_id
            assert detail.headers["etag"] == '"1"'

            hidden = await client.get(
                f"/api/v1/savings/goals/{goal_id}",
                headers=headers(token=second_token),
            )
            assert hidden.status_code == 404
            assert hidden.json()["error"]["code"] == "not_found"

            tx_env.execute(
                "UPDATE savings_goals "
                "SET status = 'archived', archived_at = now(), "
                "revision = revision + 1 "
                "WHERE id = :id",
                {"id": goal_id},
            )
            active = await client.get(
                "/api/v1/savings/goals",
                headers=headers(token=first_token),
            )
            assert [item["name"] for item in active.json()["data"]] == [
                "New laptop"
            ]
            archived = await client.get(
                "/api/v1/savings/goals?status=archived",
                headers=headers(token=first_token),
            )
            assert [item["name"] for item in archived.json()["data"]] == [
                "Emergency fund"
            ]
            every_goal = await client.get(
                "/api/v1/savings/goals?status=all",
                headers=headers(token=first_token),
            )
            assert len(every_goal.json()["data"]) == 2

    asyncio.run(scenario())


def test_goal_creation_validation_and_idempotency(tx_env: TransactionEnv):
    async def scenario() -> None:
        async with tx_env.client() as client:
            _, session = await register_verify_login(client, tx_env)
            token = session["access_token"]
            user_id = session["user"]["id"]
            valid = goal_payload()

            unauthenticated = await client.post(
                "/api/v1/savings/goals",
                json=valid,
            )
            assert unauthenticated.status_code == 401

            missing_key = await client.post(
                "/api/v1/savings/goals",
                json=valid,
                headers=headers(token=token),
            )
            assert missing_key.status_code == 422
            assert missing_key.json()["error"]["code"] == "validation_error"

            cases: list[dict[str, Any]] = [
                {key: value for key, value in valid.items() if key != "name"},
                goal_payload(name="   "),
                goal_payload(name="x" * 81),
                goal_payload(target_kyat=0),
                goal_payload(target_kyat=-1),
                goal_payload(target_kyat=1.5),
                goal_payload(target_kyat=1_000.0),
                goal_payload(target_kyat=9_007_199_254_740_992),
                goal_payload(tone="x" * 17),
                {**valid, "user_id": "00000000-0000-0000-0000-000000000000"},
                {**valid, "unknown": "value"},
            ]
            for payload in cases:
                response = await create_goal(client, token, payload)
                assert response.status_code == 422, (payload, response.text)

            # A savings target is a setting, not an individual financial entry,
            # so the one-billion-Kyat entry cap does not apply to it.
            large_target = await create_goal(
                client,
                token,
                goal_payload(name="House", target_kyat=1_000_000_001),
            )
            assert large_target.status_code == 201, large_target.text
            assert large_target.json()["data"]["target_kyat"] == 1_000_000_001

            key = new_key("goal")
            first = await create_goal(client, token, valid, key=key)
            assert first.status_code == 201, first.text
            replay = await create_goal(client, token, valid, key=key)
            assert replay.status_code == 201
            assert replay.json()["data"]["id"] == first.json()["data"]["id"]

            conflict = await create_goal(
                client,
                token,
                goal_payload(name="Different goal"),
                key=key,
            )
            assert conflict.status_code == 409
            assert conflict.json()["error"]["code"] == "idempotency_key_reused"

            count = tx_env.one(
                "SELECT count(*) AS count FROM savings_goals "
                "WHERE user_id = :id",
                {"id": user_id},
            )
            assert count is not None and count["count"] == 2

    asyncio.run(scenario())


def test_update_goal_uses_revision_preconditions_and_preserves_balance(
    tx_env: TransactionEnv,
):
    async def scenario() -> None:
        async with tx_env.client() as client:
            _, first = await register_verify_login(client, tx_env)
            _, second = await register_verify_login(client, tx_env)
            token = first["access_token"]
            other_token = second["access_token"]

            created = await create_goal(client, token, goal_payload())
            assert created.status_code == 201, created.text
            goal_id = created.json()["data"]["id"]

            missing_match = await client.patch(
                f"/api/v1/savings/goals/{goal_id}",
                json={"name": "Updated"},
                headers=headers(token=token),
            )
            assert missing_match.status_code == 428
            assert missing_match.json()["error"]["code"] == (
                "precondition_required"
            )

            stale = await client.patch(
                f"/api/v1/savings/goals/{goal_id}",
                json={"name": "Updated"},
                headers=headers(token=token, if_match='"99"'),
            )
            assert stale.status_code == 412
            assert stale.json()["error"]["code"] == "stale_revision"

            contribution = await create_contribution(
                client,
                token,
                goal_id,
                contribution_payload(amount_kyat=40_000),
            )
            assert contribution.status_code == 201, contribution.text

            current = await client.get(
                f"/api/v1/savings/goals/{goal_id}",
                headers=headers(token=token),
            )
            current_etag = current.headers["etag"]
            assert current.json()["data"]["actual_balance_kyat"] == 40_000
            assert current.json()["data"]["remaining_kyat"] == 460_000

            updated = await client.patch(
                f"/api/v1/savings/goals/{goal_id}",
                json={
                    "name": "  Updated fund  ",
                    "target_kyat": 300_000,
                    "tone": "focused",
                },
                headers=headers(token=token, if_match=current_etag),
            )
            assert updated.status_code == 200, updated.text
            body = updated.json()["data"]
            assert body["name"] == "Updated fund"
            assert body["target_kyat"] == 300_000
            assert body["tone"] == "focused"
            assert body["actual_balance_kyat"] == 40_000
            assert body["remaining_kyat"] == 260_000
            assert updated.headers["etag"] == '"3"'

            cleared = await client.patch(
                f"/api/v1/savings/goals/{goal_id}",
                json={"tone": None},
                headers=headers(token=token, if_match='"3"'),
            )
            assert cleared.status_code == 200, cleared.text
            assert cleared.json()["data"]["tone"] is None
            assert cleared.headers["etag"] == '"4"'

            hidden = await client.patch(
                f"/api/v1/savings/goals/{goal_id}",
                json={"name": "Stolen"},
                headers=headers(token=other_token, if_match='"1"'),
            )
            assert hidden.status_code == 404

            empty = await client.patch(
                f"/api/v1/savings/goals/{goal_id}",
                json={},
                headers=headers(token=token, if_match='"4"'),
            )
            assert empty.status_code == 422

            null_field = await client.patch(
                f"/api/v1/savings/goals/{goal_id}",
                json={"name": None},
                headers=headers(token=token, if_match='"4"'),
            )
            assert null_field.status_code == 422

            tx_env.execute(
                "UPDATE savings_goals "
                "SET status = 'archived', archived_at = now(), "
                "revision = revision + 1 "
                "WHERE id = :id",
                {"id": goal_id},
            )
            archived_update = await client.patch(
                f"/api/v1/savings/goals/{goal_id}",
                json={"name": "Archived edit"},
                headers=headers(token=token, if_match='"5"'),
            )
            assert archived_update.status_code == 409
            assert archived_update.json()["error"]["code"] == "goal_archived"

            archived_contribution = await create_contribution(
                client,
                token,
                goal_id,
                contribution_payload(),
            )
            assert archived_contribution.status_code == 409
            assert archived_contribution.json()["error"]["code"] == (
                "goal_archived"
            )

            row = _db_goal(tx_env, goal_id)
            assert row is not None
            assert row["name"] == "Updated fund"
            assert row["target_kyat"] == 300_000
            assert row["revision"] == 5

    asyncio.run(scenario())


def test_update_goal_accepts_weak_if_match_revision_tokens(
    tx_env: TransactionEnv,
):
    async def scenario() -> None:
        async with tx_env.client() as client:
            _, session = await register_verify_login(client, tx_env)
            token = session["access_token"]
            created = await create_goal(client, token, goal_payload())
            assert created.status_code == 201, created.text
            goal_id = created.json()["data"]["id"]
            weak_etag = f'W/{created.headers["etag"]}'

            missing = await client.patch(
                f"/api/v1/savings/goals/{goal_id}",
                json={"name": "Missing"},
                headers=headers(token=token),
            )
            assert missing.status_code == 428
            assert missing.json()["error"]["code"] == "precondition_required"

            stale = await client.patch(
                f"/api/v1/savings/goals/{goal_id}",
                json={"name": "Stale"},
                headers=headers(token=token, if_match='W/"99"'),
            )
            assert stale.status_code == 412
            assert stale.json()["error"]["code"] == "stale_revision"

            for malformed in (
                'W/"not-a-revision"',
                "W/1",
                'w/"1"',
                'W/"1", "2"',
                'W/"',
            ):
                response = await client.patch(
                    f"/api/v1/savings/goals/{goal_id}",
                    json={"name": "Malformed"},
                    headers=headers(token=token, if_match=malformed),
                )
                assert response.status_code == 412, (malformed, response.text)

            updated = await client.patch(
                f"/api/v1/savings/goals/{goal_id}",
                json={"name": "Weakly updated", "target_kyat": 300_000},
                headers=headers(token=token, if_match=weak_etag),
            )
            assert updated.status_code == 200, updated.text
            assert updated.json()["data"]["name"] == "Weakly updated"
            assert updated.json()["data"]["target_kyat"] == 300_000
            assert updated.headers["etag"] == '"2"'

            row = _db_goal(tx_env, goal_id)
            assert row is not None
            assert row["name"] == "Weakly updated"
            assert row["target_kyat"] == 300_000
            assert row["revision"] == 2

            stale_weak = await client.patch(
                f"/api/v1/savings/goals/{goal_id}",
                json={"name": "Stale weak"},
                headers=headers(token=token, if_match=weak_etag),
            )
            assert stale_weak.status_code == 412
            assert stale_weak.json()["error"]["code"] == "stale_revision"

    asyncio.run(scenario())


def test_contributions_update_derived_balance_and_validate_amounts(
    tx_env: TransactionEnv,
):
    async def scenario() -> None:
        async with tx_env.client() as client:
            _, first = await register_verify_login(client, tx_env)
            _, second = await register_verify_login(client, tx_env)
            token = first["access_token"]
            other_token = second["access_token"]
            user_id = first["user"]["id"]

            created = await create_goal(
                client,
                token,
                goal_payload(target_kyat=100_000),
            )
            assert created.status_code == 201, created.text
            goal_id = created.json()["data"]["id"]

            missing_key = await client.post(
                f"/api/v1/savings/goals/{goal_id}/entries/contributions",
                json=contribution_payload(),
                headers=headers(token=token),
            )
            assert missing_key.status_code == 422

            cases = [
                contribution_payload(amount_kyat=0),
                contribution_payload(amount_kyat=-1),
                contribution_payload(amount_kyat=1.5),
                contribution_payload(amount_kyat=1_000.0),
                contribution_payload(amount_kyat=1_000_000_001),
                contribution_payload(entry_date="2026-02-30"),
                contribution_payload(entry_date="2026-11-03T00:00:00"),
                contribution_payload(note="x" * 101),
                {**contribution_payload(), "user_id": user_id},
                {**contribution_payload(), "unknown": "value"},
            ]
            for payload in cases:
                response = await create_contribution(
                    client,
                    token,
                    goal_id,
                    payload,
                )
                assert response.status_code == 422, (payload, response.text)

            first_entry = await create_contribution(
                client,
                token,
                goal_id,
                contribution_payload(
                    amount_kyat=30_000,
                    entry_date="2026-11-01",
                    note="  First deposit  ",
                ),
            )
            assert first_entry.status_code == 201, first_entry.text
            entry = first_entry.json()["data"]
            assert entry["entry_type"] == "contribution"
            assert entry["amount_kyat"] == 30_000
            assert entry["entry_date"] == "2026-11-01"
            assert entry["note"] == "First deposit"
            assert entry["plan_day_id"] is None
            assert entry["correction_of_entry_id"] is None
            assert entry["correction_reason"] is None
            first_entry_id = entry["id"]

            second_entry = await create_contribution(
                client,
                token,
                goal_id,
                contribution_payload(
                    amount_kyat=20_000,
                    entry_date="2026-11-03",
                    note=None,
                ),
            )
            assert second_entry.status_code == 201, second_entry.text

            detail = await client.get(
                f"/api/v1/savings/goals/{goal_id}",
                headers=headers(token=token),
            )
            assert detail.status_code == 200
            assert detail.json()["data"]["actual_balance_kyat"] == 50_000
            assert detail.json()["data"]["remaining_kyat"] == 50_000
            assert detail.headers["etag"] == '"3"'

            history = await client.get(
                f"/api/v1/savings/goals/{goal_id}/entries",
                headers=headers(token=token),
            )
            assert history.status_code == 200
            assert [item["id"] for item in history.json()["data"]] == [
                second_entry.json()["data"]["id"],
                first_entry_id,
            ]

            overfunded = await create_contribution(
                client,
                token,
                goal_id,
                contribution_payload(
                    amount_kyat=60_000,
                    entry_date="2026-11-04",
                ),
            )
            assert overfunded.status_code == 201, overfunded.text
            funded = await client.get(
                f"/api/v1/savings/goals/{goal_id}",
                headers=headers(token=token),
            )
            assert funded.json()["data"]["actual_balance_kyat"] == 110_000
            assert funded.json()["data"]["remaining_kyat"] == 0

            key = new_key("save")
            replay = await create_contribution(
                client,
                token,
                goal_id,
                contribution_payload(
                    amount_kyat=5_000,
                    entry_date="2026-11-05",
                ),
                key=key,
            )
            assert replay.status_code == 201, replay.text
            replay_again = await create_contribution(
                client,
                token,
                goal_id,
                contribution_payload(
                    amount_kyat=5_000,
                    entry_date="2026-11-05",
                ),
                key=key,
            )
            assert replay_again.status_code == 201
            assert replay_again.json()["data"]["id"] == (
                replay.json()["data"]["id"]
            )
            conflict = await create_contribution(
                client,
                token,
                goal_id,
                contribution_payload(
                    amount_kyat=6_000,
                    entry_date="2026-11-05",
                ),
                key=key,
            )
            assert conflict.status_code == 409
            assert conflict.json()["error"]["code"] == (
                "idempotency_key_reused"
            )

            count = tx_env.one(
                "SELECT count(*) AS count FROM savings_entries "
                "WHERE user_id = :id AND goal_id = :goal_id",
                {"id": user_id, "goal_id": goal_id},
            )
            assert count is not None and count["count"] == 4

            hidden_history = await client.get(
                f"/api/v1/savings/goals/{goal_id}/entries",
                headers=headers(token=other_token),
            )
            assert hidden_history.status_code == 404

            hidden_contribution = await create_contribution(
                client,
                other_token,
                goal_id,
                contribution_payload(),
            )
            assert hidden_contribution.status_code == 404

            missing_goal = await create_contribution(
                client,
                token,
                "00000000-0000-0000-0000-000000000000",
                contribution_payload(),
            )
            assert missing_goal.status_code == 404

    asyncio.run(scenario())


def test_contribution_history_is_append_only_and_persisted(
    tx_env: TransactionEnv,
):
    async def scenario() -> None:
        async with tx_env.client() as client:
            _, session = await register_verify_login(client, tx_env)
            token = session["access_token"]
            user_id = session["user"]["id"]

            created = await create_goal(client, token, goal_payload())
            assert created.status_code == 201, created.text
            goal_id = created.json()["data"]["id"]

            recorded = await create_contribution(
                client,
                token,
                goal_id,
                contribution_payload(amount_kyat=12_500),
            )
            assert recorded.status_code == 201, recorded.text
            entry_id = recorded.json()["data"]["id"]

            patch_entry = await client.patch(
                f"/api/v1/savings/goals/{goal_id}/entries/{entry_id}",
                json={"amount_kyat": 1},
                headers=headers(token=token, if_match='"1"'),
            )
            assert patch_entry.status_code == 404

            delete_entry = await client.delete(
                f"/api/v1/savings/goals/{goal_id}/entries/{entry_id}",
                headers=headers(token=token, if_match='"1"'),
            )
            assert delete_entry.status_code == 404

            history = await client.get(
                f"/api/v1/savings/goals/{goal_id}/entries",
                headers=headers(token=token),
            )
            assert history.status_code == 200
            assert [item["id"] for item in history.json()["data"]] == [
                entry_id
            ]
            assert history.json()["data"][0]["amount_kyat"] == 12_500

            row = tx_env.one(
                "SELECT id, user_id, goal_id, entry_type, amount_kyat, "
                "entry_date, note FROM savings_entries WHERE id = :id",
                {"id": entry_id},
            )
            assert row is not None
            assert str(row["user_id"]) == user_id
            assert str(row["goal_id"]) == goal_id
            assert row["entry_type"] == "contribution"
            assert row["amount_kyat"] == 12_500
            assert row["entry_date"].isoformat() == "2026-11-03"
            assert row["note"] == "Weekly deposit"

    asyncio.run(scenario())


def test_withdrawals_update_balance_history_and_persist(tx_env: TransactionEnv):
    async def scenario() -> None:
        async with tx_env.client() as client:
            _, session = await register_verify_login(client, tx_env)
            token = session["access_token"]
            user_id = session["user"]["id"]

            created = await create_goal(
                client,
                token,
                goal_payload(target_kyat=100_000),
            )
            assert created.status_code == 201, created.text
            goal_id = created.json()["data"]["id"]

            contribution = await create_contribution(
                client,
                token,
                goal_id,
                contribution_payload(amount_kyat=50_000),
            )
            assert contribution.status_code == 201, contribution.text

            withdrawal = await create_withdrawal(
                client,
                token,
                goal_id,
                contribution_payload(
                    amount_kyat=20_000,
                    entry_date="2026-11-04",
                    note="  Cash out  ",
                ),
            )
            assert withdrawal.status_code == 201, withdrawal.text
            entry = withdrawal.json()["data"]
            assert entry["entry_type"] == "withdrawal"
            assert entry["amount_kyat"] == 20_000
            assert entry["entry_date"] == "2026-11-04"
            assert entry["note"] == "Cash out"
            assert entry["plan_day_id"] is None

            detail = await client.get(
                f"/api/v1/savings/goals/{goal_id}",
                headers=headers(token=token),
            )
            assert detail.status_code == 200, detail.text
            assert detail.json()["data"]["actual_balance_kyat"] == 30_000
            assert detail.json()["data"]["remaining_kyat"] == 70_000
            assert detail.headers["etag"] == '"3"'

            history = await client.get(
                f"/api/v1/savings/goals/{goal_id}/entries",
                headers=headers(token=token),
            )
            assert history.status_code == 200
            assert [item["entry_type"] for item in history.json()["data"]] == [
                "withdrawal",
                "contribution",
            ]
            assert [item["amount_kyat"] for item in history.json()["data"]] == [
                20_000,
                50_000,
            ]

            row = tx_env.one(
                "SELECT user_id, goal_id, entry_type, amount_kyat, entry_date, "
                "note FROM savings_entries WHERE id = :id",
                {"id": entry["id"]},
            )
            assert row is not None
            assert str(row["user_id"]) == user_id
            assert str(row["goal_id"]) == goal_id
            assert row["entry_type"] == "withdrawal"
            assert row["amount_kyat"] == 20_000
            assert row["entry_date"].isoformat() == "2026-11-04"
            assert row["note"] == "Cash out"

            # Withdrawing the exact remaining balance is allowed and leaves a
            # zero balance; a later withdrawal cannot make it negative.
            remaining = await create_withdrawal(
                client,
                token,
                goal_id,
                contribution_payload(
                    amount_kyat=30_000,
                    entry_date="2026-11-05",
                    note=None,
                ),
            )
            assert remaining.status_code == 201, remaining.text

            zero_balance = await client.get(
                f"/api/v1/savings/goals/{goal_id}",
                headers=headers(token=token),
            )
            assert zero_balance.json()["data"]["actual_balance_kyat"] == 0

            overdraw = await create_withdrawal(
                client,
                token,
                goal_id,
                contribution_payload(
                    amount_kyat=1,
                    entry_date="2026-11-06",
                ),
            )
            assert overdraw.status_code == 409
            assert overdraw.json()["error"]["code"] == (
                "withdrawal_exceeds_balance"
            )

            after = await client.get(
                f"/api/v1/savings/goals/{goal_id}",
                headers=headers(token=token),
            )
            assert after.json()["data"]["actual_balance_kyat"] == 0

            persisted = tx_env.one(
                "SELECT COALESCE(SUM(CASE WHEN entry_type = 'contribution' "
                "THEN amount_kyat ELSE -amount_kyat END), 0) AS total, "
                "count(*) AS count FROM savings_entries "
                "WHERE user_id = :id AND goal_id = :goal_id",
                {"id": user_id, "goal_id": goal_id},
            )
            assert persisted is not None
            assert persisted["total"] == 0
            assert persisted["count"] == 3

    asyncio.run(scenario())


def test_withdrawal_validation_ownership_and_idempotency(
    tx_env: TransactionEnv,
):
    async def scenario() -> None:
        async with tx_env.client() as client:
            _, first = await register_verify_login(client, tx_env)
            _, second = await register_verify_login(client, tx_env)
            token = first["access_token"]
            other_token = second["access_token"]
            user_id = first["user"]["id"]

            created = await create_goal(client, token, goal_payload())
            assert created.status_code == 201, created.text
            goal_id = created.json()["data"]["id"]

            funded = await create_contribution(
                client,
                token,
                goal_id,
                contribution_payload(amount_kyat=10_000),
            )
            assert funded.status_code == 201, funded.text

            unauthenticated = await client.post(
                f"/api/v1/savings/goals/{goal_id}/entries/withdrawals",
                json=contribution_payload(amount_kyat=1_000),
                headers=headers(idem=new_key("withdraw")),
            )
            assert unauthenticated.status_code == 401

            missing_key = await client.post(
                f"/api/v1/savings/goals/{goal_id}/entries/withdrawals",
                json=contribution_payload(amount_kyat=1_000),
                headers=headers(token=token),
            )
            assert missing_key.status_code == 422

            cases = [
                contribution_payload(amount_kyat=0),
                contribution_payload(amount_kyat=-1),
                contribution_payload(amount_kyat=1.5),
                contribution_payload(amount_kyat=1_000.0),
                contribution_payload(amount_kyat=1_000_000_001),
                contribution_payload(entry_date="2026-02-30"),
                contribution_payload(entry_date="2026-11-03T00:00:00"),
                contribution_payload(note="x" * 101),
                {**contribution_payload(), "user_id": user_id},
                {**contribution_payload(), "unknown": "value"},
            ]
            for payload in cases:
                response = await create_withdrawal(
                    client,
                    token,
                    goal_id,
                    payload,
                )
                assert response.status_code == 422, (payload, response.text)

            too_large = await create_withdrawal(
                client,
                token,
                goal_id,
                contribution_payload(amount_kyat=10_001),
            )
            assert too_large.status_code == 409
            assert too_large.json()["error"]["code"] == (
                "withdrawal_exceeds_balance"
            )

            hidden = await create_withdrawal(
                client,
                other_token,
                goal_id,
                contribution_payload(amount_kyat=1_000),
            )
            assert hidden.status_code == 404

            missing_goal = await create_withdrawal(
                client,
                token,
                "00000000-0000-0000-0000-000000000000",
                contribution_payload(amount_kyat=1_000),
            )
            assert missing_goal.status_code == 404

            key = new_key("withdraw")
            payload = contribution_payload(
                amount_kyat=2_000,
                entry_date="2026-11-04",
            )
            recorded = await create_withdrawal(
                client,
                token,
                goal_id,
                payload,
                key=key,
            )
            assert recorded.status_code == 201, recorded.text
            replay = await create_withdrawal(
                client,
                token,
                goal_id,
                payload,
                key=key,
            )
            assert replay.status_code == 201
            assert replay.json()["data"]["id"] == recorded.json()["data"]["id"]

            conflict = await create_withdrawal(
                client,
                token,
                goal_id,
                contribution_payload(
                    amount_kyat=2_001,
                    entry_date="2026-11-04",
                ),
                key=key,
            )
            assert conflict.status_code == 409
            assert conflict.json()["error"]["code"] == (
                "idempotency_key_reused"
            )

            tx_env.execute(
                "UPDATE savings_goals "
                "SET status = 'archived', archived_at = now(), "
                "revision = revision + 1 "
                "WHERE id = :id",
                {"id": goal_id},
            )
            archived = await create_withdrawal(
                client,
                token,
                goal_id,
                contribution_payload(amount_kyat=1_000),
            )
            assert archived.status_code == 409
            assert archived.json()["error"]["code"] == "goal_archived"

            persisted = tx_env.one(
                "SELECT count(*) AS count FROM savings_entries "
                "WHERE user_id = :id AND goal_id = :goal_id",
                {"id": user_id, "goal_id": goal_id},
            )
            assert persisted is not None and persisted["count"] == 2

    asyncio.run(scenario())


def test_savings_allocation_deducts_once_and_tracks_targets_and_withdrawals(
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
                starting_balance_kyat=1_000_000,
                effective_date="2026-01-01",
            )
            assert _balance(tx_env, user_id) == 1_000_000

            created = await create_goal(
                client,
                token,
                goal_payload(target_kyat=300_000),
            )
            assert created.status_code == 201, created.text
            goal_id = created.json()["data"]["id"]
            # The allocation is reserved immediately.
            assert _balance(tx_env, user_id) == 700_000

            first = await create_contribution(
                client,
                token,
                goal_id,
                contribution_payload(amount_kyat=100_000),
            )
            assert first.status_code == 201, first.text
            # A contribution inside the allocation moves already-reserved cash
            # and must not deduct it a second time.
            assert _balance(tx_env, user_id) == 700_000

            excess = await create_contribution(
                client,
                token,
                goal_id,
                contribution_payload(amount_kyat=250_000),
            )
            assert excess.status_code == 201, excess.text
            # Contributions now total 350,000, so only the 50,000 above the
            # 300,000 target is an additional reservation.
            assert _balance(tx_env, user_id) == 650_000

            current = await client.get(
                f"/api/v1/savings/goals/{goal_id}",
                headers=headers(token=token),
            )
            increased = await client.patch(
                f"/api/v1/savings/goals/{goal_id}",
                json={"target_kyat": 500_000},
                headers=headers(
                    token=token,
                    if_match=current.headers["etag"],
                ),
            )
            assert increased.status_code == 200, increased.text
            # Increasing the target by 200,000 reserves exactly the difference.
            assert _balance(tx_env, user_id) == 500_000
            assert increased.headers["etag"] == '"4"'

            decreased = await client.patch(
                f"/api/v1/savings/goals/{goal_id}",
                json={"target_kyat": 400_000},
                headers=headers(token=token, if_match='"4"'),
            )
            assert decreased.status_code == 200, decreased.text
            # Decreasing the target by 100,000 releases exactly the difference.
            assert _balance(tx_env, user_id) == 600_000

            withdrawn = await create_withdrawal(
                client,
                token,
                goal_id,
                contribution_payload(amount_kyat=50_000),
            )
            assert withdrawn.status_code == 201, withdrawn.text
            # Withdrawing returns cash to the available balance.
            assert _balance(tx_env, user_id) == 650_000

            current = await client.get(
                f"/api/v1/savings/goals/{goal_id}",
                headers=headers(token=token),
            )
            archived = await client.post(
                f"/api/v1/savings/goals/{goal_id}/archive",
                json={"confirm": True},
                headers=headers(
                    token=token,
                    idem=new_key("archive"),
                    if_match=current.headers["etag"],
                ),
            )
            assert archived.status_code == 200, archived.text
            assert archived.json()["data"]["status"] == "archived"
            # Active target 400,000 with 350,000 contributed and 50,000
            # withdrawn reserved 350,000. Archiving releases the unused 50,000
            # but keeps the 300,000 actual balance reserved.
            assert _balance(tx_env, user_id) == 700_000

            restored = await client.post(
                f"/api/v1/savings/goals/{goal_id}/restore",
                json={"confirm": True},
                headers=headers(
                    token=token,
                    idem=new_key("restore"),
                    if_match=archived.headers["etag"],
                ),
            )
            assert restored.status_code == 200, restored.text
            assert restored.json()["data"]["status"] == "active"
            assert _balance(tx_env, user_id) == 650_000

    asyncio.run(scenario())


def test_archive_and_restore_require_confirmation_revision_and_idempotency(
    tx_env: TransactionEnv,
):
    async def scenario() -> None:
        async with tx_env.client() as client:
            _, first = await register_verify_login(client, tx_env)
            _, second = await register_verify_login(client, tx_env)
            token = first["access_token"]
            other_token = second["access_token"]

            created = await create_goal(client, token, goal_payload())
            assert created.status_code == 201, created.text
            goal_id = created.json()["data"]["id"]

            unconfirmed = await client.post(
                f"/api/v1/savings/goals/{goal_id}/archive",
                json={"confirm": False},
                headers=headers(
                    token=token,
                    idem=new_key("archive"),
                    if_match='"1"',
                ),
            )
            assert unconfirmed.status_code == 422
            assert unconfirmed.json()["error"]["code"] == (
                "confirmation_required"
            )

            missing_match = await client.post(
                f"/api/v1/savings/goals/{goal_id}/archive",
                json={"confirm": True},
                headers=headers(token=token, idem=new_key("archive")),
            )
            assert missing_match.status_code == 428
            assert missing_match.json()["error"]["code"] == (
                "precondition_required"
            )

            stale = await client.post(
                f"/api/v1/savings/goals/{goal_id}/archive",
                json={"confirm": True},
                headers=headers(
                    token=token,
                    idem=new_key("archive"),
                    if_match='"99"',
                ),
            )
            assert stale.status_code == 412
            assert stale.json()["error"]["code"] == "stale_revision"

            hidden = await client.post(
                f"/api/v1/savings/goals/{goal_id}/archive",
                json={"confirm": True},
                headers=headers(
                    token=other_token,
                    idem=new_key("archive"),
                    if_match='"1"',
                ),
            )
            assert hidden.status_code == 404

            key = new_key("archive")
            archived = await client.post(
                f"/api/v1/savings/goals/{goal_id}/archive",
                json={"confirm": True},
                headers=headers(
                    token=token,
                    idem=key,
                    if_match='"1"',
                ),
            )
            assert archived.status_code == 200, archived.text
            assert archived.json()["data"]["status"] == "archived"
            assert archived.headers["etag"] == '"2"'

            replay = await client.post(
                f"/api/v1/savings/goals/{goal_id}/archive",
                json={"confirm": True},
                headers=headers(token=token, idem=key, if_match='"1"'),
            )
            assert replay.status_code == 200
            assert replay.headers["etag"] == '"2"'
            assert replay.json() == archived.json()

            no_op = await client.post(
                f"/api/v1/savings/goals/{goal_id}/archive",
                json={"confirm": True},
                headers=headers(
                    token=token,
                    idem=new_key("archive"),
                    if_match='"2"',
                ),
            )
            assert no_op.status_code == 200
            assert no_op.headers["etag"] == '"2"'

            active = await client.get(
                "/api/v1/savings/goals",
                headers=headers(token=token),
            )
            assert active.json()["data"] == []
            archived_list = await client.get(
                "/api/v1/savings/goals?status=archived",
                headers=headers(token=token),
            )
            assert [item["id"] for item in archived_list.json()["data"]] == [
                goal_id
            ]

            restored = await client.post(
                f"/api/v1/savings/goals/{goal_id}/restore",
                json={"confirm": True},
                headers=headers(
                    token=token,
                    idem=new_key("restore"),
                    if_match='W/"2"',
                ),
            )
            assert restored.status_code == 200, restored.text
            assert restored.json()["data"]["status"] == "active"
            assert restored.headers["etag"] == '"3"'

            restored_no_op = await client.post(
                f"/api/v1/savings/goals/{goal_id}/restore",
                json={"confirm": True},
                headers=headers(
                    token=token,
                    idem=new_key("restore"),
                    if_match='"3"',
                ),
            )
            assert restored_no_op.status_code == 200
            assert restored_no_op.headers["etag"] == '"3"'

            row = tx_env.one(
                "SELECT status, archived_at FROM savings_goals "
                "WHERE id = :id",
                {"id": goal_id},
            )
            assert row is not None
            assert row["status"] == "active"
            assert row["archived_at"] is None

    asyncio.run(scenario())
