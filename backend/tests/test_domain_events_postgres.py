"""PostgreSQL integration tests for bounded domain-event logging.

These tests run against the dedicated test database configured through the
``FINPATH_TEST_*`` settings and skip when it is not available. They prove that
domain events are emitted once per committed action, suppressed on idempotent
replays and no-op operations, absent when a write rolls back, and never carry
sensitive user data.
"""

from __future__ import annotations

import asyncio
import logging

from app.core.observability import JsonFormatter
from tests.tx_support import (
    ORIGIN,
    PASSWORD,
    TransactionEnv,
    category_id,
    create_transaction,
    headers,
    login,
    new_email,
    new_key,
    register,
    register_verify_login,
    transaction_payload,
)

EXPENSE_CATEGORY = "Groceries"


def _enable_domain_capture(caplog) -> None:
    # Domain events are logged on ``app.*`` loggers; the ``app`` logger level
    # is set from configuration, so pin both it and the root capture level.
    caplog.set_level(logging.INFO)
    caplog.set_level(logging.INFO, logger="app")


def _events(caplog, name: str) -> list[logging.LogRecord]:
    return [
        record
        for record in caplog.records
        if getattr(record, "event", None) == name
    ]


def test_login_success_and_failure_events(tx_env: TransactionEnv, caplog) -> None:
    _enable_domain_capture(caplog)

    async def scenario() -> None:
        async with tx_env.client() as client:
            email, _ = await register_verify_login(client, tx_env)

            wrong_password = await client.post(
                "/api/v1/auth/login",
                json={"email": email, "password": "definitely-not-the-password"},
                headers=headers(origin=ORIGIN),
            )
            assert wrong_password.status_code == 401, wrong_password.text

            unknown = await client.post(
                "/api/v1/auth/login",
                json={"email": new_email(), "password": PASSWORD},
                headers=headers(origin=ORIGIN),
            )
            assert unknown.status_code == 401, unknown.text

            unverified_email = new_email()
            assert (await register(client, unverified_email)).status_code == 201
            unverified = await login(client, unverified_email)
            assert unverified.status_code == 403, unverified.text

    asyncio.run(scenario())

    successes = _events(caplog, "auth_login_succeeded")
    failures = _events(caplog, "auth_login_failed")

    assert len(successes) == 1
    assert successes[0].levelno == logging.INFO

    assert {record.reason for record in failures} == {
        "invalid_credentials",
        "email_verification_required",
    }
    assert all(record.levelno == logging.WARNING for record in failures)
    # Unknown email and wrong password are indistinguishable.
    assert (
        sum(
            1
            for record in failures
            if record.reason == "invalid_credentials"
        )
        == 2
    )


def test_login_failure_event_for_suspended_account(
    tx_env: TransactionEnv,
    caplog,
) -> None:
    _enable_domain_capture(caplog)

    async def scenario() -> None:
        async with tx_env.client() as client:
            email, _ = await register_verify_login(client, tx_env)
            tx_env.execute(
                "UPDATE users SET status = 'suspended' WHERE email = :email",
                {"email": email},
            )
            blocked = await login(client, email)
            assert blocked.status_code == 403, blocked.text

    asyncio.run(scenario())

    failures = _events(caplog, "auth_login_failed")
    assert [record.reason for record in failures] == ["account_suspended"]
    assert failures[0].levelno == logging.WARNING


def test_transaction_events_and_replay_suppression(
    tx_env: TransactionEnv,
    caplog,
) -> None:
    _enable_domain_capture(caplog)

    async def scenario() -> None:
        async with tx_env.client() as client:
            _, auth = await register_verify_login(client, tx_env)
            token = auth["access_token"]
            category = category_id(tx_env, "expense", EXPENSE_CATEGORY)

            create_key = new_key("tx")
            created = await create_transaction(
                client,
                token,
                transaction_payload(category),
                key=create_key,
            )
            assert created.status_code == 201, created.text

            # The same idempotency key must not produce a second event.
            replay = await create_transaction(
                client,
                token,
                transaction_payload(category),
                key=create_key,
            )
            assert replay.status_code == 201, replay.text

            transaction_id = created.json()["data"]["id"]
            updated = await client.patch(
                f"/api/v1/transactions/{transaction_id}",
                json={"description": "Updated market"},
                headers=headers(
                    token=token,
                    if_match=created.headers["etag"],
                ),
            )
            assert updated.status_code == 200, updated.text

            deleted = await client.delete(
                f"/api/v1/transactions/{transaction_id}",
                headers=headers(
                    token=token,
                    idem=new_key("tx-del"),
                    if_match=updated.headers["etag"],
                ),
            )
            assert deleted.status_code == 204, deleted.text

    asyncio.run(scenario())

    assert len(_events(caplog, "transaction_created")) == 1
    assert len(_events(caplog, "transaction_updated")) == 1
    assert len(_events(caplog, "transaction_deleted")) == 1


def test_loan_events_and_repayment_reversal(
    tx_env: TransactionEnv,
    caplog,
) -> None:
    _enable_domain_capture(caplog)

    async def scenario() -> None:
        async with tx_env.client() as client:
            _, auth = await register_verify_login(client, tx_env)
            token = auth["access_token"]

            loan = await client.post(
                "/api/v1/loans",
                json={
                    "name": "Car loan",
                    "principal_kyat": 100_000,
                    "start_date": "2026-01-15",
                },
                headers=headers(token=token, idem=new_key("loan")),
            )
            assert loan.status_code == 201, loan.text
            loan_id = loan.json()["data"]["id"]

            repayment = await client.post(
                f"/api/v1/loans/{loan_id}/repayments",
                json={
                    "amount_kyat": 25_000,
                    "repayment_date": "2026-02-01",
                    "note": "Cash payment",
                },
                headers=headers(token=token, idem=new_key("repay")),
            )
            assert repayment.status_code == 201, repayment.text
            repayment_id = repayment.json()["data"]["id"]

            correction = await client.post(
                f"/api/v1/loans/{loan_id}/repayments/"
                f"{repayment_id}/corrections",
                json={"reason": "Duplicate payment"},
                headers=headers(token=token, idem=new_key("correct")),
            )
            assert correction.status_code == 201, correction.text

    asyncio.run(scenario())

    assert len(_events(caplog, "loan_created")) == 1
    assert len(_events(caplog, "loan_repayment_recorded")) == 1
    assert len(_events(caplog, "loan_repayment_reversed")) == 1


def test_failed_repayment_emits_no_event(
    tx_env: TransactionEnv,
    caplog,
) -> None:
    _enable_domain_capture(caplog)

    async def scenario() -> None:
        async with tx_env.client() as client:
            _, auth = await register_verify_login(client, tx_env)
            token = auth["access_token"]

            loan = await client.post(
                "/api/v1/loans",
                json={
                    "name": "Car loan",
                    "principal_kyat": 100_000,
                    "start_date": "2026-01-15",
                },
                headers=headers(token=token, idem=new_key("loan")),
            )
            assert loan.status_code == 201, loan.text
            loan_id = loan.json()["data"]["id"]

            # Exceeding the outstanding principal rolls the write back.
            too_much = await client.post(
                f"/api/v1/loans/{loan_id}/repayments",
                json={
                    "amount_kyat": 200_000,
                    "repayment_date": "2026-02-01",
                    "note": None,
                },
                headers=headers(token=token, idem=new_key("repay")),
            )
            assert too_much.status_code == 409, too_much.text

    asyncio.run(scenario())

    assert len(_events(caplog, "loan_created")) == 1
    assert _events(caplog, "loan_repayment_recorded") == []


def test_savings_events_archive_restore_and_noop(
    tx_env: TransactionEnv,
    caplog,
) -> None:
    _enable_domain_capture(caplog)

    async def scenario() -> None:
        async with tx_env.client() as client:
            _, auth = await register_verify_login(client, tx_env)
            token = auth["access_token"]

            goal = await client.post(
                "/api/v1/savings/goals",
                json={
                    "name": "Emergency fund",
                    "target_kyat": 500_000,
                    "tone": "calm",
                },
                headers=headers(token=token, idem=new_key("goal")),
            )
            assert goal.status_code == 201, goal.text
            goal_id = goal.json()["data"]["id"]

            contribution = await client.post(
                f"/api/v1/savings/goals/{goal_id}/entries/contributions",
                json={
                    "amount_kyat": 25_000,
                    "entry_date": "2026-11-03",
                    "note": "Weekly deposit",
                },
                headers=headers(token=token, idem=new_key("save")),
            )
            assert contribution.status_code == 201, contribution.text

            withdrawal = await client.post(
                f"/api/v1/savings/goals/{goal_id}/entries/withdrawals",
                json={
                    "amount_kyat": 5_000,
                    "entry_date": "2026-11-04",
                    "note": "Cash",
                },
                headers=headers(token=token, idem=new_key("withdraw")),
            )
            assert withdrawal.status_code == 201, withdrawal.text

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

            # Re-archiving an already-archived goal is a no-op, so it must not
            # emit a second event even with a fresh idempotency key.
            noop = await client.post(
                f"/api/v1/savings/goals/{goal_id}/archive",
                json={"confirm": True},
                headers=headers(
                    token=token,
                    idem=new_key("archive-again"),
                    if_match=archived.headers["etag"],
                ),
            )
            assert noop.status_code == 200, noop.text

            restored = await client.post(
                f"/api/v1/savings/goals/{goal_id}/restore",
                json={"confirm": True},
                headers=headers(
                    token=token,
                    idem=new_key("restore"),
                    if_match=noop.headers["etag"],
                ),
            )
            assert restored.status_code == 200, restored.text

    asyncio.run(scenario())

    assert len(_events(caplog, "savings_contribution_recorded")) == 1
    assert len(_events(caplog, "savings_withdrawal_recorded")) == 1
    assert len(_events(caplog, "savings_goal_archived")) == 1
    assert len(_events(caplog, "savings_goal_restored")) == 1


def test_travel_spending_event_and_replay_suppression(
    tx_env: TransactionEnv,
    caplog,
) -> None:
    _enable_domain_capture(caplog)

    async def scenario() -> None:
        async with tx_env.client() as client:
            _, auth = await register_verify_login(client, tx_env)
            token = auth["access_token"]

            trip = await client.post(
                "/api/v1/trips",
                json={
                    "name": "Lisbon getaway",
                    "destination": "Lisbon, Portugal",
                    "start_date": "2026-11-14",
                    "end_date": "2026-11-19",
                },
                headers=headers(token=token, idem=new_key("trip")),
            )
            assert trip.status_code == 201, trip.text
            trip_id = trip.json()["data"]["id"]

            spend_key = new_key("spend")
            payload = {
                "description": "Flight deposit",
                "category": "Flights",
                "amount_kyat": 450_000,
                "spending_date": "2026-10-20",
                "category_budget_id": None,
            }
            first = await client.post(
                f"/api/v1/trips/{trip_id}/spending",
                json=payload,
                headers=headers(token=token, idem=spend_key),
            )
            assert first.status_code == 201, first.text

            replay = await client.post(
                f"/api/v1/trips/{trip_id}/spending",
                json=payload,
                headers=headers(token=token, idem=spend_key),
            )
            assert replay.status_code == 201, replay.text

    asyncio.run(scenario())

    assert len(_events(caplog, "travel_spending_recorded")) == 1


def test_report_export_event(tx_env: TransactionEnv, caplog) -> None:
    _enable_domain_capture(caplog)

    async def scenario() -> None:
        async with tx_env.client() as client:
            _, auth = await register_verify_login(client, tx_env)
            response = await client.get(
                "/api/v1/reports/export.csv",
                headers=headers(token=auth["access_token"]),
            )
            assert response.status_code == 200, response.text

    asyncio.run(scenario())

    assert len(_events(caplog, "report_exported")) == 1


def test_domain_events_never_contain_sensitive_values(
    tx_env: TransactionEnv,
    caplog,
) -> None:
    _enable_domain_capture(caplog)

    secret_email = new_email()
    secret_description = "Sensitive market purchase"
    secret_note = "private-note-value"
    secret_amount = 987_654_321
    captured: dict[str, str] = {}

    async def scenario() -> None:
        async with tx_env.client() as client:
            registered = await register(client, secret_email)
            assert registered.status_code == 201, registered.text
            verification_token = tx_env.sender.last_token(
                secret_email, "email_verification"
            )
            verified = await client.post(
                "/api/v1/auth/email-verification/consume",
                json={"token": verification_token},
                headers=headers(idem=new_key("verify"), origin=ORIGIN),
            )
            assert verified.status_code == 200, verified.text

            logged_in = await login(client, secret_email)
            assert logged_in.status_code == 200, logged_in.text
            access_token = logged_in.json()["data"]["access_token"]
            captured["access_token"] = access_token
            captured["verification_token"] = verification_token

            category = category_id(tx_env, "expense", EXPENSE_CATEGORY)
            created = await create_transaction(
                client,
                access_token,
                transaction_payload(
                    category,
                    description=secret_description,
                    amount_kyat=secret_amount,
                ),
                key=new_key("tx"),
            )
            assert created.status_code == 201, created.text

            goal = await client.post(
                "/api/v1/savings/goals",
                json={
                    "name": "Sensitive goal name",
                    "target_kyat": 500_000,
                    "tone": "calm",
                },
                headers=headers(token=access_token, idem=new_key("goal")),
            )
            assert goal.status_code == 201, goal.text
            goal_id = goal.json()["data"]["id"]
            contribution = await client.post(
                f"/api/v1/savings/goals/{goal_id}/entries/contributions",
                json={
                    "amount_kyat": 12_345,
                    "entry_date": "2026-11-03",
                    "note": secret_note,
                },
                headers=headers(token=access_token, idem=new_key("save")),
            )
            assert contribution.status_code == 201, contribution.text

            failed_login = await client.post(
                "/api/v1/auth/login",
                json={"email": secret_email, "password": "wrong-password"},
                headers=headers(origin=ORIGIN),
            )
            assert failed_login.status_code == 401, failed_login.text

            exported = await client.get(
                "/api/v1/reports/export.csv",
                headers=headers(token=access_token),
            )
            assert exported.status_code == 200, exported.text

    asyncio.run(scenario())

    formatted = "\n".join(
        JsonFormatter().format(record) for record in caplog.records
    )

    for secret in (
        secret_email,
        secret_description,
        secret_note,
        str(secret_amount),
        captured["access_token"],
        captured["verification_token"],
        PASSWORD,
    ):
        assert secret not in formatted

    for header_name in ("Authorization", "Cookie", "Set-Cookie"):
        assert header_name not in formatted
