"""Owner-scoped savings goal and contribution services.

All SQL runs through the normal runtime identity (``finpath_app``) and is scoped
by the authenticated user's ID. Client-supplied ownership fields are never
accepted.
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
from app.schemas.savings import (
    SavingsContributionCreateRequest,
    SavingsGoalArchiveRequest,
    SavingsGoalCreateRequest,
    SavingsGoalStatusFilter,
    SavingsGoalUpdateRequest,
    SavingsWithdrawalCreateRequest,
)
from app.services.auth import AuthContext
from app.services.idempotency import IdempotencyResult, run_idempotent

JSON_SAFE_INTEGER_MAX = 9_007_199_254_740_991

# The Pages edge may weaken the server's strong ETag to W/"N" in transit
# while preserving the revision. Accept both forms and compare numerically.
_IF_MATCH_PATTERN = re.compile(r'^(?:W/)?"([0-9]+)"$')

_GOAL_SELECT = """
SELECT g.id, g.name, g.target_kyat, g.tone, g.status, g.created_at,
       g.updated_at, g.revision,
       COALESCE((
           SELECT SUM(
               CASE WHEN e.entry_type = 'contribution'
                    THEN e.amount_kyat::numeric
                    ELSE -e.amount_kyat::numeric
               END
           )
           FROM savings_entries AS e
           WHERE e.goal_id = g.id
             AND e.user_id = g.user_id
       ), 0) AS actual_balance
FROM savings_goals AS g
"""

_GOAL_LOCK_SELECT = """
SELECT g.id, g.name, g.target_kyat, g.tone, g.status, g.created_at,
       g.updated_at, g.revision
FROM savings_goals AS g
"""

_ENTRY_SELECT = """
SELECT e.id, e.entry_type, e.amount_kyat, e.entry_date, e.note,
       e.plan_day_id, e.correction_of_entry_id, e.correction_reason,
       e.created_at
FROM savings_entries AS e
"""


@dataclass(frozen=True)
class SavingsGoalView:
    body: dict[str, Any]
    etag: str


def _etag(revision: int) -> str:
    return f'"{revision}"'


def _field_error(field: str, code: str, message: str) -> dict[str, str]:
    return {"field": field, "code": code, "message": message}


def _not_found(resource: str = "savings goal") -> ApiError:
    return ApiError(
        404,
        "not_found",
        f"The requested {resource} was not found.",
    )


def _goal_archived() -> ApiError:
    return ApiError(
        409,
        "goal_archived",
        "This savings goal is archived and cannot accept changes.",
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
            "The supplied revision does not match the current savings goal.",
        )
    return int(match.group(1))


def _validate_target(target_kyat: int) -> None:
    if target_kyat <= 0:
        raise ApiError(
            422,
            "validation_error",
            "The savings target must be a positive whole-Kyat integer.",
            field_errors=[
                _field_error(
                    "target_kyat",
                    "target_not_positive",
                    "Enter a target greater than zero.",
                )
            ],
        )
    if target_kyat > JSON_SAFE_INTEGER_MAX:
        raise ApiError(
            422,
            "amount_not_safe_integer",
            "The savings target is outside the supported JSON integer range.",
            field_errors=[
                _field_error(
                    "target_kyat",
                    "amount_not_safe_integer",
                    "The target is too large to represent safely.",
                )
            ],
        )


def _validate_contribution_amount(amount_kyat: int) -> None:
    if amount_kyat <= 0:
        raise ApiError(
            422,
            "validation_error",
            "The savings contribution amount must be a positive whole-Kyat integer.",
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
            "The savings contribution amount is outside the supported JSON integer range.",
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
            "The savings contribution amount exceeds the individual-entry limit.",
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


def _validate_withdrawal_amount(amount_kyat: int) -> None:
    if amount_kyat <= 0:
        raise ApiError(
            422,
            "validation_error",
            "The savings withdrawal amount must be a positive whole-Kyat integer.",
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
            "The savings withdrawal amount is outside the supported JSON integer range.",
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
            "The savings withdrawal amount exceeds the individual-entry limit.",
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


def _goal_resource(row: Mapping[str, Any]) -> dict[str, Any]:
    target = Decimal(row["target_kyat"])
    actual = Decimal(row["actual_balance"])
    remaining = max(Decimal(0), target - actual)
    return {
        "id": str(row["id"]),
        "name": row["name"],
        "target_kyat": _safe_int(target, label="savings target"),
        "tone": row["tone"],
        "status": row["status"],
        "actual_balance_kyat": _safe_int(
            actual, label="savings balance"
        ),
        "remaining_kyat": _safe_int(remaining, label="savings remaining"),
        "created_at": row["created_at"].isoformat(),
        "updated_at": row["updated_at"].isoformat(),
    }


def _entry_resource(row: Mapping[str, Any]) -> dict[str, Any]:
    return {
        "id": str(row["id"]),
        "entry_type": row["entry_type"],
        "amount_kyat": int(row["amount_kyat"]),
        "entry_date": row["entry_date"].isoformat(),
        "note": row["note"],
        "plan_day_id": (
            str(row["plan_day_id"]) if row["plan_day_id"] is not None else None
        ),
        "correction_of_entry_id": (
            str(row["correction_of_entry_id"])
            if row["correction_of_entry_id"] is not None
            else None
        ),
        "correction_reason": row["correction_reason"],
        "created_at": row["created_at"].isoformat(),
    }


def _actual_balance(
    session: Session,
    *,
    user_id: UUID,
    goal_id: UUID,
) -> Decimal:
    row = session.execute(
        text(
            """
            SELECT COALESCE(SUM(
                       CASE WHEN e.entry_type = 'contribution'
                            THEN e.amount_kyat::numeric
                            ELSE -e.amount_kyat::numeric
                       END
                   ), 0) AS total
            FROM savings_entries AS e
            WHERE e.user_id = :user_id
              AND e.goal_id = :goal_id
            """
        ),
        {"user_id": user_id, "goal_id": goal_id},
    ).mappings().one()
    return Decimal(row["total"])


def calculate_recognized_savings_allocation(
    session: Session,
    *,
    user_id: UUID,
) -> int:
    """Return the savings amount reserved from the available balance.

    ``savings_goals.target_kyat`` is the allocation, not an expense. Creating
    or increasing a target reserves cash immediately; contributions move cash
    inside an already-reserved bucket and therefore do not deduct it again.
    Contributions above the target are new reservations and add only the
    excess. Withdrawals return cash to the available balance. A target
    reduction releases only the un-contributed remainder; it cannot return cash
    already recorded as actual savings. Archiving or cancelling a goal releases
    the un-contributed remainder ``max(0, target - actual)`` while the actual
    savings balance stays reserved; the goal can be restored to reserve that
    remainder again.

    Equivalently, an active goal reserves
    ``max(target, contributions) - withdrawals`` and an archived goal reserves
    its actual balance ``contributions - withdrawals``. The aggregate is
    checked before it is returned. Like recognized travel, this allocation
    layer is intentionally not filtered by the financial-settings effective
    date; the target is current state rather than a dated cash movement.
    """
    row = session.execute(
        text(
            """
            SELECT COALESCE(SUM(
                CASE
                    WHEN totals.status = 'active'
                        THEN GREATEST(
                            totals.target_kyat,
                            totals.contributed_kyat
                        ) - totals.withdrawn_kyat
                    ELSE GREATEST(
                        totals.contributed_kyat - totals.withdrawn_kyat,
                        0
                    )
                END
            ), 0) AS recognized
            FROM (
                SELECT
                    g.id,
                    g.status,
                    g.target_kyat::numeric AS target_kyat,
                    COALESCE((
                        SELECT SUM(e.amount_kyat::numeric)
                        FROM savings_entries AS e
                        WHERE e.goal_id = g.id
                          AND e.user_id = g.user_id
                          AND e.entry_type = 'contribution'
                    ), 0) AS contributed_kyat,
                    COALESCE((
                        SELECT SUM(e.amount_kyat::numeric)
                        FROM savings_entries AS e
                        WHERE e.goal_id = g.id
                          AND e.user_id = g.user_id
                          AND e.entry_type = 'withdrawal'
                    ), 0) AS withdrawn_kyat
                FROM savings_goals AS g
                WHERE g.user_id = :user_id
            ) AS totals
            """
        ),
        {"user_id": user_id},
    ).mappings().one()
    return _safe_int(
        Decimal(row["recognized"]),
        label="recognized savings allocation",
    )


def _goal_row_for_user(
    session: Session,
    *,
    user_id: UUID,
    goal_id: UUID,
    for_update: bool = False,
) -> Mapping[str, Any] | None:
    if for_update:
        # Lock the parent goal first, then compute the derived balance in a new
        # statement so it reflects entries committed before the lock.
        row = session.execute(
            text(
                _GOAL_LOCK_SELECT
                + " WHERE g.user_id = :user_id AND g.id = :goal_id"
                + " FOR UPDATE OF g"
            ),
            {"user_id": user_id, "goal_id": goal_id},
        ).mappings().first()
        if row is None:
            return None
        result = dict(row)
        result["actual_balance"] = _actual_balance(
            session,
            user_id=user_id,
            goal_id=goal_id,
        )
        return result

    return session.execute(
        text(
            _GOAL_SELECT
            + " WHERE g.user_id = :user_id AND g.id = :goal_id"
        ),
        {"user_id": user_id, "goal_id": goal_id},
    ).mappings().first()


def create_goal(
    session: Session,
    *,
    context: AuthContext,
    payload: SavingsGoalCreateRequest,
    idempotency_key: str,
) -> IdempotencyResult:
    request_payload = payload.model_dump(mode="json")

    def operation() -> IdempotencyResult:
        _validate_target(payload.target_kyat)
        goal_id = uuid4()
        row = session.execute(
            text(
                """
                INSERT INTO savings_goals (
                    id, user_id, name, target_kyat, tone
                ) VALUES (
                    :id, :user_id, :name, :target_kyat, :tone
                )
                RETURNING status, created_at, updated_at, revision
                """
            ),
            {
                "id": goal_id,
                "user_id": context.user_id,
                "name": payload.name,
                "target_kyat": payload.target_kyat,
                "tone": payload.tone,
            },
        ).mappings().one()
        resource_row = {
            "id": goal_id,
            "name": payload.name,
            "target_kyat": payload.target_kyat,
            "tone": payload.tone,
            "status": row["status"],
            "created_at": row["created_at"],
            "updated_at": row["updated_at"],
            "revision": row["revision"],
            "actual_balance": Decimal(0),
        }
        return IdempotencyResult(
            status_code=201,
            body={"data": _goal_resource(resource_row)},
            headers={"ETag": _etag(int(row["revision"]))},
        )

    try:
        result, _ = run_idempotent(
            session,
            scope=f"user:{context.user_id}",
            method="POST",
            route="/api/v1/savings/goals",
            key=idempotency_key,
            payload=request_payload,
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


def list_goals(
    session: Session,
    *,
    context: AuthContext,
    status: SavingsGoalStatusFilter,
) -> dict[str, Any]:
    statement = _GOAL_SELECT + " WHERE g.user_id = :user_id"
    parameters: dict[str, Any] = {"user_id": context.user_id}
    if status != "all":
        statement += " AND g.status = :status"
        parameters["status"] = status
    statement += " ORDER BY g.created_at DESC, g.id DESC"
    rows = session.execute(text(statement), parameters).mappings().all()
    return {"data": [_goal_resource(row) for row in rows]}


def get_goal(
    session: Session,
    *,
    context: AuthContext,
    goal_id: UUID,
) -> SavingsGoalView:
    row = _goal_row_for_user(
        session,
        user_id=context.user_id,
        goal_id=goal_id,
    )
    if row is None:
        raise _not_found()
    return SavingsGoalView(
        body={"data": _goal_resource(row)},
        etag=_etag(int(row["revision"])),
    )


def _set_goal_archive_state(
    session: Session,
    *,
    context: AuthContext,
    goal_id: UUID,
    payload: SavingsGoalArchiveRequest,
    if_match: str | None,
    idempotency_key: str,
    archived: bool,
) -> IdempotencyResult:
    request_payload = payload.model_dump(mode="json")
    action = "archive" if archived else "restore"
    target_status = "archived" if archived else "active"

    def operation() -> IdempotencyResult:
        if not payload.confirm:
            raise ApiError(
                422,
                "confirmation_required",
                f"Confirm that you want to {action} this savings goal.",
                field_errors=[
                    _field_error(
                        "confirm",
                        "confirmation_required",
                        "Set confirm to true to continue.",
                    )
                ],
            )

        expected_revision = _parse_if_match(if_match)
        row = _goal_row_for_user(
            session,
            user_id=context.user_id,
            goal_id=goal_id,
            for_update=True,
        )
        if row is None:
            raise _not_found()
        if int(row["revision"]) != expected_revision:
            raise ApiError(
                412,
                "stale_revision",
                "The supplied revision does not match the current savings goal.",
            )

        if row["status"] == target_status:
            # Repeating a successful archive/restore with a new key is a no-op
            # rather than a second revision bump.
            updated_at = row["updated_at"]
            revision = int(row["revision"])
        else:
            updated = session.execute(
                text(
                    """
                    UPDATE savings_goals
                    SET status = :status,
                        archived_at = CASE
                            WHEN :archived THEN now()
                            ELSE NULL
                        END,
                        updated_at = now(),
                        revision = revision + 1
                    WHERE id = :goal_id
                      AND user_id = :user_id
                      AND revision = :expected_revision
                    RETURNING updated_at, revision
                    """
                ),
                {
                    "status": target_status,
                    "archived": archived,
                    "goal_id": goal_id,
                    "user_id": context.user_id,
                    "expected_revision": expected_revision,
                },
            ).mappings().first()
            if updated is None:
                raise ApiError(
                    412,
                    "stale_revision",
                    "The supplied revision does not match the current savings goal.",
                )
            updated_at = updated["updated_at"]
            revision = int(updated["revision"])

        resource_row = {
            "id": row["id"],
            "name": row["name"],
            "target_kyat": row["target_kyat"],
            "tone": row["tone"],
            "status": target_status,
            "created_at": row["created_at"],
            "updated_at": updated_at,
            "revision": revision,
            "actual_balance": row["actual_balance"],
        }
        return IdempotencyResult(
            status_code=200,
            body={"data": _goal_resource(resource_row)},
            headers={"ETag": _etag(revision)},
        )

    try:
        result, _ = run_idempotent(
            session,
            scope=f"user:{context.user_id}",
            method="POST",
            route=f"/api/v1/savings/goals/{goal_id}/{action}",
            key=idempotency_key,
            payload=request_payload,
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


def archive_goal(
    session: Session,
    *,
    context: AuthContext,
    goal_id: UUID,
    payload: SavingsGoalArchiveRequest,
    if_match: str | None,
    idempotency_key: str,
) -> IdempotencyResult:
    return _set_goal_archive_state(
        session,
        context=context,
        goal_id=goal_id,
        payload=payload,
        if_match=if_match,
        idempotency_key=idempotency_key,
        archived=True,
    )


def restore_goal(
    session: Session,
    *,
    context: AuthContext,
    goal_id: UUID,
    payload: SavingsGoalArchiveRequest,
    if_match: str | None,
    idempotency_key: str,
) -> IdempotencyResult:
    return _set_goal_archive_state(
        session,
        context=context,
        goal_id=goal_id,
        payload=payload,
        if_match=if_match,
        idempotency_key=idempotency_key,
        archived=False,
    )


def update_goal(
    session: Session,
    *,
    context: AuthContext,
    goal_id: UUID,
    payload: SavingsGoalUpdateRequest,
    if_match: str | None,
) -> SavingsGoalView:
    expected_revision = _parse_if_match(if_match)
    changed = payload.model_fields_set

    try:
        row = _goal_row_for_user(
            session,
            user_id=context.user_id,
            goal_id=goal_id,
            for_update=True,
        )
        if row is None:
            raise _not_found()
        if int(row["revision"]) != expected_revision:
            raise ApiError(
                412,
                "stale_revision",
                "The supplied revision does not match the current savings goal.",
            )
        if row["status"] != "active":
            raise _goal_archived()

        final_name = payload.name if "name" in changed else row["name"]
        final_target = (
            payload.target_kyat
            if "target_kyat" in changed
            else int(row["target_kyat"])
        )
        final_tone = payload.tone if "tone" in changed else row["tone"]

        if "target_kyat" in changed:
            _validate_target(final_target)

        updated = session.execute(
            text(
                """
                UPDATE savings_goals
                SET name = :name,
                    target_kyat = :target_kyat,
                    tone = :tone,
                    updated_at = now(),
                    revision = revision + 1
                WHERE id = :goal_id
                  AND user_id = :user_id
                  AND revision = :expected_revision
                RETURNING updated_at, revision
                """
            ),
            {
                "name": final_name,
                "target_kyat": final_target,
                "tone": final_tone,
                "goal_id": goal_id,
                "user_id": context.user_id,
                "expected_revision": expected_revision,
            },
        ).mappings().first()
        if updated is None:
            raise ApiError(
                412,
                "stale_revision",
                "The supplied revision does not match the current savings goal.",
            )
        session.commit()
    except ApiError:
        session.rollback()
        raise
    except SQLAlchemyError:
        session.rollback()
        raise

    resource_row = {
        "id": row["id"],
        "name": final_name,
        "target_kyat": final_target,
        "tone": final_tone,
        "status": row["status"],
        "created_at": row["created_at"],
        "updated_at": updated["updated_at"],
        "revision": updated["revision"],
        "actual_balance": row["actual_balance"],
    }
    return SavingsGoalView(
        body={"data": _goal_resource(resource_row)},
        etag=_etag(int(updated["revision"])),
    )


def list_entries(
    session: Session,
    *,
    context: AuthContext,
    goal_id: UUID,
) -> dict[str, Any]:
    goal = _goal_row_for_user(
        session,
        user_id=context.user_id,
        goal_id=goal_id,
    )
    if goal is None:
        raise _not_found()
    rows = session.execute(
        text(
            _ENTRY_SELECT
            + " WHERE e.user_id = :user_id AND e.goal_id = :goal_id"
            + " ORDER BY e.entry_date DESC, e.created_at DESC, e.id DESC"
        ),
        {"user_id": context.user_id, "goal_id": goal_id},
    ).mappings().all()
    return {"data": [_entry_resource(row) for row in rows]}


def create_contribution(
    session: Session,
    *,
    context: AuthContext,
    goal_id: UUID,
    payload: SavingsContributionCreateRequest,
    idempotency_key: str,
) -> IdempotencyResult:
    request_payload = payload.model_dump(mode="json")

    def operation() -> IdempotencyResult:
        goal = _goal_row_for_user(
            session,
            user_id=context.user_id,
            goal_id=goal_id,
            for_update=True,
        )
        if goal is None:
            raise _not_found()
        if goal["status"] != "active":
            raise _goal_archived()
        _validate_contribution_amount(payload.amount_kyat)

        entry_id = uuid4()
        row = session.execute(
            text(
                """
                INSERT INTO savings_entries (
                    id, user_id, goal_id, entry_type, amount_kyat,
                    entry_date, note
                ) VALUES (
                    :id, :user_id, :goal_id, 'contribution', :amount_kyat,
                    :entry_date, :note
                )
                RETURNING created_at
                """
            ),
            {
                "id": entry_id,
                "user_id": context.user_id,
                "goal_id": goal_id,
                "amount_kyat": payload.amount_kyat,
                "entry_date": payload.entry_date,
                "note": payload.note,
            },
        ).mappings().one()
        session.execute(
            text(
                """
                UPDATE savings_goals
                SET updated_at = now(), revision = revision + 1
                WHERE id = :goal_id AND user_id = :user_id
                """
            ),
            {"goal_id": goal_id, "user_id": context.user_id},
        )
        resource_row = {
            "id": entry_id,
            "entry_type": "contribution",
            "amount_kyat": payload.amount_kyat,
            "entry_date": payload.entry_date,
            "note": payload.note,
            "plan_day_id": None,
            "correction_of_entry_id": None,
            "correction_reason": None,
            "created_at": row["created_at"],
        }
        return IdempotencyResult(
            status_code=201,
            body={"data": _entry_resource(resource_row)},
            headers={},
        )

    try:
        result, _ = run_idempotent(
            session,
            scope=f"user:{context.user_id}",
            method="POST",
            route=f"/api/v1/savings/goals/{goal_id}/entries/contributions",
            key=idempotency_key,
            payload=request_payload,
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


def create_withdrawal(
    session: Session,
    *,
    context: AuthContext,
    goal_id: UUID,
    payload: SavingsWithdrawalCreateRequest,
    idempotency_key: str,
) -> IdempotencyResult:
    request_payload = payload.model_dump(mode="json")

    def operation() -> IdempotencyResult:
        goal = _goal_row_for_user(
            session,
            user_id=context.user_id,
            goal_id=goal_id,
            for_update=True,
        )
        if goal is None:
            raise _not_found()
        if goal["status"] != "active":
            raise _goal_archived()
        _validate_withdrawal_amount(payload.amount_kyat)

        available = Decimal(goal["actual_balance"])
        amount = Decimal(payload.amount_kyat)
        if amount > available:
            raise ApiError(
                409,
                "withdrawal_exceeds_balance",
                "The withdrawal exceeds the available savings balance.",
                field_errors=[
                    _field_error(
                        "amount_kyat",
                        "withdrawal_exceeds_balance",
                        "Enter an amount no greater than the actual savings balance.",
                    )
                ],
            )

        entry_id = uuid4()
        row = session.execute(
            text(
                """
                INSERT INTO savings_entries (
                    id, user_id, goal_id, entry_type, amount_kyat,
                    entry_date, note
                ) VALUES (
                    :id, :user_id, :goal_id, 'withdrawal', :amount_kyat,
                    :entry_date, :note
                )
                RETURNING created_at
                """
            ),
            {
                "id": entry_id,
                "user_id": context.user_id,
                "goal_id": goal_id,
                "amount_kyat": payload.amount_kyat,
                "entry_date": payload.entry_date,
                "note": payload.note,
            },
        ).mappings().one()
        session.execute(
            text(
                """
                UPDATE savings_goals
                SET updated_at = now(), revision = revision + 1
                WHERE id = :goal_id AND user_id = :user_id
                """
            ),
            {"goal_id": goal_id, "user_id": context.user_id},
        )
        resource_row = {
            "id": entry_id,
            "entry_type": "withdrawal",
            "amount_kyat": payload.amount_kyat,
            "entry_date": payload.entry_date,
            "note": payload.note,
            "plan_day_id": None,
            "correction_of_entry_id": None,
            "correction_reason": None,
            "created_at": row["created_at"],
        }
        return IdempotencyResult(
            status_code=201,
            body={"data": _entry_resource(resource_row)},
            headers={},
        )

    try:
        result, _ = run_idempotent(
            session,
            scope=f"user:{context.user_id}",
            method="POST",
            route=f"/api/v1/savings/goals/{goal_id}/entries/withdrawals",
            key=idempotency_key,
            payload=request_payload,
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
