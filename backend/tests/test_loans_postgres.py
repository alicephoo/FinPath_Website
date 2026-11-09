"""PostgreSQL tests for loan CRUD, repayments, and ownership."""

from __future__ import annotations

import asyncio
from typing import Any
from uuid import UUID, uuid4

from tests.tx_support import (
    TransactionEnv,
    headers,
    new_key,
    register_verify_login,
)


def loan_payload(
    *,
    name: str = "Car loan",
    principal_kyat: int = 100_000,
    start_date: str = "2026-01-15",
) -> dict[str, Any]:
    return {
        "name": name,
        "principal_kyat": principal_kyat,
        "start_date": start_date,
    }


def repayment_payload(
    *,
    amount_kyat: int = 25_000,
    repayment_date: str = "2026-02-01",
    note: str | None = "Cash payment",
) -> dict[str, Any]:
    return {
        "amount_kyat": amount_kyat,
        "repayment_date": repayment_date,
        "note": note,
    }


async def create_loan(
    client,
    token: str,
    payload: dict[str, Any],
    *,
    key: str | None = None,
):
    return await client.post(
        "/api/v1/loans",
        json=payload,
        headers=headers(token=token, idem=key or new_key("loan")),
    )


async def record_repayment(
    client,
    token: str,
    loan_id: str,
    payload: dict[str, Any],
    *,
    key: str | None = None,
):
    return await client.post(
        f"/api/v1/loans/{loan_id}/repayments",
        json=payload,
        headers=headers(token=token, idem=key or new_key("repay")),
    )


def _db_loan(env: TransactionEnv, loan_id: str):
    return env.one(
        "SELECT id, user_id, name, principal_kyat, start_date, revision "
        "FROM loans WHERE id = :id",
        {"id": loan_id},
    )


def _db_repayment(env: TransactionEnv, repayment_id: str):
    return env.one(
        "SELECT id, user_id, loan_id, amount_kyat, repayment_date, note, "
        "revision FROM loan_repayments WHERE id = :id",
        {"id": repayment_id},
    )


def test_create_list_and_view_loans_are_owner_scoped(tx_env: TransactionEnv):
    async def scenario() -> None:
        async with tx_env.client() as client:
            _, first = await register_verify_login(client, tx_env)
            _, second = await register_verify_login(client, tx_env)
            first_token = first["access_token"]
            second_token = second["access_token"]
            first_user_id = first["user"]["id"]

            created = await create_loan(
                client,
                first_token,
                loan_payload(name="  Home loan  ", principal_kyat=250_000),
            )
            assert created.status_code == 201, created.text
            body = created.json()["data"]
            assert body["name"] == "Home loan"
            assert body["principal_kyat"] == 250_000
            assert body["start_date"] == "2026-01-15"
            assert body["total_repaid_kyat"] == 0
            assert body["outstanding_principal_kyat"] == 250_000
            assert "user_id" not in body
            assert created.headers["etag"] == '"1"'
            loan_id = body["id"]
            assert UUID(loan_id)

            row = _db_loan(tx_env, loan_id)
            assert row is not None
            assert str(row["user_id"]) == first_user_id
            assert row["principal_kyat"] == 250_000
            assert row["revision"] == 1

            older = await create_loan(
                client,
                first_token,
                loan_payload(
                    name="Education loan",
                    principal_kyat=50_000,
                    start_date="2025-06-01",
                ),
            )
            assert older.status_code == 201, older.text

            listing = await client.get(
                "/api/v1/loans",
                headers=headers(token=first_token),
            )
            assert listing.status_code == 200
            assert [item["name"] for item in listing.json()["data"]] == [
                "Home loan",
                "Education loan",
            ]

            other_listing = await client.get(
                "/api/v1/loans",
                headers=headers(token=second_token),
            )
            assert other_listing.status_code == 200
            assert other_listing.json()["data"] == []

            detail = await client.get(
                f"/api/v1/loans/{loan_id}",
                headers=headers(token=first_token),
            )
            assert detail.status_code == 200
            assert detail.json()["data"]["id"] == loan_id
            assert detail.headers["etag"] == '"1"'

            hidden = await client.get(
                f"/api/v1/loans/{loan_id}",
                headers=headers(token=second_token),
            )
            assert hidden.status_code == 404
            assert hidden.json()["error"]["code"] == "not_found"

    asyncio.run(scenario())


def test_loan_creation_validation_and_idempotency(tx_env: TransactionEnv):
    async def scenario() -> None:
        async with tx_env.client() as client:
            _, session = await register_verify_login(client, tx_env)
            token = session["access_token"]
            user_id = session["user"]["id"]
            valid = loan_payload()

            unauthenticated = await client.post("/api/v1/loans", json=valid)
            assert unauthenticated.status_code == 401

            missing_key = await client.post(
                "/api/v1/loans",
                json=valid,
                headers=headers(token=token),
            )
            assert missing_key.status_code == 422
            assert missing_key.json()["error"]["code"] == "validation_error"

            cases: list[dict[str, Any]] = [
                {key: value for key, value in valid.items() if key != "name"},
                loan_payload(name="   "),
                loan_payload(name="x" * 71),
                loan_payload(principal_kyat=0),
                loan_payload(principal_kyat=-1),
                loan_payload(principal_kyat=1.5),
                loan_payload(principal_kyat=1_000.0),
                loan_payload(principal_kyat=1_000_000_001),
                loan_payload(start_date="2026-02-30"),
                loan_payload(start_date="2026-01-15T00:00:00"),
                {**valid, "user_id": str(uuid4())},
                {**valid, "unknown": "value"},
            ]
            for payload in cases:
                response = await create_loan(client, token, payload)
                assert response.status_code == 422, (payload, response.text)

            key = new_key("loan")
            first = await create_loan(client, token, valid, key=key)
            assert first.status_code == 201, first.text
            replay = await create_loan(client, token, valid, key=key)
            assert replay.status_code == 201
            assert replay.json()["data"]["id"] == first.json()["data"]["id"]

            conflict = await create_loan(
                client,
                token,
                loan_payload(name="Different loan"),
                key=key,
            )
            assert conflict.status_code == 409
            assert conflict.json()["error"]["code"] == "idempotency_key_reused"

            count = tx_env.one(
                "SELECT count(*) AS count FROM loans WHERE user_id = :id",
                {"id": user_id},
            )
            assert count is not None and count["count"] == 1

    asyncio.run(scenario())


def test_update_loan_uses_revision_preconditions_and_preserves_repayments(
    tx_env: TransactionEnv,
):
    async def scenario() -> None:
        async with tx_env.client() as client:
            _, first = await register_verify_login(client, tx_env)
            _, second = await register_verify_login(client, tx_env)
            token = first["access_token"]
            other_token = second["access_token"]

            created = await create_loan(client, token, loan_payload())
            assert created.status_code == 201, created.text
            loan_id = created.json()["data"]["id"]

            missing_match = await client.patch(
                f"/api/v1/loans/{loan_id}",
                json={"name": "Updated"},
                headers=headers(token=token),
            )
            assert missing_match.status_code == 428
            assert missing_match.json()["error"]["code"] == (
                "precondition_required"
            )

            stale = await client.patch(
                f"/api/v1/loans/{loan_id}",
                json={"name": "Updated"},
                headers=headers(token=token, if_match='"99"'),
            )
            assert stale.status_code == 412
            assert stale.json()["error"]["code"] == "stale_revision"

            updated = await client.patch(
                f"/api/v1/loans/{loan_id}",
                json={
                    "name": "  Updated loan  ",
                    "principal_kyat": 120_000,
                    "start_date": "2026-01-20",
                },
                headers=headers(token=token, if_match='"1"'),
            )
            assert updated.status_code == 200, updated.text
            body = updated.json()["data"]
            assert body["name"] == "Updated loan"
            assert body["principal_kyat"] == 120_000
            assert body["start_date"] == "2026-01-20"
            assert updated.headers["etag"] == '"2"'

            repayment = await record_repayment(
                client,
                token,
                loan_id,
                repayment_payload(amount_kyat=40_000),
            )
            assert repayment.status_code == 201, repayment.text

            current = await client.get(
                f"/api/v1/loans/{loan_id}",
                headers=headers(token=token),
            )
            assert current.json()["data"]["total_repaid_kyat"] == 40_000
            current_etag = current.headers["etag"]

            below = await client.patch(
                f"/api/v1/loans/{loan_id}",
                json={"principal_kyat": 30_000},
                headers=headers(token=token, if_match=current_etag),
            )
            assert below.status_code == 422
            assert any(
                error["code"] == "principal_below_repayments"
                for error in below.json()["error"]["field_errors"]
            )
            unchanged = await client.get(
                f"/api/v1/loans/{loan_id}",
                headers=headers(token=token),
            )
            assert unchanged.json()["data"]["principal_kyat"] == 120_000
            assert unchanged.json()["data"]["total_repaid_kyat"] == 40_000

            hidden = await client.patch(
                f"/api/v1/loans/{loan_id}",
                json={"name": "Stolen"},
                headers=headers(token=other_token, if_match='"1"'),
            )
            assert hidden.status_code == 404

            empty = await client.patch(
                f"/api/v1/loans/{loan_id}",
                json={},
                headers=headers(token=token, if_match=current_etag),
            )
            assert empty.status_code == 422

            null_field = await client.patch(
                f"/api/v1/loans/{loan_id}",
                json={"name": None},
                headers=headers(token=token, if_match=current_etag),
            )
            assert null_field.status_code == 422

            row = _db_loan(tx_env, loan_id)
            assert row is not None
            assert row["name"] == "Updated loan"
            assert row["principal_kyat"] == 120_000
            assert row["revision"] == 3

    asyncio.run(scenario())


def test_record_repayment_updates_outstanding_and_validates_amounts(
    tx_env: TransactionEnv,
):
    async def scenario() -> None:
        async with tx_env.client() as client:
            _, first = await register_verify_login(client, tx_env)
            _, second = await register_verify_login(client, tx_env)
            token = first["access_token"]
            other_token = second["access_token"]

            created = await create_loan(
                client,
                token,
                loan_payload(principal_kyat=100_000),
            )
            assert created.status_code == 201, created.text
            loan_id = created.json()["data"]["id"]

            missing_key = await client.post(
                f"/api/v1/loans/{loan_id}/repayments",
                json=repayment_payload(),
                headers=headers(token=token),
            )
            assert missing_key.status_code == 422

            partial = await record_repayment(
                client,
                token,
                loan_id,
                repayment_payload(
                    amount_kyat=25_000,
                    note="  Cash payment  ",
                ),
            )
            assert partial.status_code == 201, partial.text
            repayment = partial.json()["data"]
            assert repayment["amount_kyat"] == 25_000
            assert repayment["repayment_date"] == "2026-02-01"
            assert repayment["note"] == "Cash payment"
            assert repayment["corrected"] is False
            assert partial.headers["etag"] == '"1"'
            repayment_id = repayment["id"]

            detail = await client.get(
                f"/api/v1/loans/{loan_id}",
                headers=headers(token=token),
            )
            assert detail.json()["data"]["total_repaid_kyat"] == 25_000
            assert detail.json()["data"]["outstanding_principal_kyat"] == 75_000
            assert detail.headers["etag"] == '"2"'

            history = await client.get(
                f"/api/v1/loans/{loan_id}/repayments",
                headers=headers(token=token),
            )
            assert history.status_code == 200
            assert [item["id"] for item in history.json()["data"]] == [
                repayment_id
            ]

            one = await client.get(
                f"/api/v1/loans/{loan_id}/repayments/{repayment_id}",
                headers=headers(token=token),
            )
            assert one.status_code == 200
            assert one.json()["data"]["id"] == repayment_id
            assert one.headers["etag"] == '"1"'

            over = await record_repayment(
                client,
                token,
                loan_id,
                repayment_payload(amount_kyat=75_001),
            )
            assert over.status_code == 409
            assert over.json()["error"]["code"] == "repayment_exceeds_principal"

            cases = [
                repayment_payload(amount_kyat=0),
                repayment_payload(amount_kyat=-1),
                repayment_payload(amount_kyat=1.5),
                repayment_payload(amount_kyat=1_000.0),
                repayment_payload(amount_kyat=1_000_000_001),
                repayment_payload(repayment_date="2026-02-30"),
                repayment_payload(repayment_date="2026-02-01T00:00:00"),
                repayment_payload(note="x" * 81),
            ]
            for payload in cases:
                invalid = await record_repayment(
                    client,
                    token,
                    loan_id,
                    payload,
                )
                assert invalid.status_code == 422, (payload, invalid.text)

            key = new_key("repay")
            replay_first = await record_repayment(
                client,
                token,
                loan_id,
                repayment_payload(amount_kyat=10_000),
                key=key,
            )
            assert replay_first.status_code == 201, replay_first.text
            replay = await record_repayment(
                client,
                token,
                loan_id,
                repayment_payload(amount_kyat=10_000),
                key=key,
            )
            assert replay.status_code == 201
            assert replay.json()["data"]["id"] == replay_first.json()["data"]["id"]

            conflict = await record_repayment(
                client,
                token,
                loan_id,
                repayment_payload(amount_kyat=11_000),
                key=key,
            )
            assert conflict.status_code == 409
            assert conflict.json()["error"]["code"] == "idempotency_key_reused"

            hidden = await record_repayment(
                client,
                other_token,
                loan_id,
                repayment_payload(amount_kyat=1_000),
            )
            assert hidden.status_code == 404

            hidden_history = await client.get(
                f"/api/v1/loans/{loan_id}/repayments",
                headers=headers(token=other_token),
            )
            assert hidden_history.status_code == 404

            payoff = await record_repayment(
                client,
                token,
                loan_id,
                repayment_payload(amount_kyat=65_000),
            )
            assert payoff.status_code == 201, payoff.text
            paid = await client.get(
                f"/api/v1/loans/{loan_id}",
                headers=headers(token=token),
            )
            assert paid.json()["data"]["total_repaid_kyat"] == 100_000
            assert paid.json()["data"]["outstanding_principal_kyat"] == 0

            after_payoff = await record_repayment(
                client,
                token,
                loan_id,
                repayment_payload(amount_kyat=1),
            )
            assert after_payoff.status_code == 409

            row = _db_repayment(tx_env, repayment_id)
            assert row is not None
            assert str(row["user_id"]) == first["user"]["id"]
            assert str(row["loan_id"]) == loan_id
            assert row["amount_kyat"] == 25_000

    asyncio.run(scenario())


def test_repayment_corrections_reverse_principal_and_are_append_only(
    tx_env: TransactionEnv,
):
    async def scenario() -> None:
        async with tx_env.client() as client:
            _, first = await register_verify_login(client, tx_env)
            _, second = await register_verify_login(client, tx_env)
            token = first["access_token"]
            other_token = second["access_token"]

            created = await create_loan(
                client,
                token,
                loan_payload(principal_kyat=100_000),
            )
            assert created.status_code == 201, created.text
            loan_id = created.json()["data"]["id"]

            repayment = await record_repayment(
                client,
                token,
                loan_id,
                repayment_payload(amount_kyat=25_000),
            )
            assert repayment.status_code == 201, repayment.text
            repayment_id = repayment.json()["data"]["id"]

            missing_key = await client.post(
                f"/api/v1/loans/{loan_id}/repayments/{repayment_id}/corrections",
                json={"reason": "Recorded twice"},
                headers=headers(token=token),
            )
            assert missing_key.status_code == 422

            invalid_reason = await client.post(
                f"/api/v1/loans/{loan_id}/repayments/{repayment_id}/corrections",
                json={"reason": "   "},
                headers=headers(token=token, idem=new_key("correction")),
            )
            assert invalid_reason.status_code == 422

            key = new_key("correction")
            corrected = await client.post(
                f"/api/v1/loans/{loan_id}/repayments/{repayment_id}/corrections",
                json={"reason": "  Recorded twice  "},
                headers=headers(token=token, idem=key),
            )
            assert corrected.status_code == 201, corrected.text
            body = corrected.json()["data"]
            assert body["original_repayment"]["id"] == repayment_id
            assert body["original_repayment"]["corrected"] is True
            assert body["correction"]["amount_kyat"] == 25_000
            assert body["correction"]["type"] == "reversal"
            assert body["correction"]["reason"] == "Recorded twice"
            assert body["outstanding_principal_kyat"] == 100_000
            assert corrected.headers["etag"] == '"2"'

            replay = await client.post(
                f"/api/v1/loans/{loan_id}/repayments/{repayment_id}/corrections",
                json={"reason": "Recorded twice"},
                headers=headers(token=token, idem=key),
            )
            assert replay.status_code == 201
            assert (
                replay.json()["data"]["correction"]["id"]
                == body["correction"]["id"]
            )

            duplicate = await client.post(
                f"/api/v1/loans/{loan_id}/repayments/{repayment_id}/corrections",
                json={"reason": "Again"},
                headers=headers(token=token, idem=new_key("correction")),
            )
            assert duplicate.status_code == 409
            assert duplicate.json()["error"]["code"] == (
                "repayment_already_corrected"
            )

            hidden = await client.post(
                f"/api/v1/loans/{loan_id}/repayments/{repayment_id}/corrections",
                json={"reason": "Other user"},
                headers=headers(token=other_token, idem=new_key("correction")),
            )
            assert hidden.status_code == 404

            detail = await client.get(
                f"/api/v1/loans/{loan_id}",
                headers=headers(token=token),
            )
            assert detail.json()["data"]["total_repaid_kyat"] == 0
            assert detail.json()["data"]["outstanding_principal_kyat"] == 100_000

            history = await client.get(
                f"/api/v1/loans/{loan_id}/repayments",
                headers=headers(token=token),
            )
            assert history.json()["data"][0]["corrected"] is True

            replacement = await record_repayment(
                client,
                token,
                loan_id,
                repayment_payload(amount_kyat=20_000),
            )
            assert replacement.status_code == 201, replacement.text

            counts = tx_env.one(
                "SELECT "
                "(SELECT count(*) FROM loan_repayments "
                " WHERE loan_id = :loan_id) AS repayments, "
                "(SELECT count(*) FROM loan_repayment_corrections "
                " WHERE loan_id = :loan_id) AS corrections",
                {"loan_id": loan_id},
            )
            assert counts is not None
            assert counts["repayments"] == 2
            assert counts["corrections"] == 1

    asyncio.run(scenario())


def test_concurrent_repayments_cannot_overpay(tx_env: TransactionEnv):
    async def scenario() -> None:
        async with tx_env.client() as client:
            _, session = await register_verify_login(client, tx_env)
            token = session["access_token"]

            created = await create_loan(
                client,
                token,
                loan_payload(principal_kyat=100_000),
            )
            assert created.status_code == 201, created.text
            loan_id = created.json()["data"]["id"]

            async with tx_env.client() as first_client, tx_env.client() as second_client:
                responses = await asyncio.gather(
                    record_repayment(
                        first_client,
                        token,
                        loan_id,
                        repayment_payload(amount_kyat=60_000),
                    ),
                    record_repayment(
                        second_client,
                        token,
                        loan_id,
                        repayment_payload(amount_kyat=60_000),
                    ),
                )
            statuses = sorted(response.status_code for response in responses)
            assert statuses == [201, 409], [
                response.text for response in responses
            ]

            detail = await client.get(
                f"/api/v1/loans/{loan_id}",
                headers=headers(token=token),
            )
            assert detail.json()["data"]["total_repaid_kyat"] == 60_000
            assert detail.json()["data"]["outstanding_principal_kyat"] == 40_000

            rows = tx_env.all(
                "SELECT id FROM loan_repayments WHERE loan_id = :loan_id",
                {"loan_id": loan_id},
            )
            assert len(rows) == 1

    asyncio.run(scenario())
