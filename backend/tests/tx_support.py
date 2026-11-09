"""Shared helpers for the Transactions API PostgreSQL tests."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any
from uuid import UUID, uuid4

import httpx
from sqlalchemy import text
from sqlalchemy.engine import Engine

from app.main import app
from app.services.email import EmailDeliveryError, EmailMessage

ORIGIN = "http://localhost:5173"
PASSWORD = "correct horse battery staple"


class RecordingSender:
    def __init__(self) -> None:
        self.messages: list[EmailMessage] = []

    @property
    def is_available(self) -> bool:
        return True

    def send(self, message: EmailMessage) -> None:
        self.messages.append(message)

    def last_token(self, email: str, purpose: str) -> str:
        for message in reversed(self.messages):
            if message.to == email and message.purpose == purpose:
                return message.token
        raise AssertionError(f"No {purpose} email for {email}")


@dataclass
class TransactionEnv:
    runtime_engine: Engine
    registration_engine: Engine
    migration_engine: Engine
    sender: RecordingSender
    database_name: str

    def client(self, **kwargs: Any) -> httpx.AsyncClient:
        transport = httpx.ASGITransport(
            app=app,
            raise_app_exceptions=False,
        )
        return httpx.AsyncClient(
            transport=transport,
            base_url="https://test",
            **kwargs,
        )

    def one(self, statement: str, parameters: dict[str, Any] | None = None):
        with self.migration_engine.connect() as connection:
            return connection.execute(
                text(statement), parameters or {}
            ).mappings().first()

    def all(self, statement: str, parameters: dict[str, Any] | None = None):
        with self.migration_engine.connect() as connection:
            return list(
                connection.execute(
                    text(statement), parameters or {}
                ).mappings()
            )

    def execute(self, statement: str, parameters: dict[str, Any] | None = None):
        with self.migration_engine.begin() as connection:
            connection.execute(text(statement), parameters or {})


def new_email() -> str:
    return f"tx-test-{uuid4().hex}@example.test"


def new_key(prefix: str = "key") -> str:
    return f"{prefix}-{uuid4().hex}"


def headers(
    *,
    token: str | None = None,
    idem: str | None = None,
    if_match: str | None = None,
    origin: str | None = None,
) -> dict[str, str]:
    result: dict[str, str] = {}
    if token is not None:
        result["Authorization"] = f"Bearer {token}"
    if idem is not None:
        result["Idempotency-Key"] = idem
    if if_match is not None:
        result["If-Match"] = if_match
    if origin is not None:
        result["Origin"] = origin
    return result


async def register(
    client: httpx.AsyncClient,
    email: str,
    *,
    key: str | None = None,
) -> httpx.Response:
    return await client.post(
        "/api/v1/auth/register",
        json={"email": email, "password": PASSWORD},
        headers=headers(idem=key or new_key("reg"), origin=ORIGIN),
    )


async def verify(
    client: httpx.AsyncClient,
    env: TransactionEnv,
    email: str,
) -> httpx.Response:
    token = env.sender.last_token(email, "email_verification")
    return await client.post(
        "/api/v1/auth/email-verification/consume",
        json={"token": token},
        headers=headers(idem=new_key("verify"), origin=ORIGIN),
    )


async def login(
    client: httpx.AsyncClient,
    email: str,
) -> httpx.Response:
    return await client.post(
        "/api/v1/auth/login",
        json={"email": email, "password": PASSWORD},
        headers=headers(origin=ORIGIN),
    )


async def register_verify_login(
    client: httpx.AsyncClient,
    env: TransactionEnv,
    email: str | None = None,
) -> tuple[str, dict[str, Any]]:
    address = email or new_email()
    registered = await register(client, address)
    assert registered.status_code == 201, registered.text
    verified = await verify(client, env, address)
    assert verified.status_code == 200, verified.text
    logged_in = await login(client, address)
    assert logged_in.status_code == 200, logged_in.text
    return address, logged_in.json()["data"]


def category_id(env: TransactionEnv, transaction_type: str, name: str) -> UUID:
    row = env.one(
        "SELECT id FROM transaction_categories "
        "WHERE type = :type AND name = :name",
        {"type": transaction_type, "name": name},
    )
    assert row is not None, (transaction_type, name)
    return UUID(str(row["id"]))


def transaction_payload(
    category: UUID,
    *,
    description: str = "Market",
    transaction_type: str = "expense",
    amount_kyat: int = 12500,
    transaction_date: str = "2026-11-03",
) -> dict[str, Any]:
    return {
        "description": description,
        "type": transaction_type,
        "category_id": str(category),
        "amount_kyat": amount_kyat,
        "transaction_date": transaction_date,
    }


async def create_transaction(
    client: httpx.AsyncClient,
    token: str,
    payload: dict[str, Any],
    *,
    key: str | None = None,
) -> httpx.Response:
    return await client.post(
        "/api/v1/transactions",
        json=payload,
        headers=headers(token=token, idem=key or new_key("tx")),
    )


def set_starting_balance(
    env: TransactionEnv,
    *,
    user_id: str,
    starting_balance_kyat: int,
    effective_date: str,
) -> None:
    env.execute(
        "UPDATE user_financial_settings "
        "SET starting_balance_kyat = :starting_balance_kyat, "
        "effective_date = :effective_date, revision = revision + 1, "
        "updated_at = now() "
        "WHERE user_id = :user_id",
        {
            "starting_balance_kyat": starting_balance_kyat,
            "effective_date": effective_date,
            "user_id": user_id,
        },
    )


def insert_bulk_transactions(
    env: TransactionEnv,
    *,
    user_id: str,
    category: UUID,
    count: int,
    start_date: str = "2026-01-01",
) -> None:
    env.execute(
        """
        INSERT INTO transactions (
            id, user_id, category_id, type, description,
            amount_kyat, transaction_date
        )
        SELECT gen_random_uuid(), :user_id, :category_id, 'expense',
               'Bulk ' || series, 1000,
               CAST(:start_date AS date) + series
        FROM generate_series(1, :count) AS series
        """,
        {
            "user_id": user_id,
            "category_id": category,
            "count": count,
            "start_date": start_date,
        },
    )
