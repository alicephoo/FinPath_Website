"""Owner-scoped transaction services and financial calculations.

All SQL in this module runs through the normal runtime identity (``finpath_app``)
and is scoped by the authenticated user's ID. Client-supplied ownership fields
are never accepted.
"""

from __future__ import annotations

import base64
import binascii
import json
import re
from dataclasses import dataclass
from datetime import date, datetime, timezone
from decimal import Decimal
from typing import Any, Mapping
from uuid import UUID, uuid4

from sqlalchemy import text
from sqlalchemy.exc import IntegrityError, SQLAlchemyError
from sqlalchemy.orm import Session

from app.api.errors import ApiError
from app.core.config import settings
from app.schemas.transactions import (
    TransactionCreateRequest,
    TransactionUpdateRequest,
)
from app.services.auth import AuthContext
from app.services.idempotency import IdempotencyResult, run_idempotent

# JSON numbers are interoperable only within the IEEE-754 safe-integer range.
JSON_SAFE_INTEGER_MAX = 9_007_199_254_740_991

_TRANSACTION_SELECT = """
SELECT t.id, t.description, t.type, t.category_id, c.name AS category,
       t.amount_kyat, t.transaction_date, t.created_at, t.updated_at,
       t.revision
FROM transactions AS t
JOIN transaction_categories AS c ON c.id = t.category_id
"""

_DATE_PATTERN = re.compile(r"^\d{4}-\d{2}-\d{2}$")
_IF_MATCH_PATTERN = re.compile(r'^"([0-9]+)"$')


@dataclass(frozen=True)
class TransactionView:
    body: dict[str, Any]
    etag: str


@dataclass(frozen=True)
class AccountBalance:
    status: str
    starting_balance_kyat: int | None
    effective_date: date | None
    amount_kyat: int | None
    as_of_date: date


@dataclass(frozen=True)
class TransactionTotals:
    income_kyat: int
    expense_kyat: int


def _now() -> datetime:
    return datetime.now(timezone.utc)


def _etag(revision: int) -> str:
    return f'"{revision}"'


def _field_error(field: str, code: str, message: str) -> dict[str, str]:
    return {"field": field, "code": code, "message": message}


def _not_found() -> ApiError:
    return ApiError(
        404,
        "not_found",
        "The requested transaction was not found.",
    )


def parse_query_date(value: str | None, field: str) -> date | None:
    """Parse an inclusive ``YYYY-MM-DD`` query date or raise a contract error."""
    if value is None:
        return None
    if not _DATE_PATTERN.fullmatch(value):
        raise ApiError(
            422,
            "invalid_date_range",
            "Date filters must use YYYY-MM-DD.",
            field_errors=[
                _field_error(
                    field,
                    "invalid_date_range",
                    "Use a valid YYYY-MM-DD date.",
                )
            ],
        )
    try:
        return date.fromisoformat(value)
    except ValueError:
        raise ApiError(
            422,
            "invalid_date_range",
            "Date filters must use YYYY-MM-DD.",
            field_errors=[
                _field_error(
                    field,
                    "invalid_date_range",
                    "Use a valid calendar date.",
                )
            ],
        ) from None


def validate_date_range(
    from_date: date | None,
    to_date: date | None,
) -> None:
    if from_date is not None and to_date is not None and from_date > to_date:
        raise ApiError(
            422,
            "invalid_date_range",
            "The start date must not be after the end date.",
            field_errors=[
                _field_error(
                    "from",
                    "invalid_date_range",
                    "The start date must not be after the end date.",
                )
            ],
        )


def _parse_if_match(value: str | None) -> int:
    if value is None or not value.strip():
        raise ApiError(
            428,
            "precondition_required",
            "An If-Match header with the current ETag is required.",
        )
    match = _IF_MATCH_PATTERN.fullmatch(value.strip())
    if match is None:
        raise ApiError(
            412,
            "stale_revision",
            "The supplied revision does not match the current transaction.",
        )
    return int(match.group(1))


def _validate_amount(amount_kyat: int) -> None:
    if amount_kyat <= 0:
        raise ApiError(
            422,
            "validation_error",
            "The transaction amount must be a positive whole-Kyat integer.",
            field_errors=[
                _field_error(
                    "amount_kyat",
                    "amount_not_positive",
                    "Enter an amount greater than zero.",
                )
            ],
        )
    if amount_kyat > JSON_SAFE_INTEGER_MAX:
        raise ApiError(
            422,
            "amount_not_safe_integer",
            "The transaction amount is outside the supported JSON integer range.",
            field_errors=[
                _field_error(
                    "amount_kyat",
                    "amount_not_safe_integer",
                    "The amount is too large to represent safely.",
                )
            ],
        )
    if amount_kyat > settings.max_financial_entry_kyat:
        raise ApiError(
            422,
            "amount_limit_exceeded",
            "The transaction amount exceeds the individual-entry limit.",
            field_errors=[
                _field_error(
                    "amount_kyat",
                    "amount_limit_exceeded",
                    (
                        "The maximum amount for one entry is "
                        f"{settings.max_financial_entry_kyat} MMK."
                    ),
                )
            ],
        )


def _active_category_for_assignment(
    session: Session,
    *,
    category_id: UUID,
    transaction_type: str,
) -> Mapping[str, Any]:
    category = session.execute(
        text(
            """
            SELECT id, name, type, is_active
            FROM transaction_categories
            WHERE id = :category_id
            """
        ),
        {"category_id": category_id},
    ).mappings().first()
    if category is None or not category["is_active"]:
        raise ApiError(
            422,
            "validation_error",
            "The selected transaction category is not available.",
            field_errors=[
                _field_error(
                    "category_id",
                    "invalid_category",
                    "Choose an active predefined category.",
                )
            ],
        )
    if category["type"] != transaction_type:
        raise ApiError(
            422,
            "validation_error",
            "The selected category does not match the transaction type.",
            field_errors=[
                _field_error(
                    "category_id",
                    "category_type_mismatch",
                    "Choose a category with the same income/expense type.",
                )
            ],
        )
    return category


def _category_name(session: Session, category_id: UUID) -> str:
    name = session.execute(
        text("SELECT name FROM transaction_categories WHERE id = :category_id"),
        {"category_id": category_id},
    ).scalar_one_or_none()
    if name is None:
        # The composite FK prevents this for persisted rows. Treat an
        # inconsistent reference as a safe not-found response rather than
        # leaking database details.
        raise _not_found()
    return str(name)


def _resource(
    row: Mapping[str, Any],
    *,
    category_name: str | None = None,
) -> dict[str, Any]:
    return {
        "id": str(row["id"]),
        "description": row["description"],
        "type": row["type"],
        "category_id": str(row["category_id"]),
        "category": category_name if category_name is not None else row["category"],
        "amount_kyat": int(row["amount_kyat"]),
        "transaction_date": row["transaction_date"].isoformat(),
        "created_at": row["created_at"].isoformat(),
        "updated_at": row["updated_at"].isoformat(),
    }


def _row_for_user(
    session: Session,
    *,
    user_id: UUID,
    transaction_id: UUID,
    for_update: bool = False,
) -> Mapping[str, Any] | None:
    lock_clause = " FOR UPDATE OF t" if for_update else ""
    return session.execute(
        text(
            _TRANSACTION_SELECT
            + " WHERE t.user_id = :user_id AND t.id = :transaction_id"
            + lock_clause
        ),
        {"user_id": user_id, "transaction_id": transaction_id},
    ).mappings().first()


def list_categories(
    session: Session,
    *,
    transaction_type: str | None,
) -> list[dict[str, Any]]:
    parameters: dict[str, Any] = {}
    statement = (
        "SELECT id, name, type FROM transaction_categories "
        "WHERE is_active = true"
    )
    if transaction_type is not None:
        statement += " AND type = :transaction_type"
        parameters["transaction_type"] = transaction_type
    statement += " ORDER BY type ASC, lower(name) ASC, id ASC"
    rows = session.execute(text(statement), parameters).mappings().all()
    return [
        {"id": str(row["id"]), "name": row["name"], "type": row["type"]}
        for row in rows
    ]


def create_transaction(
    session: Session,
    *,
    context: AuthContext,
    payload: TransactionCreateRequest,
    idempotency_key: str,
) -> IdempotencyResult:
    request_payload = payload.model_dump(mode="json")

    def operation() -> IdempotencyResult:
        category = _active_category_for_assignment(
            session,
            category_id=payload.category_id,
            transaction_type=payload.type,
        )
        _validate_amount(payload.amount_kyat)
        transaction_id = uuid4()
        row = session.execute(
            text(
                """
                INSERT INTO transactions (
                    id, user_id, category_id, type, description,
                    amount_kyat, transaction_date
                ) VALUES (
                    :id, :user_id, :category_id, :type, :description,
                    :amount_kyat, :transaction_date
                )
                RETURNING created_at, updated_at, revision
                """
            ),
            {
                "id": transaction_id,
                "user_id": context.user_id,
                "category_id": payload.category_id,
                "type": payload.type,
                "description": payload.description,
                "amount_kyat": payload.amount_kyat,
                "transaction_date": payload.transaction_date,
            },
        ).mappings().one()
        body = {
            "data": {
                "id": str(transaction_id),
                "description": payload.description,
                "type": payload.type,
                "category_id": str(payload.category_id),
                "category": category["name"],
                "amount_kyat": payload.amount_kyat,
                "transaction_date": payload.transaction_date.isoformat(),
                "created_at": row["created_at"].isoformat(),
                "updated_at": row["updated_at"].isoformat(),
            }
        }
        return IdempotencyResult(
            status_code=201,
            body=body,
            headers={"ETag": _etag(int(row["revision"]))},
        )

    try:
        result, _ = run_idempotent(
            session,
            scope=f"user:{context.user_id}",
            method="POST",
            route="/api/v1/transactions",
            key=idempotency_key,
            payload=request_payload,
            owner_user_id=context.user_id,
            operation=operation,
        )
        session.commit()
    except ApiError:
        session.rollback()
        raise
    except IntegrityError as exc:
        session.rollback()
        constraint = getattr(
            getattr(exc.orig, "diag", None), "constraint_name", None
        )
        if constraint and "category" in constraint:
            raise ApiError(
                422,
                "validation_error",
                "The selected transaction category is not available.",
                field_errors=[
                    _field_error(
                        "category_id",
                        "invalid_category",
                        "Choose an active predefined category.",
                    )
                ],
            ) from None
        raise
    except SQLAlchemyError:
        session.rollback()
        raise
    return result


def get_transaction(
    session: Session,
    *,
    context: AuthContext,
    transaction_id: UUID,
) -> TransactionView:
    row = _row_for_user(
        session,
        user_id=context.user_id,
        transaction_id=transaction_id,
    )
    if row is None:
        raise _not_found()
    return TransactionView(
        body={"data": _resource(row)},
        etag=_etag(int(row["revision"])),
    )


def _encode_cursor(row: Mapping[str, Any]) -> str:
    raw = json.dumps(
        {
            "transaction_date": row["transaction_date"].isoformat(),
            "id": str(row["id"]),
        },
        separators=(",", ":"),
    ).encode("utf-8")
    return base64.urlsafe_b64encode(raw).decode("ascii").rstrip("=")


def _decode_cursor(cursor: str) -> tuple[date, UUID]:
    try:
        padded = cursor + "=" * (-len(cursor) % 4)
        decoded = base64.urlsafe_b64decode(padded.encode("ascii"))
        payload = json.loads(decoded.decode("utf-8"))
        cursor_date = date.fromisoformat(payload["transaction_date"])
        cursor_id = UUID(payload["id"])
    except (
        ValueError,
        TypeError,
        KeyError,
        UnicodeDecodeError,
        binascii.Error,
        json.JSONDecodeError,
    ):
        raise ApiError(
            422,
            "validation_error",
            "The pagination cursor is invalid.",
            field_errors=[
                _field_error(
                    "cursor",
                    "invalid_cursor",
                    "Use the opaque cursor returned by a previous response.",
                )
            ],
        ) from None
    return cursor_date, cursor_id


def _escape_like(value: str) -> str:
    return (
        value.replace("\\", "\\\\")
        .replace("%", "\\%")
        .replace("_", "\\_")
    )


def list_transactions(
    session: Session,
    *,
    context: AuthContext,
    transaction_type: str | None,
    category_id: UUID | None,
    from_date: date | None,
    to_date: date | None,
    q: str | None,
    limit: int,
    cursor: str | None,
) -> dict[str, Any]:
    validate_date_range(from_date, to_date)

    conditions = ["t.user_id = :user_id"]
    parameters: dict[str, Any] = {"user_id": context.user_id}

    if transaction_type is not None:
        conditions.append("t.type = :transaction_type")
        parameters["transaction_type"] = transaction_type
    if category_id is not None:
        conditions.append("t.category_id = :category_id")
        parameters["category_id"] = category_id
    if from_date is not None:
        conditions.append("t.transaction_date >= :from_date")
        parameters["from_date"] = from_date
    if to_date is not None:
        conditions.append("t.transaction_date <= :to_date")
        parameters["to_date"] = to_date
    if q is not None:
        conditions.append("t.description ILIKE :description_pattern ESCAPE '\\'")
        parameters["description_pattern"] = f"%{_escape_like(q)}%"

    if cursor is not None:
        cursor_date, cursor_id = _decode_cursor(cursor)
        conditions.append(
            "(t.transaction_date, t.id) < (:cursor_date, :cursor_id)"
        )
        parameters["cursor_date"] = cursor_date
        parameters["cursor_id"] = cursor_id

    parameters["fetch_limit"] = limit + 1
    statement = (
        _TRANSACTION_SELECT
        + " WHERE "
        + " AND ".join(conditions)
        + " ORDER BY t.transaction_date DESC, t.id DESC"
        + " LIMIT :fetch_limit"
    )
    rows = session.execute(text(statement), parameters).mappings().all()

    has_more = len(rows) > limit
    page = rows[:limit]
    next_cursor = _encode_cursor(page[-1]) if has_more and page else None
    return {
        "data": [_resource(row) for row in page],
        "pagination": {"limit": limit, "next_cursor": next_cursor},
    }


def update_transaction(
    session: Session,
    *,
    context: AuthContext,
    transaction_id: UUID,
    payload: TransactionUpdateRequest,
    if_match: str | None,
) -> TransactionView:
    expected_revision = _parse_if_match(if_match)
    changed = payload.model_fields_set

    try:
        row = _row_for_user(
            session,
            user_id=context.user_id,
            transaction_id=transaction_id,
            for_update=True,
        )
        if row is None:
            raise _not_found()
        if int(row["revision"]) != expected_revision:
            raise ApiError(
                412,
                "stale_revision",
                "The supplied revision does not match the current transaction.",
            )

        final_type = payload.type if "type" in changed else row["type"]
        final_category_id = (
            payload.category_id if "category_id" in changed else row["category_id"]
        )
        final_amount = (
            payload.amount_kyat
            if "amount_kyat" in changed
            else int(row["amount_kyat"])
        )
        final_description = (
            payload.description
            if "description" in changed
            else row["description"]
        )
        final_transaction_date = (
            payload.transaction_date
            if "transaction_date" in changed
            else row["transaction_date"]
        )

        if "amount_kyat" in changed:
            _validate_amount(final_amount)

        category_changed = (
            final_type != row["type"]
            or final_category_id != row["category_id"]
        )
        if category_changed:
            category = _active_category_for_assignment(
                session,
                category_id=final_category_id,
                transaction_type=final_type,
            )
            category_name = str(category["name"])
        else:
            category_name = str(row["category"])

        updated = session.execute(
            text(
                """
                UPDATE transactions
                SET category_id = :category_id,
                    type = :type,
                    description = :description,
                    amount_kyat = :amount_kyat,
                    transaction_date = :transaction_date,
                    updated_at = now(),
                    revision = revision + 1
                WHERE id = :transaction_id
                  AND user_id = :user_id
                  AND revision = :expected_revision
                RETURNING updated_at, revision
                """
            ),
            {
                "category_id": final_category_id,
                "type": final_type,
                "description": final_description,
                "amount_kyat": final_amount,
                "transaction_date": final_transaction_date,
                "transaction_id": transaction_id,
                "user_id": context.user_id,
                "expected_revision": expected_revision,
            },
        ).mappings().first()
        if updated is None:
            raise ApiError(
                412,
                "stale_revision",
                "The supplied revision does not match the current transaction.",
            )
        session.commit()
    except ApiError:
        session.rollback()
        raise
    except SQLAlchemyError:
        session.rollback()
        raise

    return TransactionView(
        body={
            "data": {
                "id": str(row["id"]),
                "description": final_description,
                "type": final_type,
                "category_id": str(final_category_id),
                "category": category_name,
                "amount_kyat": int(final_amount),
                "transaction_date": final_transaction_date.isoformat(),
                "created_at": row["created_at"].isoformat(),
                "updated_at": updated["updated_at"].isoformat(),
            }
        },
        etag=_etag(int(updated["revision"])),
    )


def delete_transaction(
    session: Session,
    *,
    context: AuthContext,
    transaction_id: UUID,
    if_match: str | None,
    idempotency_key: str,
) -> IdempotencyResult:
    normalized_if_match = if_match.strip() if if_match is not None else None

    def operation() -> IdempotencyResult:
        expected_revision = _parse_if_match(normalized_if_match)
        row = _row_for_user(
            session,
            user_id=context.user_id,
            transaction_id=transaction_id,
            for_update=True,
        )
        if row is None:
            raise _not_found()
        if int(row["revision"]) != expected_revision:
            raise ApiError(
                412,
                "stale_revision",
                "The supplied revision does not match the current transaction.",
            )
        session.execute(
            text(
                "DELETE FROM transactions "
                "WHERE id = :transaction_id AND user_id = :user_id"
            ),
            {"transaction_id": transaction_id, "user_id": context.user_id},
        )
        return IdempotencyResult(status_code=204, body={}, headers={})

    try:
        result, _ = run_idempotent(
            session,
            scope=f"user:{context.user_id}",
            method="DELETE",
            route=f"/api/v1/transactions/{transaction_id}",
            key=idempotency_key,
            payload={"if_match": normalized_if_match},
            owner_user_id=context.user_id,
            operation=operation,
        )
        session.commit()
    except ApiError:
        session.rollback()
        raise
    except SQLAlchemyError:
        session.rollback()
        raise
    return result


def _safe_aggregate(value: Decimal, *, label: str) -> int:
    if value != value.to_integral_value() or not (
        -JSON_SAFE_INTEGER_MAX <= value <= JSON_SAFE_INTEGER_MAX
    ):
        raise ApiError(
            500,
            "aggregate_out_of_range",
            f"The {label} cannot be represented safely.",
        )
    return int(value)


def calculate_transaction_totals(
    session: Session,
    *,
    user_id: UUID,
    from_date: date | None = None,
    to_date: date | None = None,
) -> TransactionTotals:
    """Return checked income and expense totals for a user's transactions.

    ``from_date`` and ``to_date`` are inclusive. The totals cover transactions
    only; loan repayments, savings entries, and trip spending live in separate
    tables and are never included.
    """
    conditions = ["user_id = :user_id"]
    parameters: dict[str, Any] = {"user_id": user_id}
    if from_date is not None:
        conditions.append("transaction_date >= :from_date")
        parameters["from_date"] = from_date
    if to_date is not None:
        conditions.append("transaction_date <= :to_date")
        parameters["to_date"] = to_date

    totals = session.execute(
        text(
            """
            SELECT
                COALESCE(
                    SUM(amount_kyat::numeric)
                        FILTER (WHERE type = 'income'),
                    0
                ) AS income,
                COALESCE(
                    SUM(amount_kyat::numeric)
                        FILTER (WHERE type = 'expense'),
                    0
                ) AS expense
            FROM transactions
            WHERE """
            + " AND ".join(conditions)
        ),
        parameters,
    ).mappings().one()
    return TransactionTotals(
        income_kyat=_safe_aggregate(
            Decimal(totals["income"]),
            label="transaction income total",
        ),
        expense_kyat=_safe_aggregate(
            Decimal(totals["expense"]),
            label="transaction expense total",
        ),
    )


def calculate_account_balance(
    session: Session,
    *,
    user_id: UUID,
    as_of_date: date | None = None,
) -> AccountBalance:
    """Return the owner's starting-balance plus eligible cash movements.

    Transactions, loan proceeds, and unreversed loan repayments are included
    only when their respective dates are strictly after the configured
    effective date. The loan model is borrowing-only: ``loans.start_date`` is
    the date the borrowed cash was received, and each unreversed
    ``loan_repayments`` row is a cash outflow. Loan repayments are not
    transaction expenses, so they are counted exactly once here. Savings
    entries remain separate and are excluded. Travel is an allocation layer:
    each active trip deducts the greater of its planned budget and actual
    spending, while completed or cancelled trips deduct actual spending only.
    """
    report_date = as_of_date or date.today()
    settings_row = session.execute(
        text(
            """
            SELECT starting_balance_kyat, effective_date
            FROM user_financial_settings
            WHERE user_id = :user_id
            """
        ),
        {"user_id": user_id},
    ).mappings().first()

    if (
        settings_row is None
        or settings_row["starting_balance_kyat"] is None
        or settings_row["effective_date"] is None
    ):
        return AccountBalance(
            status="not_configured",
            starting_balance_kyat=None,
            effective_date=None,
            amount_kyat=None,
            as_of_date=report_date,
        )

    totals = session.execute(
        text(
            """
            SELECT
                COALESCE(
                    SUM(amount_kyat::numeric)
                        FILTER (WHERE type = 'income'),
                    0
                ) AS income,
                COALESCE(
                    SUM(amount_kyat::numeric)
                        FILTER (WHERE type = 'expense'),
                    0
                ) AS expense
            FROM transactions
            WHERE user_id = :user_id
              AND transaction_date > :effective_date
            """
        ),
        {
            "user_id": user_id,
            "effective_date": settings_row["effective_date"],
        },
    ).mappings().one()

    loan_totals = session.execute(
        text(
            """
            SELECT
                COALESCE((
                    SELECT SUM(l.principal_kyat::numeric)
                    FROM loans AS l
                    WHERE l.user_id = :user_id
                      AND l.start_date > :effective_date
                ), 0) AS borrowed,
                COALESCE((
                    SELECT SUM(r.amount_kyat::numeric)
                    FROM loan_repayments AS r
                    LEFT JOIN loan_repayment_corrections AS c
                        ON c.repayment_id = r.id
                    WHERE r.user_id = :user_id
                      AND c.id IS NULL
                      AND r.repayment_date > :effective_date
                ), 0) AS repaid
            """
        ),
        {
            "user_id": user_id,
            "effective_date": settings_row["effective_date"],
        },
    ).mappings().one()

    starting_balance = Decimal(settings_row["starting_balance_kyat"])
    if not (-JSON_SAFE_INTEGER_MAX <= starting_balance <= JSON_SAFE_INTEGER_MAX):
        raise ApiError(
            500,
            "aggregate_out_of_range",
            "The calculated balance cannot be represented safely.",
        )
    from app.services.travel import calculate_recognized_travel_expense

    travel_expense = calculate_recognized_travel_expense(
        session,
        user_id=user_id,
    )
    amount = (
        starting_balance
        + Decimal(totals["income"])
        - Decimal(totals["expense"])
        + Decimal(loan_totals["borrowed"])
        - Decimal(loan_totals["repaid"])
        - Decimal(travel_expense)
    )
    if amount != amount.to_integral_value() or not (
        -JSON_SAFE_INTEGER_MAX <= amount <= JSON_SAFE_INTEGER_MAX
    ):
        raise ApiError(
            500,
            "aggregate_out_of_range",
            "The calculated balance cannot be represented safely.",
        )

    return AccountBalance(
        status="configured",
        starting_balance_kyat=int(starting_balance),
        effective_date=settings_row["effective_date"],
        amount_kyat=int(amount),
        as_of_date=report_date,
    )
