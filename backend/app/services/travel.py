"""Owner-scoped trip, planned-expense, and actual-spending services.

All SQL runs through the normal runtime identity (``finpath_app``) and is scoped
by the authenticated user's ID. Client-supplied ownership fields are never
accepted.

Planned expenses reuse the existing ``trip_category_budgets`` table where
``category`` is the planned-expense name/category. Actual spending reuses
``trip_spending_entries``. Trip spending is never written as a transaction, but
the shared travel-expense calculation feeds account totals: an active trip
recognizes the greater of its allocation and actual spending, while completed
or cancelled trips recognize actual spending only.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from decimal import Decimal
from typing import Any, Mapping
from uuid import UUID, uuid4

from sqlalchemy import text
from sqlalchemy.exc import SQLAlchemyError
from sqlalchemy.orm import Session

from app.api.errors import ApiError
from app.core.config import settings
from app.schemas.travel import (
    TripBudgetCreateRequest,
    TripBudgetUpdateRequest,
    TripCreateRequest,
    TripImportRequest,
    TripSpendingCreateRequest,
    TripSpendingUpdateRequest,
    TripUpdateRequest,
)
from app.services.auth import AuthContext
from app.services.idempotency import IdempotencyResult, run_idempotent

JSON_SAFE_INTEGER_MAX = 9_007_199_254_740_991

# The Pages edge may weaken the server's strong ETag to W/"N" in transit
# while preserving the revision. Accept both forms and compare numerically.
_IF_MATCH_PATTERN = re.compile(r'^(?:W/)?"([0-9]+)"$')

_TRIP_SELECT = """
SELECT t.id, t.name, t.destination, t.start_date, t.end_date,
       t.status, t.revision,
       COALESCE((
           SELECT SUM(b.budget_kyat::numeric)
           FROM trip_category_budgets AS b
           WHERE b.trip_id = t.id AND b.user_id = t.user_id
       ), 0) AS budget_kyat,
       COALESCE((
           SELECT SUM(s.amount_kyat::numeric)
           FROM trip_spending_entries AS s
           WHERE s.trip_id = t.id AND s.user_id = t.user_id
       ), 0) AS spending_kyat
FROM trips AS t
"""

_BUDGET_SELECT = """
SELECT b.id, b.category, b.budget_kyat, b.revision
FROM trip_category_budgets AS b
"""

_SPENDING_SELECT = """
SELECT s.id, s.description, s.category, s.category_budget_id,
       s.amount_kyat, s.spending_date, s.revision
FROM trip_spending_entries AS s
"""


@dataclass(frozen=True)
class TravelView:
    body: dict[str, Any]
    etag: str


def _etag(revision: int) -> str:
    return f'"{revision}"'


def _field_error(field: str, code: str, message: str) -> dict[str, str]:
    return {"field": field, "code": code, "message": message}


def _not_found(resource: str = "trip") -> ApiError:
    return ApiError(
        404,
        "not_found",
        f"The requested {resource} was not found.",
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
            "The supplied revision does not match the current record.",
        )
    return int(match.group(1))


def _safe_int(value: Decimal, *, label: str) -> int:
    if value != value.to_integral_value() or not (
        -JSON_SAFE_INTEGER_MAX <= value <= JSON_SAFE_INTEGER_MAX
    ):
        raise ApiError(
            500,
            "aggregate_out_of_range",
            f"The {label} cannot be represented safely.",
        )
    return int(value)


def _validate_spending_amount(
    amount_kyat: int,
    *,
    field: str = "amount_kyat",
) -> None:
    if amount_kyat <= 0:
        raise ApiError(
            422,
            "validation_error",
            "The trip spending amount must be a positive whole-Kyat integer.",
            field_errors=[
                _field_error(
                    field,
                    "amount_not_positive",
                    "Enter an amount greater than zero.",
                )
            ],
        )
    if amount_kyat > JSON_SAFE_INTEGER_MAX:
        raise ApiError(
            422,
            "amount_not_safe_integer",
            "The trip spending amount is outside the supported JSON integer range.",
            field_errors=[
                _field_error(
                    field,
                    "amount_not_safe_integer",
                    "The amount is too large to represent safely.",
                )
            ],
        )
    if amount_kyat > settings.max_financial_entry_kyat:
        raise ApiError(
            422,
            "amount_limit_exceeded",
            "The trip spending amount exceeds the individual-entry limit.",
            field_errors=[
                _field_error(
                    field,
                    "amount_limit_exceeded",
                    (
                        "The maximum amount for one entry is "
                        f"{settings.max_financial_entry_kyat} MMK."
                    ),
                )
            ],
        )


def _trip_resource(row: Mapping[str, Any]) -> dict[str, Any]:
    return {
        "id": str(row["id"]),
        "name": row["name"],
        "destination": row["destination"],
        "start_date": row["start_date"].isoformat(),
        "end_date": row["end_date"].isoformat(),
        "status": row["status"],
        "budget_kyat": _safe_int(
            Decimal(row["budget_kyat"]),
            label="trip planned total",
        ),
        "spending_kyat": _safe_int(
            Decimal(row["spending_kyat"]),
            label="trip spending total",
        ),
    }


def calculate_recognized_travel_expense(
    session: Session,
    *,
    user_id: UUID,
) -> int:
    """Return the travel expense recognized in account totals.

    An active trip reserves the greater of its planned budget and its actual
    spending, so the allocation is deducted once and actual spending above the
    plan adds only the excess. Completed or cancelled trips recognize actual
    spending only, which returns any unused allocation. Trips are never summed
    twice: the recognized amount is the aggregate of the per-trip rule.
    """
    row = session.execute(
        text(
            """
            SELECT COALESCE(SUM(
                CASE
                    WHEN totals.status = 'active'
                        THEN GREATEST(totals.budget_kyat, totals.spending_kyat)
                    ELSE totals.spending_kyat
                END
            ), 0) AS recognized
            FROM (
                SELECT
                    t.id,
                    t.status,
                    COALESCE((
                        SELECT SUM(b.budget_kyat::numeric)
                        FROM trip_category_budgets AS b
                        WHERE b.trip_id = t.id AND b.user_id = t.user_id
                    ), 0) AS budget_kyat,
                    COALESCE((
                        SELECT SUM(s.amount_kyat::numeric)
                        FROM trip_spending_entries AS s
                        WHERE s.trip_id = t.id AND s.user_id = t.user_id
                    ), 0) AS spending_kyat
                FROM trips AS t
                WHERE t.user_id = :user_id
            ) AS totals
            """
        ),
        {"user_id": user_id},
    ).mappings().one()
    return _safe_int(
        Decimal(row["recognized"]),
        label="recognized travel expense",
    )


def _budget_resource(row: Mapping[str, Any]) -> dict[str, Any]:
    return {
        "id": str(row["id"]),
        "category": row["category"],
        "budget_kyat": int(row["budget_kyat"]),
    }


def _spending_resource(row: Mapping[str, Any]) -> dict[str, Any]:
    return {
        "id": str(row["id"]),
        "description": row["description"],
        "category": row["category"],
        "category_budget_id": (
            str(row["category_budget_id"])
            if row["category_budget_id"] is not None
            else None
        ),
        "amount_kyat": int(row["amount_kyat"]),
        "spending_date": row["spending_date"].isoformat(),
    }


def _trip_row_for_user(
    session: Session,
    *,
    user_id: UUID,
    trip_id: UUID,
    for_update: bool = False,
) -> Mapping[str, Any] | None:
    statement = _TRIP_SELECT + (
        " WHERE t.user_id = :user_id AND t.id = :trip_id"
    )
    if for_update:
        statement += " FOR UPDATE OF t"
    return session.execute(
        text(statement),
        {"user_id": user_id, "trip_id": trip_id},
    ).mappings().first()


def _budget_row_for_user(
    session: Session,
    *,
    user_id: UUID,
    trip_id: UUID,
    budget_id: UUID,
    for_update: bool = False,
) -> Mapping[str, Any] | None:
    statement = _BUDGET_SELECT + (
        " WHERE b.user_id = :user_id AND b.trip_id = :trip_id"
        " AND b.id = :budget_id"
    )
    if for_update:
        statement += " FOR UPDATE OF b"
    return session.execute(
        text(statement),
        {
            "user_id": user_id,
            "trip_id": trip_id,
            "budget_id": budget_id,
        },
    ).mappings().first()


def _spending_row_for_user(
    session: Session,
    *,
    user_id: UUID,
    trip_id: UUID,
    spending_id: UUID,
    for_update: bool = False,
) -> Mapping[str, Any] | None:
    statement = _SPENDING_SELECT + (
        " WHERE s.user_id = :user_id AND s.trip_id = :trip_id"
        " AND s.id = :spending_id"
    )
    if for_update:
        statement += " FOR UPDATE OF s"
    return session.execute(
        text(statement),
        {
            "user_id": user_id,
            "trip_id": trip_id,
            "spending_id": spending_id,
        },
    ).mappings().first()


def _lock_trip(
    session: Session,
    *,
    context: AuthContext,
    trip_id: UUID,
) -> Mapping[str, Any]:
    row = _trip_row_for_user(
        session,
        user_id=context.user_id,
        trip_id=trip_id,
        for_update=True,
    )
    if row is None:
        raise _not_found()
    return row


def _bump_trip(
    session: Session,
    *,
    user_id: UUID,
    trip_id: UUID,
) -> None:
    session.execute(
        text(
            """
            UPDATE trips
            SET updated_at = now(), revision = revision + 1
            WHERE id = :trip_id AND user_id = :user_id
            """
        ),
        {"trip_id": trip_id, "user_id": user_id},
    )


def _run_idempotent_write(
    session: Session,
    *,
    context: AuthContext,
    method: str,
    route: str,
    idempotency_key: str,
    payload: dict[str, Any],
    operation,
) -> IdempotencyResult:
    try:
        result, _ = run_idempotent(
            session,
            scope=f"user:{context.user_id}",
            method=method,
            route=route,
            key=idempotency_key,
            payload=payload,
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


def _budget_category_exists(
    session: Session,
    *,
    user_id: UUID,
    trip_id: UUID,
    category: str,
    exclude_budget_id: UUID | None = None,
) -> bool:
    parameters: dict[str, Any] = {
        "user_id": user_id,
        "trip_id": trip_id,
        "category": category,
    }
    statement = (
        "SELECT 1 FROM trip_category_budgets"
        " WHERE user_id = :user_id AND trip_id = :trip_id"
        " AND lower(category) = lower(:category)"
    )
    if exclude_budget_id is not None:
        statement += " AND id <> :exclude_budget_id"
        parameters["exclude_budget_id"] = exclude_budget_id
    statement += " LIMIT 1"
    return session.execute(text(statement), parameters).first() is not None


def _validate_budget_reference(
    session: Session,
    *,
    context: AuthContext,
    trip_id: UUID,
    budget_id: UUID | None,
) -> None:
    if budget_id is None:
        return
    row = _budget_row_for_user(
        session,
        user_id=context.user_id,
        trip_id=trip_id,
        budget_id=budget_id,
    )
    if row is None:
        raise ApiError(
            422,
            "validation_error",
            "The selected planned expense does not belong to this trip.",
            field_errors=[
                _field_error(
                    "category_budget_id",
                    "invalid_budget_reference",
                    "Choose a planned expense from the same trip.",
                )
            ],
        )


def create_trip(
    session: Session,
    *,
    context: AuthContext,
    payload: TripCreateRequest,
    idempotency_key: str,
) -> IdempotencyResult:
    def operation() -> IdempotencyResult:
        trip_id = uuid4()
        row = session.execute(
            text(
                """
                INSERT INTO trips (
                    id, user_id, name, destination, start_date, end_date
                ) VALUES (
                    :id, :user_id, :name, :destination, :start_date, :end_date
                )
                RETURNING revision
                """
            ),
            {
                "id": trip_id,
                "user_id": context.user_id,
                "name": payload.name,
                "destination": payload.destination,
                "start_date": payload.start_date,
                "end_date": payload.end_date,
            },
        ).mappings().one()
        resource = {
            "id": str(trip_id),
            "name": payload.name,
            "destination": payload.destination,
            "start_date": payload.start_date.isoformat(),
            "end_date": payload.end_date.isoformat(),
            "status": "active",
            "budget_kyat": 0,
            "spending_kyat": 0,
        }
        return IdempotencyResult(
            status_code=201,
            body={"data": resource},
            headers={"ETag": _etag(int(row["revision"]))},
        )

    return _run_idempotent_write(
        session,
        context=context,
        method="POST",
        route="/api/v1/trips",
        idempotency_key=idempotency_key,
        payload=payload.model_dump(mode="json"),
        operation=operation,
    )


def list_trips(
    session: Session,
    *,
    context: AuthContext,
) -> dict[str, Any]:
    rows = session.execute(
        text(
            _TRIP_SELECT
            + " WHERE t.user_id = :user_id AND t.status <> 'cancelled'"
            + " ORDER BY t.start_date DESC, t.created_at DESC, t.id DESC"
        ),
        {"user_id": context.user_id},
    ).mappings().all()
    return {"data": [_trip_resource(row) for row in rows]}


def get_trip(
    session: Session,
    *,
    context: AuthContext,
    trip_id: UUID,
) -> TravelView:
    row = _trip_row_for_user(
        session,
        user_id=context.user_id,
        trip_id=trip_id,
    )
    if row is None:
        raise _not_found()
    return TravelView(
        body={"data": _trip_resource(row)},
        etag=_etag(int(row["revision"])),
    )


def update_trip(
    session: Session,
    *,
    context: AuthContext,
    trip_id: UUID,
    payload: TripUpdateRequest,
    if_match: str | None,
) -> TravelView:
    expected_revision = _parse_if_match(if_match)
    changed = payload.model_fields_set

    try:
        row = _lock_trip(session, context=context, trip_id=trip_id)
        if int(row["revision"]) != expected_revision:
            raise ApiError(
                412,
                "stale_revision",
                "The supplied revision does not match the current trip.",
            )

        final_name = payload.name if "name" in changed else row["name"]
        final_destination = (
            payload.destination
            if "destination" in changed
            else row["destination"]
        )
        final_start = (
            payload.start_date
            if "start_date" in changed
            else row["start_date"]
        )
        final_end = (
            payload.end_date if "end_date" in changed else row["end_date"]
        )
        if final_end < final_start:
            raise ApiError(
                422,
                "validation_error",
                "The return date cannot be before the departure date.",
                field_errors=[
                    _field_error(
                        "end_date",
                        "date_order",
                        "The return date cannot be before the departure date.",
                    )
                ],
            )

        updated = session.execute(
            text(
                """
                UPDATE trips
                SET name = :name,
                    destination = :destination,
                    start_date = :start_date,
                    end_date = :end_date,
                    updated_at = now(),
                    revision = revision + 1
                WHERE id = :trip_id
                  AND user_id = :user_id
                  AND revision = :expected_revision
                RETURNING revision
                """
            ),
            {
                "name": final_name,
                "destination": final_destination,
                "start_date": final_start,
                "end_date": final_end,
                "trip_id": trip_id,
                "user_id": context.user_id,
                "expected_revision": expected_revision,
            },
        ).mappings().first()
        if updated is None:
            raise ApiError(
                412,
                "stale_revision",
                "The supplied revision does not match the current trip.",
            )
        session.commit()
    except ApiError:
        session.rollback()
        raise
    except SQLAlchemyError:
        session.rollback()
        raise

    resource = {
        "id": str(row["id"]),
        "name": final_name,
        "destination": final_destination,
        "start_date": final_start.isoformat(),
        "end_date": final_end.isoformat(),
        "status": row["status"],
        "budget_kyat": _safe_int(
            Decimal(row["budget_kyat"]),
            label="trip planned total",
        ),
        "spending_kyat": _safe_int(
            Decimal(row["spending_kyat"]),
            label="trip spending total",
        ),
    }
    return TravelView(
        body={"data": resource},
        etag=_etag(int(updated["revision"])),
    )


def complete_trip(
    session: Session,
    *,
    context: AuthContext,
    trip_id: UUID,
    if_match: str | None,
    idempotency_key: str,
) -> IdempotencyResult:
    normalized_if_match = if_match.strip() if if_match is not None else None

    def operation() -> IdempotencyResult:
        expected_revision = _parse_if_match(normalized_if_match)
        row = _lock_trip(session, context=context, trip_id=trip_id)
        if row["status"] == "completed":
            # Repeated completion is a no-op; recognition is derived from the
            # trip status, so there is no second accounting entry to create.
            return IdempotencyResult(
                status_code=200,
                body={"data": _trip_resource(row)},
                headers={"ETag": _etag(int(row["revision"]))},
            )
        if row["status"] == "cancelled":
            raise ApiError(
                409,
                "trip_cancelled",
                "A cancelled trip cannot be completed.",
            )
        if int(row["revision"]) != expected_revision:
            raise ApiError(
                412,
                "stale_revision",
                "The supplied revision does not match the current trip.",
            )
        updated = session.execute(
            text(
                """
                UPDATE trips
                SET status = 'completed',
                    updated_at = now(),
                    revision = revision + 1
                WHERE id = :trip_id AND user_id = :user_id
                RETURNING revision
                """
            ),
            {"trip_id": trip_id, "user_id": context.user_id},
        ).mappings().one()
        completed = dict(row)
        completed["status"] = "completed"
        return IdempotencyResult(
            status_code=200,
            body={"data": _trip_resource(completed)},
            headers={"ETag": _etag(int(updated["revision"]))},
        )

    return _run_idempotent_write(
        session,
        context=context,
        method="POST",
        route=f"/api/v1/trips/{trip_id}/complete",
        idempotency_key=idempotency_key,
        payload={"if_match": normalized_if_match},
        operation=operation,
    )


def delete_trip(
    session: Session,
    *,
    context: AuthContext,
    trip_id: UUID,
    if_match: str | None,
    idempotency_key: str,
) -> IdempotencyResult:
    normalized_if_match = if_match.strip() if if_match is not None else None

    def operation() -> IdempotencyResult:
        expected_revision = _parse_if_match(normalized_if_match)
        row = _lock_trip(session, context=context, trip_id=trip_id)
        if row["status"] == "cancelled":
            return IdempotencyResult(status_code=204, body={}, headers={})
        if int(row["revision"]) != expected_revision:
            raise ApiError(
                412,
                "stale_revision",
                "The supplied revision does not match the current trip.",
            )
        # Deleting is a cancellation: the unused allocation is released, but
        # real spending history is preserved so genuine expenses are not
        # erased from financial totals.
        session.execute(
            text(
                """
                UPDATE trips
                SET status = 'cancelled',
                    updated_at = now(),
                    revision = revision + 1
                WHERE id = :trip_id AND user_id = :user_id
                """
            ),
            {"trip_id": trip_id, "user_id": context.user_id},
        )
        return IdempotencyResult(status_code=204, body={}, headers={})

    return _run_idempotent_write(
        session,
        context=context,
        method="DELETE",
        route=f"/api/v1/trips/{trip_id}",
        idempotency_key=idempotency_key,
        payload={"if_match": normalized_if_match},
        operation=operation,
    )


def list_budgets(
    session: Session,
    *,
    context: AuthContext,
    trip_id: UUID,
) -> dict[str, Any]:
    trip = _trip_row_for_user(
        session,
        user_id=context.user_id,
        trip_id=trip_id,
    )
    if trip is None:
        raise _not_found()
    rows = session.execute(
        text(
            _BUDGET_SELECT
            + " WHERE b.user_id = :user_id AND b.trip_id = :trip_id"
            + " ORDER BY lower(b.category) ASC, b.id ASC"
        ),
        {"user_id": context.user_id, "trip_id": trip_id},
    ).mappings().all()
    return {"data": [_budget_resource(row) for row in rows]}


def get_budget(
    session: Session,
    *,
    context: AuthContext,
    trip_id: UUID,
    budget_id: UUID,
) -> TravelView:
    row = _budget_row_for_user(
        session,
        user_id=context.user_id,
        trip_id=trip_id,
        budget_id=budget_id,
    )
    if row is None:
        raise _not_found("planned expense")
    return TravelView(
        body={"data": _budget_resource(row)},
        etag=_etag(int(row["revision"])),
    )


def create_budget(
    session: Session,
    *,
    context: AuthContext,
    trip_id: UUID,
    payload: TripBudgetCreateRequest,
    idempotency_key: str,
) -> IdempotencyResult:
    def operation() -> IdempotencyResult:
        _lock_trip(session, context=context, trip_id=trip_id)
        if _budget_category_exists(
            session,
            user_id=context.user_id,
            trip_id=trip_id,
            category=payload.category,
        ):
            raise ApiError(
                409,
                "budget_category_exists",
                "This trip already has a planned expense with that name.",
                field_errors=[
                    _field_error(
                        "category",
                        "budget_category_exists",
                        "Use a different planned-expense name.",
                    )
                ],
            )
        budget_id = uuid4()
        row = session.execute(
            text(
                """
                INSERT INTO trip_category_budgets (
                    id, user_id, trip_id, category, budget_kyat
                ) VALUES (
                    :id, :user_id, :trip_id, :category, :budget_kyat
                )
                RETURNING revision
                """
            ),
            {
                "id": budget_id,
                "user_id": context.user_id,
                "trip_id": trip_id,
                "category": payload.category,
                "budget_kyat": payload.budget_kyat,
            },
        ).mappings().one()
        _bump_trip(
            session,
            user_id=context.user_id,
            trip_id=trip_id,
        )
        resource = {
            "id": str(budget_id),
            "category": payload.category,
            "budget_kyat": payload.budget_kyat,
        }
        return IdempotencyResult(
            status_code=201,
            body={"data": resource},
            headers={"ETag": _etag(int(row["revision"]))},
        )

    return _run_idempotent_write(
        session,
        context=context,
        method="POST",
        route=f"/api/v1/trips/{trip_id}/budgets",
        idempotency_key=idempotency_key,
        payload=payload.model_dump(mode="json"),
        operation=operation,
    )


def update_budget(
    session: Session,
    *,
    context: AuthContext,
    trip_id: UUID,
    budget_id: UUID,
    payload: TripBudgetUpdateRequest,
    if_match: str | None,
) -> TravelView:
    expected_revision = _parse_if_match(if_match)
    changed = payload.model_fields_set

    try:
        _lock_trip(session, context=context, trip_id=trip_id)
        row = _budget_row_for_user(
            session,
            user_id=context.user_id,
            trip_id=trip_id,
            budget_id=budget_id,
            for_update=True,
        )
        if row is None:
            raise _not_found("planned expense")
        if int(row["revision"]) != expected_revision:
            raise ApiError(
                412,
                "stale_revision",
                "The supplied revision does not match the current planned expense.",
            )

        final_category = (
            payload.category if "category" in changed else row["category"]
        )
        final_amount = (
            payload.budget_kyat
            if "budget_kyat" in changed
            else int(row["budget_kyat"])
        )
        category_changed = final_category != row["category"]

        if category_changed and _budget_category_exists(
            session,
            user_id=context.user_id,
            trip_id=trip_id,
            category=final_category,
            exclude_budget_id=budget_id,
        ):
            raise ApiError(
                409,
                "budget_category_exists",
                "This trip already has a planned expense with that name.",
                field_errors=[
                    _field_error(
                        "category",
                        "budget_category_exists",
                        "Use a different planned-expense name.",
                    )
                ],
            )

        updated = session.execute(
            text(
                """
                UPDATE trip_category_budgets
                SET category = :category,
                    budget_kyat = :budget_kyat,
                    updated_at = now(),
                    revision = revision + 1
                WHERE id = :budget_id
                  AND user_id = :user_id
                  AND trip_id = :trip_id
                  AND revision = :expected_revision
                RETURNING revision
                """
            ),
            {
                "category": final_category,
                "budget_kyat": final_amount,
                "budget_id": budget_id,
                "user_id": context.user_id,
                "trip_id": trip_id,
                "expected_revision": expected_revision,
            },
        ).mappings().first()
        if updated is None:
            raise ApiError(
                412,
                "stale_revision",
                "The supplied revision does not match the current planned expense.",
            )

        if category_changed:
            session.execute(
                text(
                    """
                    UPDATE trip_spending_entries
                    SET category = :category,
                        updated_at = now(),
                        revision = revision + 1
                    WHERE user_id = :user_id
                      AND trip_id = :trip_id
                      AND category_budget_id = :budget_id
                    """
                ),
                {
                    "category": final_category,
                    "user_id": context.user_id,
                    "trip_id": trip_id,
                    "budget_id": budget_id,
                },
            )

        _bump_trip(
            session,
            user_id=context.user_id,
            trip_id=trip_id,
        )
        session.commit()
    except ApiError:
        session.rollback()
        raise
    except SQLAlchemyError:
        session.rollback()
        raise

    return TravelView(
        body={
            "data": {
                "id": str(row["id"]),
                "category": final_category,
                "budget_kyat": final_amount,
            }
        },
        etag=_etag(int(updated["revision"])),
    )


def delete_budget(
    session: Session,
    *,
    context: AuthContext,
    trip_id: UUID,
    budget_id: UUID,
    if_match: str | None,
    idempotency_key: str,
) -> IdempotencyResult:
    normalized_if_match = if_match.strip() if if_match is not None else None

    def operation() -> IdempotencyResult:
        expected_revision = _parse_if_match(normalized_if_match)
        _lock_trip(session, context=context, trip_id=trip_id)
        row = _budget_row_for_user(
            session,
            user_id=context.user_id,
            trip_id=trip_id,
            budget_id=budget_id,
            for_update=True,
        )
        if row is None:
            raise _not_found("planned expense")
        if int(row["revision"]) != expected_revision:
            raise ApiError(
                412,
                "stale_revision",
                "The supplied revision does not match the current planned expense.",
            )

        # Preserve actual spending and its category snapshot; only detach it.
        session.execute(
            text(
                """
                UPDATE trip_spending_entries
                SET category_budget_id = NULL,
                    updated_at = now(),
                    revision = revision + 1
                WHERE user_id = :user_id
                  AND trip_id = :trip_id
                  AND category_budget_id = :budget_id
                """
            ),
            {
                "user_id": context.user_id,
                "trip_id": trip_id,
                "budget_id": budget_id,
            },
        )
        session.execute(
            text(
                """
                DELETE FROM trip_category_budgets
                WHERE id = :budget_id
                  AND user_id = :user_id
                  AND trip_id = :trip_id
                """
            ),
            {
                "budget_id": budget_id,
                "user_id": context.user_id,
                "trip_id": trip_id,
            },
        )
        _bump_trip(
            session,
            user_id=context.user_id,
            trip_id=trip_id,
        )
        return IdempotencyResult(status_code=204, body={}, headers={})

    return _run_idempotent_write(
        session,
        context=context,
        method="DELETE",
        route=f"/api/v1/trips/{trip_id}/budgets/{budget_id}",
        idempotency_key=idempotency_key,
        payload={"if_match": normalized_if_match},
        operation=operation,
    )


def list_spending(
    session: Session,
    *,
    context: AuthContext,
    trip_id: UUID,
) -> dict[str, Any]:
    trip = _trip_row_for_user(
        session,
        user_id=context.user_id,
        trip_id=trip_id,
    )
    if trip is None:
        raise _not_found()
    rows = session.execute(
        text(
            _SPENDING_SELECT
            + " WHERE s.user_id = :user_id AND s.trip_id = :trip_id"
            + " ORDER BY s.spending_date DESC, s.created_at DESC, s.id DESC"
        ),
        {"user_id": context.user_id, "trip_id": trip_id},
    ).mappings().all()
    return {"data": [_spending_resource(row) for row in rows]}


def get_spending(
    session: Session,
    *,
    context: AuthContext,
    trip_id: UUID,
    spending_id: UUID,
) -> TravelView:
    row = _spending_row_for_user(
        session,
        user_id=context.user_id,
        trip_id=trip_id,
        spending_id=spending_id,
    )
    if row is None:
        raise _not_found("trip spending entry")
    return TravelView(
        body={"data": _spending_resource(row)},
        etag=_etag(int(row["revision"])),
    )


def create_spending(
    session: Session,
    *,
    context: AuthContext,
    trip_id: UUID,
    payload: TripSpendingCreateRequest,
    idempotency_key: str,
) -> IdempotencyResult:
    def operation() -> IdempotencyResult:
        _validate_spending_amount(payload.amount_kyat)
        _lock_trip(session, context=context, trip_id=trip_id)
        _validate_budget_reference(
            session,
            context=context,
            trip_id=trip_id,
            budget_id=payload.category_budget_id,
        )
        spending_id = uuid4()
        row = session.execute(
            text(
                """
                INSERT INTO trip_spending_entries (
                    id, user_id, trip_id, category_budget_id, category,
                    description, amount_kyat, spending_date
                ) VALUES (
                    :id, :user_id, :trip_id, :category_budget_id, :category,
                    :description, :amount_kyat, :spending_date
                )
                RETURNING revision
                """
            ),
            {
                "id": spending_id,
                "user_id": context.user_id,
                "trip_id": trip_id,
                "category_budget_id": payload.category_budget_id,
                "category": payload.category,
                "description": payload.description,
                "amount_kyat": payload.amount_kyat,
                "spending_date": payload.spending_date,
            },
        ).mappings().one()
        _bump_trip(
            session,
            user_id=context.user_id,
            trip_id=trip_id,
        )
        resource = {
            "id": str(spending_id),
            "description": payload.description,
            "category": payload.category,
            "category_budget_id": (
                str(payload.category_budget_id)
                if payload.category_budget_id is not None
                else None
            ),
            "amount_kyat": payload.amount_kyat,
            "spending_date": payload.spending_date.isoformat(),
        }
        return IdempotencyResult(
            status_code=201,
            body={"data": resource},
            headers={"ETag": _etag(int(row["revision"]))},
        )

    return _run_idempotent_write(
        session,
        context=context,
        method="POST",
        route=f"/api/v1/trips/{trip_id}/spending",
        idempotency_key=idempotency_key,
        payload=payload.model_dump(mode="json"),
        operation=operation,
    )


def update_spending(
    session: Session,
    *,
    context: AuthContext,
    trip_id: UUID,
    spending_id: UUID,
    payload: TripSpendingUpdateRequest,
    if_match: str | None,
) -> TravelView:
    expected_revision = _parse_if_match(if_match)
    changed = payload.model_fields_set

    try:
        _lock_trip(session, context=context, trip_id=trip_id)
        row = _spending_row_for_user(
            session,
            user_id=context.user_id,
            trip_id=trip_id,
            spending_id=spending_id,
            for_update=True,
        )
        if row is None:
            raise _not_found("trip spending entry")
        if int(row["revision"]) != expected_revision:
            raise ApiError(
                412,
                "stale_revision",
                "The supplied revision does not match the current spending entry.",
            )

        final_description = (
            payload.description
            if "description" in changed
            else row["description"]
        )
        final_category = (
            payload.category if "category" in changed else row["category"]
        )
        final_amount = (
            payload.amount_kyat
            if "amount_kyat" in changed
            else int(row["amount_kyat"])
        )
        final_date = (
            payload.spending_date
            if "spending_date" in changed
            else row["spending_date"]
        )
        final_budget_id = (
            payload.category_budget_id
            if "category_budget_id" in changed
            else row["category_budget_id"]
        )
        _validate_spending_amount(final_amount)
        if "category_budget_id" in changed:
            _validate_budget_reference(
                session,
                context=context,
                trip_id=trip_id,
                budget_id=final_budget_id,
            )

        updated = session.execute(
            text(
                """
                UPDATE trip_spending_entries
                SET description = :description,
                    category = :category,
                    amount_kyat = :amount_kyat,
                    spending_date = :spending_date,
                    category_budget_id = :category_budget_id,
                    updated_at = now(),
                    revision = revision + 1
                WHERE id = :spending_id
                  AND user_id = :user_id
                  AND trip_id = :trip_id
                  AND revision = :expected_revision
                RETURNING revision
                """
            ),
            {
                "description": final_description,
                "category": final_category,
                "amount_kyat": final_amount,
                "spending_date": final_date,
                "category_budget_id": final_budget_id,
                "spending_id": spending_id,
                "user_id": context.user_id,
                "trip_id": trip_id,
                "expected_revision": expected_revision,
            },
        ).mappings().first()
        if updated is None:
            raise ApiError(
                412,
                "stale_revision",
                "The supplied revision does not match the current spending entry.",
            )

        _bump_trip(
            session,
            user_id=context.user_id,
            trip_id=trip_id,
        )
        session.commit()
    except ApiError:
        session.rollback()
        raise
    except SQLAlchemyError:
        session.rollback()
        raise

    return TravelView(
        body={
            "data": {
                "id": str(row["id"]),
                "description": final_description,
                "category": final_category,
                "category_budget_id": (
                    str(final_budget_id)
                    if final_budget_id is not None
                    else None
                ),
                "amount_kyat": final_amount,
                "spending_date": final_date.isoformat(),
            }
        },
        etag=_etag(int(updated["revision"])),
    )


def delete_spending(
    session: Session,
    *,
    context: AuthContext,
    trip_id: UUID,
    spending_id: UUID,
    if_match: str | None,
    idempotency_key: str,
) -> IdempotencyResult:
    normalized_if_match = if_match.strip() if if_match is not None else None

    def operation() -> IdempotencyResult:
        expected_revision = _parse_if_match(normalized_if_match)
        _lock_trip(session, context=context, trip_id=trip_id)
        row = _spending_row_for_user(
            session,
            user_id=context.user_id,
            trip_id=trip_id,
            spending_id=spending_id,
            for_update=True,
        )
        if row is None:
            raise _not_found("trip spending entry")
        if int(row["revision"]) != expected_revision:
            raise ApiError(
                412,
                "stale_revision",
                "The supplied revision does not match the current spending entry.",
            )
        session.execute(
            text(
                """
                DELETE FROM trip_spending_entries
                WHERE id = :spending_id
                  AND user_id = :user_id
                  AND trip_id = :trip_id
                """
            ),
            {
                "spending_id": spending_id,
                "user_id": context.user_id,
                "trip_id": trip_id,
            },
        )
        _bump_trip(
            session,
            user_id=context.user_id,
            trip_id=trip_id,
        )
        return IdempotencyResult(status_code=204, body={}, headers={})

    return _run_idempotent_write(
        session,
        context=context,
        method="DELETE",
        route=f"/api/v1/trips/{trip_id}/spending/{spending_id}",
        idempotency_key=idempotency_key,
        payload={"if_match": normalized_if_match},
        operation=operation,
    )


def import_trips(
    session: Session,
    *,
    context: AuthContext,
    payload: TripImportRequest,
    idempotency_key: str,
) -> IdempotencyResult:
    def operation() -> IdempotencyResult:
        # Serialize imports per user so duplicate detection cannot race between
        # two different idempotency keys.
        session.execute(
            text("SELECT pg_advisory_xact_lock(hashtextextended(:scope, 0))"),
            {"scope": f"travel-import:{context.user_id}"},
        )
        counts = {
            "trips_created": 0,
            "trips_skipped_duplicates": 0,
            "budgets_created": 0,
            "budgets_skipped_duplicates": 0,
            "spending_created": 0,
            "spending_skipped_duplicates": 0,
        }

        for trip_index, trip in enumerate(payload.trips):
            existing_trip = session.execute(
                text(
                    """
                    SELECT id
                    FROM trips
                    WHERE user_id = :user_id
                      AND lower(name) = lower(:name)
                      AND lower(destination) = lower(:destination)
                      AND start_date = :start_date
                      AND end_date = :end_date
                    LIMIT 1
                    """
                ),
                {
                    "user_id": context.user_id,
                    "name": trip.name,
                    "destination": trip.destination,
                    "start_date": trip.start_date,
                    "end_date": trip.end_date,
                },
            ).mappings().first()

            if existing_trip is None:
                trip_id = uuid4()
                session.execute(
                    text(
                        """
                        INSERT INTO trips (
                            id, user_id, name, destination,
                            start_date, end_date
                        ) VALUES (
                            :id, :user_id, :name, :destination,
                            :start_date, :end_date
                        )
                        """
                    ),
                    {
                        "id": trip_id,
                        "user_id": context.user_id,
                        "name": trip.name,
                        "destination": trip.destination,
                        "start_date": trip.start_date,
                        "end_date": trip.end_date,
                    },
                )
                counts["trips_created"] += 1
            else:
                trip_id = existing_trip["id"]
                counts["trips_skipped_duplicates"] += 1

            budget_rows = session.execute(
                text(
                    """
                    SELECT id, category
                    FROM trip_category_budgets
                    WHERE user_id = :user_id AND trip_id = :trip_id
                    """
                ),
                {"user_id": context.user_id, "trip_id": trip_id},
            ).mappings().all()
            budgets_by_category = {
                row["category"].strip().lower(): row["id"]
                for row in budget_rows
            }

            created_for_trip = False
            for budget in trip.budgets:
                category_key = budget.category.strip().lower()
                if category_key in budgets_by_category:
                    counts["budgets_skipped_duplicates"] += 1
                    continue
                budget_id = uuid4()
                session.execute(
                    text(
                        """
                        INSERT INTO trip_category_budgets (
                            id, user_id, trip_id, category, budget_kyat
                        ) VALUES (
                            :id, :user_id, :trip_id, :category, :budget_kyat
                        )
                        """
                    ),
                    {
                        "id": budget_id,
                        "user_id": context.user_id,
                        "trip_id": trip_id,
                        "category": budget.category,
                        "budget_kyat": budget.budget_kyat,
                    },
                )
                budgets_by_category[category_key] = budget_id
                counts["budgets_created"] += 1
                created_for_trip = True

            for spending_index, spending in enumerate(trip.spending):
                _validate_spending_amount(
                    spending.amount_kyat,
                    field=(
                        f"trips[{trip_index}].spending"
                        f"[{spending_index}].amount_kyat"
                    ),
                )

                budget_id: UUID | None = None
                if spending.budget_category is not None:
                    budget_id = budgets_by_category.get(
                        spending.budget_category.strip().lower()
                    )
                    if budget_id is None:
                        raise ApiError(
                            422,
                            "validation_error",
                            (
                                "The imported spending entry references a "
                                "planned expense that is not part of the trip."
                            ),
                            field_errors=[
                                _field_error(
                                    (
                                        f"trips[{trip_index}].spending"
                                        f"[{spending_index}].budget_category"
                                    ),
                                    "invalid_budget_reference",
                                    (
                                        "Add the planned expense to this trip "
                                        "or remove the budget reference."
                                    ),
                                )
                            ],
                        )

                duplicate = session.execute(
                    text(
                        """
                        SELECT 1
                        FROM trip_spending_entries
                        WHERE user_id = :user_id
                          AND trip_id = :trip_id
                          AND lower(description) = lower(:description)
                          AND lower(category) = lower(:category)
                          AND amount_kyat = :amount_kyat
                          AND spending_date = :spending_date
                        LIMIT 1
                        """
                    ),
                    {
                        "user_id": context.user_id,
                        "trip_id": trip_id,
                        "description": spending.description,
                        "category": spending.category,
                        "amount_kyat": spending.amount_kyat,
                        "spending_date": spending.spending_date,
                    },
                ).first()
                if duplicate is not None:
                    counts["spending_skipped_duplicates"] += 1
                    continue

                session.execute(
                    text(
                        """
                        INSERT INTO trip_spending_entries (
                            id, user_id, trip_id, category_budget_id,
                            category, description, amount_kyat, spending_date
                        ) VALUES (
                            :id, :user_id, :trip_id, :category_budget_id,
                            :category, :description, :amount_kyat,
                            :spending_date
                        )
                        """
                    ),
                    {
                        "id": uuid4(),
                        "user_id": context.user_id,
                        "trip_id": trip_id,
                        "category_budget_id": budget_id,
                        "category": spending.category,
                        "description": spending.description,
                        "amount_kyat": spending.amount_kyat,
                        "spending_date": spending.spending_date,
                    },
                )
                counts["spending_created"] += 1
                created_for_trip = True

            if existing_trip is not None and created_for_trip:
                _bump_trip(
                    session,
                    user_id=context.user_id,
                    trip_id=trip_id,
                )

        return IdempotencyResult(
            status_code=200,
            body={"data": counts},
            headers={},
        )

    return _run_idempotent_write(
        session,
        context=context,
        method="POST",
        route="/api/v1/trips/import",
        idempotency_key=idempotency_key,
        payload=payload.model_dump(mode="json"),
        operation=operation,
    )
