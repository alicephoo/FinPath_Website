"""Owner-scoped loan and repayment services.

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
from sqlalchemy.exc import IntegrityError, SQLAlchemyError
from sqlalchemy.orm import Session

from app.api.errors import ApiError
from app.core.config import settings
from app.schemas.loans import (
    CorrectionCreateRequest,
    LoanCreateRequest,
    LoanUpdateRequest,
    RepaymentCreateRequest,
)
from app.services.auth import AuthContext
from app.services.idempotency import IdempotencyResult, run_idempotent

JSON_SAFE_INTEGER_MAX = 9_007_199_254_740_991

# The Pages edge may weaken the server's strong ETag to W/"N" in transit
# while preserving the revision. Accept both forms and compare numerically.
_IF_MATCH_PATTERN = re.compile(r'^(?:W/)?"([0-9]+)"$')

_LOAN_SELECT = """
SELECT l.id, l.name, l.principal_kyat, l.start_date, l.created_at,
       l.updated_at, l.revision,
       COALESCE((
           SELECT SUM(r.amount_kyat::numeric)
           FROM loan_repayments AS r
           LEFT JOIN loan_repayment_corrections AS c
               ON c.repayment_id = r.id
           WHERE r.loan_id = l.id
             AND r.user_id = l.user_id
             AND c.id IS NULL
       ), 0) AS total_repaid
FROM loans AS l
"""

_LOAN_LOCK_SELECT = """
SELECT l.id, l.name, l.principal_kyat, l.start_date, l.created_at,
       l.updated_at, l.revision
FROM loans AS l
"""

_REPAYMENT_SELECT = """
SELECT r.id, r.loan_id, r.amount_kyat, r.repayment_date, r.note,
       r.created_at, r.revision, (c.id IS NOT NULL) AS corrected
FROM loan_repayments AS r
LEFT JOIN loan_repayment_corrections AS c ON c.repayment_id = r.id
"""


@dataclass(frozen=True)
class LoanView:
    body: dict[str, Any]
    etag: str


@dataclass(frozen=True)
class RepaymentView:
    body: dict[str, Any]
    etag: str


def _etag(revision: int) -> str:
    return f'"{revision}"'


def _field_error(field: str, code: str, message: str) -> dict[str, str]:
    return {"field": field, "code": code, "message": message}


def _not_found(resource: str = "loan") -> ApiError:
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
            "The supplied revision does not match the current loan.",
        )
    return int(match.group(1))


def _validate_amount(
    amount_kyat: int,
    *,
    field: str = "amount_kyat",
    label: str = "repayment",
) -> None:
    if amount_kyat <= 0:
        raise ApiError(
            422,
            "validation_error",
            f"The {label} amount must be a positive whole-Kyat integer.",
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
            f"The {label} amount is outside the supported JSON integer range.",
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
            f"The {label} amount exceeds the individual-entry limit.",
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


def _loan_resource(row: Mapping[str, Any]) -> dict[str, Any]:
    principal = Decimal(row["principal_kyat"])
    total_repaid = Decimal(row["total_repaid"])
    outstanding = principal - total_repaid
    return {
        "id": str(row["id"]),
        "name": row["name"],
        "principal_kyat": _safe_int(principal, label="loan principal"),
        "start_date": row["start_date"].isoformat(),
        "total_repaid_kyat": _safe_int(
            total_repaid, label="loan repayment total"
        ),
        "outstanding_principal_kyat": _safe_int(
            outstanding, label="outstanding principal"
        ),
        "created_at": row["created_at"].isoformat(),
        "updated_at": row["updated_at"].isoformat(),
    }


def _repayment_resource(
    row: Mapping[str, Any],
    *,
    corrected: bool | None = None,
) -> dict[str, Any]:
    return {
        "id": str(row["id"]),
        "amount_kyat": int(row["amount_kyat"]),
        "repayment_date": row["repayment_date"].isoformat(),
        "note": row["note"],
        "created_at": row["created_at"].isoformat(),
        "corrected": bool(row["corrected"]) if corrected is None else corrected,
    }


def _loan_row_for_user(
    session: Session,
    *,
    user_id: UUID,
    loan_id: UUID,
    for_update: bool = False,
) -> Mapping[str, Any] | None:
    if for_update:
        # Lock the parent row first, then compute the derived total in a new
        # statement. This guarantees the total reflects repayments committed
        # before the lock was acquired instead of a pre-lock snapshot.
        row = session.execute(
            text(
                _LOAN_LOCK_SELECT
                + " WHERE l.user_id = :user_id AND l.id = :loan_id"
                + " FOR UPDATE OF l"
            ),
            {"user_id": user_id, "loan_id": loan_id},
        ).mappings().first()
        if row is None:
            return None
        result = dict(row)
        result["total_repaid"] = _valid_repayment_total(
            session,
            user_id=user_id,
            loan_id=loan_id,
        )
        return result

    return session.execute(
        text(
            _LOAN_SELECT
            + " WHERE l.user_id = :user_id AND l.id = :loan_id"
        ),
        {"user_id": user_id, "loan_id": loan_id},
    ).mappings().first()


def _valid_repayment_total(
    session: Session,
    *,
    user_id: UUID,
    loan_id: UUID,
) -> Decimal:
    row = session.execute(
        text(
            """
            SELECT COALESCE(SUM(r.amount_kyat::numeric), 0) AS total
            FROM loan_repayments AS r
            LEFT JOIN loan_repayment_corrections AS c
                ON c.repayment_id = r.id
            WHERE r.user_id = :user_id
              AND r.loan_id = :loan_id
              AND c.id IS NULL
            """
        ),
        {"user_id": user_id, "loan_id": loan_id},
    ).mappings().one()
    return Decimal(row["total"])


def _repayment_row_for_user(
    session: Session,
    *,
    user_id: UUID,
    loan_id: UUID,
    repayment_id: UUID,
) -> Mapping[str, Any] | None:
    return session.execute(
        text(
            _REPAYMENT_SELECT
            + " WHERE r.user_id = :user_id"
            + " AND r.loan_id = :loan_id"
            + " AND r.id = :repayment_id"
        ),
        {
            "user_id": user_id,
            "loan_id": loan_id,
            "repayment_id": repayment_id,
        },
    ).mappings().first()


def create_loan(
    session: Session,
    *,
    context: AuthContext,
    payload: LoanCreateRequest,
    idempotency_key: str,
) -> IdempotencyResult:
    request_payload = payload.model_dump(mode="json")

    def operation() -> IdempotencyResult:
        _validate_amount(
            payload.principal_kyat,
            field="principal_kyat",
            label="loan principal",
        )
        loan_id = uuid4()
        row = session.execute(
            text(
                """
                INSERT INTO loans (
                    id, user_id, name, principal_kyat, start_date
                ) VALUES (
                    :id, :user_id, :name, :principal_kyat, :start_date
                )
                RETURNING created_at, updated_at, revision
                """
            ),
            {
                "id": loan_id,
                "user_id": context.user_id,
                "name": payload.name,
                "principal_kyat": payload.principal_kyat,
                "start_date": payload.start_date,
            },
        ).mappings().one()
        resource_row = {
            "id": loan_id,
            "name": payload.name,
            "principal_kyat": payload.principal_kyat,
            "start_date": payload.start_date,
            "created_at": row["created_at"],
            "updated_at": row["updated_at"],
            "revision": row["revision"],
            "total_repaid": Decimal(0),
        }
        return IdempotencyResult(
            status_code=201,
            body={"data": _loan_resource(resource_row)},
            headers={"ETag": _etag(int(row["revision"]))},
        )

    try:
        result, _ = run_idempotent(
            session,
            scope=f"user:{context.user_id}",
            method="POST",
            route="/api/v1/loans",
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


def list_loans(
    session: Session,
    *,
    context: AuthContext,
) -> dict[str, Any]:
    rows = session.execute(
        text(
            _LOAN_SELECT
            + " WHERE l.user_id = :user_id"
            + " ORDER BY l.start_date DESC, l.id DESC"
        ),
        {"user_id": context.user_id},
    ).mappings().all()
    return {"data": [_loan_resource(row) for row in rows]}


def get_loan(
    session: Session,
    *,
    context: AuthContext,
    loan_id: UUID,
) -> LoanView:
    row = _loan_row_for_user(
        session,
        user_id=context.user_id,
        loan_id=loan_id,
    )
    if row is None:
        raise _not_found()
    return LoanView(
        body={"data": _loan_resource(row)},
        etag=_etag(int(row["revision"])),
    )


def update_loan(
    session: Session,
    *,
    context: AuthContext,
    loan_id: UUID,
    payload: LoanUpdateRequest,
    if_match: str | None,
) -> LoanView:
    expected_revision = _parse_if_match(if_match)
    changed = payload.model_fields_set

    try:
        row = _loan_row_for_user(
            session,
            user_id=context.user_id,
            loan_id=loan_id,
            for_update=True,
        )
        if row is None:
            raise _not_found()
        if int(row["revision"]) != expected_revision:
            raise ApiError(
                412,
                "stale_revision",
                "The supplied revision does not match the current loan.",
            )

        final_name = payload.name if "name" in changed else row["name"]
        final_principal = (
            payload.principal_kyat
            if "principal_kyat" in changed
            else int(row["principal_kyat"])
        )
        final_start_date = (
            payload.start_date
            if "start_date" in changed
            else row["start_date"]
        )

        if "principal_kyat" in changed:
            _validate_amount(
                final_principal,
                field="principal_kyat",
                label="loan principal",
            )
            total_repaid = _valid_repayment_total(
                session,
                user_id=context.user_id,
                loan_id=loan_id,
            )
            if Decimal(final_principal) < total_repaid:
                raise ApiError(
                    422,
                    "validation_error",
                    "The loan principal cannot be less than repayments already recorded.",
                    field_errors=[
                        _field_error(
                            "principal_kyat",
                            "principal_below_repayments",
                            "Choose a principal at least equal to the total repaid.",
                        )
                    ],
                )

        updated = session.execute(
            text(
                """
                UPDATE loans
                SET name = :name,
                    principal_kyat = :principal_kyat,
                    start_date = :start_date,
                    updated_at = now(),
                    revision = revision + 1
                WHERE id = :loan_id
                  AND user_id = :user_id
                  AND revision = :expected_revision
                RETURNING updated_at, revision
                """
            ),
            {
                "name": final_name,
                "principal_kyat": final_principal,
                "start_date": final_start_date,
                "loan_id": loan_id,
                "user_id": context.user_id,
                "expected_revision": expected_revision,
            },
        ).mappings().first()
        if updated is None:
            raise ApiError(
                412,
                "stale_revision",
                "The supplied revision does not match the current loan.",
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
        "principal_kyat": final_principal,
        "start_date": final_start_date,
        "created_at": row["created_at"],
        "updated_at": updated["updated_at"],
        "revision": updated["revision"],
        "total_repaid": row["total_repaid"],
    }
    return LoanView(
        body={"data": _loan_resource(resource_row)},
        etag=_etag(int(updated["revision"])),
    )


def list_repayments(
    session: Session,
    *,
    context: AuthContext,
    loan_id: UUID,
) -> dict[str, Any]:
    loan = _loan_row_for_user(
        session,
        user_id=context.user_id,
        loan_id=loan_id,
    )
    if loan is None:
        raise _not_found()
    rows = session.execute(
        text(
            _REPAYMENT_SELECT
            + " WHERE r.user_id = :user_id AND r.loan_id = :loan_id"
            + " ORDER BY r.repayment_date DESC, r.created_at DESC, r.id DESC"
        ),
        {"user_id": context.user_id, "loan_id": loan_id},
    ).mappings().all()
    return {"data": [_repayment_resource(row) for row in rows]}


def get_repayment(
    session: Session,
    *,
    context: AuthContext,
    loan_id: UUID,
    repayment_id: UUID,
) -> RepaymentView:
    row = _repayment_row_for_user(
        session,
        user_id=context.user_id,
        loan_id=loan_id,
        repayment_id=repayment_id,
    )
    if row is None:
        raise _not_found("repayment")
    return RepaymentView(
        body={"data": _repayment_resource(row)},
        etag=_etag(int(row["revision"])),
    )


def create_repayment(
    session: Session,
    *,
    context: AuthContext,
    loan_id: UUID,
    payload: RepaymentCreateRequest,
    idempotency_key: str,
) -> IdempotencyResult:
    request_payload = payload.model_dump(mode="json")

    def operation() -> IdempotencyResult:
        loan = _loan_row_for_user(
            session,
            user_id=context.user_id,
            loan_id=loan_id,
            for_update=True,
        )
        if loan is None:
            raise _not_found()
        _validate_amount(payload.amount_kyat)
        total_repaid = Decimal(loan["total_repaid"])
        principal = Decimal(loan["principal_kyat"])
        outstanding = principal - total_repaid
        if Decimal(payload.amount_kyat) > outstanding:
            raise ApiError(
                409,
                "repayment_exceeds_principal",
                "The repayment exceeds the loan's outstanding principal.",
                field_errors=[
                    _field_error(
                        "amount_kyat",
                        "repayment_exceeds_principal",
                        "Enter an amount no greater than the outstanding principal.",
                    )
                ],
            )
        _safe_int(
            total_repaid + Decimal(payload.amount_kyat),
            label="loan repayment total",
        )

        repayment_id = uuid4()
        row = session.execute(
            text(
                """
                INSERT INTO loan_repayments (
                    id, user_id, loan_id, amount_kyat, repayment_date, note
                ) VALUES (
                    :id, :user_id, :loan_id, :amount_kyat,
                    :repayment_date, :note
                )
                RETURNING created_at, revision
                """
            ),
            {
                "id": repayment_id,
                "user_id": context.user_id,
                "loan_id": loan_id,
                "amount_kyat": payload.amount_kyat,
                "repayment_date": payload.repayment_date,
                "note": payload.note,
            },
        ).mappings().one()
        session.execute(
            text(
                """
                UPDATE loans
                SET updated_at = now(), revision = revision + 1
                WHERE id = :loan_id AND user_id = :user_id
                """
            ),
            {"loan_id": loan_id, "user_id": context.user_id},
        )
        resource_row = {
            "id": repayment_id,
            "amount_kyat": payload.amount_kyat,
            "repayment_date": payload.repayment_date,
            "note": payload.note,
            "created_at": row["created_at"],
            "revision": row["revision"],
            "corrected": False,
        }
        return IdempotencyResult(
            status_code=201,
            body={"data": _repayment_resource(resource_row)},
            headers={"ETag": _etag(int(row["revision"]))},
        )

    try:
        result, _ = run_idempotent(
            session,
            scope=f"user:{context.user_id}",
            method="POST",
            route=f"/api/v1/loans/{loan_id}/repayments",
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


def correct_repayment(
    session: Session,
    *,
    context: AuthContext,
    loan_id: UUID,
    repayment_id: UUID,
    payload: CorrectionCreateRequest,
    idempotency_key: str,
) -> IdempotencyResult:
    request_payload = payload.model_dump(mode="json")

    def operation() -> IdempotencyResult:
        loan = _loan_row_for_user(
            session,
            user_id=context.user_id,
            loan_id=loan_id,
            for_update=True,
        )
        if loan is None:
            raise _not_found()
        repayment = _repayment_row_for_user(
            session,
            user_id=context.user_id,
            loan_id=loan_id,
            repayment_id=repayment_id,
        )
        if repayment is None:
            raise _not_found("repayment")
        existing = session.execute(
            text(
                "SELECT id FROM loan_repayment_corrections "
                "WHERE repayment_id = :repayment_id"
            ),
            {"repayment_id": repayment_id},
        ).scalar_one_or_none()
        if existing is not None:
            raise ApiError(
                409,
                "repayment_already_corrected",
                "This repayment has already been corrected.",
            )

        correction_id = uuid4()
        correction = session.execute(
            text(
                """
                INSERT INTO loan_repayment_corrections (
                    id, user_id, loan_id, repayment_id, type,
                    amount_kyat, reason, created_by_user_id
                ) VALUES (
                    :id, :user_id, :loan_id, :repayment_id, 'reversal',
                    :amount_kyat, :reason, :created_by_user_id
                )
                RETURNING created_at
                """
            ),
            {
                "id": correction_id,
                "user_id": context.user_id,
                "loan_id": loan_id,
                "repayment_id": repayment_id,
                "amount_kyat": repayment["amount_kyat"],
                "reason": payload.reason,
                "created_by_user_id": context.user_id,
            },
        ).mappings().one()
        updated_repayment = session.execute(
            text(
                """
                UPDATE loan_repayments
                SET revision = revision + 1
                WHERE id = :repayment_id
                  AND loan_id = :loan_id
                  AND user_id = :user_id
                RETURNING revision
                """
            ),
            {
                "repayment_id": repayment_id,
                "loan_id": loan_id,
                "user_id": context.user_id,
            },
        ).mappings().one()
        session.execute(
            text(
                """
                UPDATE loans
                SET updated_at = now(), revision = revision + 1
                WHERE id = :loan_id AND user_id = :user_id
                """
            ),
            {"loan_id": loan_id, "user_id": context.user_id},
        )
        total_repaid = _valid_repayment_total(
            session,
            user_id=context.user_id,
            loan_id=loan_id,
        )
        outstanding = Decimal(loan["principal_kyat"]) - total_repaid
        body = {
            "data": {
                "original_repayment": _repayment_resource(
                    repayment, corrected=True
                ),
                "correction": {
                    "id": str(correction_id),
                    "original_repayment_id": str(repayment_id),
                    "amount_kyat": int(repayment["amount_kyat"]),
                    "type": "reversal",
                    "reason": payload.reason,
                    "created_by_user_id": str(context.user_id),
                    "created_at": correction["created_at"].isoformat(),
                },
                "outstanding_principal_kyat": _safe_int(
                    outstanding, label="outstanding principal"
                ),
            }
        }
        return IdempotencyResult(
            status_code=201,
            body=body,
            headers={
                "ETag": _etag(int(updated_repayment["revision"]))
            },
        )

    try:
        result, _ = run_idempotent(
            session,
            scope=f"user:{context.user_id}",
            method="POST",
            route=(
                f"/api/v1/loans/{loan_id}/repayments/"
                f"{repayment_id}/corrections"
            ),
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
        if constraint and "repayment" in constraint:
            raise ApiError(
                409,
                "repayment_already_corrected",
                "This repayment has already been corrected.",
            ) from None
        raise
    except SQLAlchemyError:
        session.rollback()
        raise
    return result
