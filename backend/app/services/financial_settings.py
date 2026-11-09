"""Owner-scoped starting-balance settings service.

The settings row is created during registration. This service only reads and
updates the authenticated caller's own row and delegates all balance math to
the existing transaction balance service so the Dashboard and settings views
cannot diverge.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Any
from uuid import UUID

from sqlalchemy import text
from sqlalchemy.exc import SQLAlchemyError
from sqlalchemy.orm import Session

from app.api.errors import ApiError
from app.schemas.financial_settings import FinancialSettingsUpdateRequest
from app.services.auth import AuthContext
from app.services.transactions import (
    AccountBalance,
    calculate_account_balance,
)

JSON_SAFE_INTEGER_MAX = 9_007_199_254_740_991

_IF_MATCH_PATTERN = re.compile(r'^"([0-9]+)"$')


@dataclass(frozen=True)
class FinancialSettingsView:
    body: dict[str, Any]
    etag: str


def _etag(revision: int) -> str:
    return f'"{revision}"'


def _field_error(field: str, code: str, message: str) -> dict[str, str]:
    return {"field": field, "code": code, "message": message}


def _unavailable() -> ApiError:
    return ApiError(
        500,
        "internal_error",
        "Financial settings are unavailable.",
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
            "The supplied revision does not match the current settings.",
        )
    return int(match.group(1))


def _validate_starting_balance(starting_balance_kyat: int | None) -> None:
    if starting_balance_kyat is None:
        return
    if not (
        -JSON_SAFE_INTEGER_MAX
        <= starting_balance_kyat
        <= JSON_SAFE_INTEGER_MAX
    ):
        raise ApiError(
            422,
            "amount_not_safe_integer",
            "The starting balance is outside the supported JSON integer range.",
            field_errors=[
                _field_error(
                    "starting_balance_kyat",
                    "amount_not_safe_integer",
                    "The starting balance is too large to represent safely.",
                )
            ],
        )


def _begin_consistent_read(session: Session) -> None:
    """Read the settings row and balance from one repeatable-read snapshot.

    Authentication has already run on this session, so end that read
    transaction before starting the settings snapshot. PostgreSQL only accepts
    ``SET TRANSACTION`` before the first query of a transaction.
    """
    session.rollback()
    session.execute(
        text("SET TRANSACTION ISOLATION LEVEL REPEATABLE READ")
    )


def _settings_row(
    session: Session,
    *,
    user_id: UUID,
    for_update: bool = False,
):
    statement = (
        "SELECT revision, starting_balance_kyat, effective_date "
        "FROM user_financial_settings WHERE user_id = :user_id"
    )
    if for_update:
        statement += " FOR UPDATE"
    return session.execute(
        text(statement),
        {"user_id": user_id},
    ).mappings().first()


def _view(
    revision: int,
    balance: AccountBalance,
) -> FinancialSettingsView:
    return FinancialSettingsView(
        body={
            "data": {
                "status": balance.status,
                "starting_balance_kyat": balance.starting_balance_kyat,
                "effective_date": (
                    balance.effective_date.isoformat()
                    if balance.effective_date is not None
                    else None
                ),
                "amount_kyat": balance.amount_kyat,
                "as_of_date": balance.as_of_date.isoformat(),
            }
        },
        etag=_etag(revision),
    )


def get_settings(
    session: Session,
    *,
    context: AuthContext,
) -> FinancialSettingsView:
    _begin_consistent_read(session)
    try:
        row = _settings_row(session, user_id=context.user_id)
        if row is None:
            raise _unavailable()
        balance = calculate_account_balance(
            session,
            user_id=context.user_id,
        )
        revision = int(row["revision"])
    except ApiError:
        session.rollback()
        raise
    except SQLAlchemyError:
        session.rollback()
        raise
    session.rollback()
    return _view(revision, balance)


def update_settings(
    session: Session,
    *,
    context: AuthContext,
    payload: FinancialSettingsUpdateRequest,
    if_match: str | None,
) -> FinancialSettingsView:
    normalized_if_match = if_match.strip() if if_match is not None else None

    try:
        expected_revision = _parse_if_match(normalized_if_match)
        _validate_starting_balance(payload.starting_balance_kyat)
        row = _settings_row(
            session,
            user_id=context.user_id,
            for_update=True,
        )
        if row is None:
            raise _unavailable()
        if int(row["revision"]) != expected_revision:
            raise ApiError(
                412,
                "stale_revision",
                "The supplied revision does not match the current settings.",
            )

        updated = session.execute(
            text(
                """
                UPDATE user_financial_settings
                SET starting_balance_kyat = :starting_balance_kyat,
                    effective_date = :effective_date,
                    revision = revision + 1,
                    updated_at = now()
                WHERE user_id = :user_id
                  AND revision = :expected_revision
                RETURNING revision
                """
            ),
            {
                "starting_balance_kyat": payload.starting_balance_kyat,
                "effective_date": payload.effective_date,
                "user_id": context.user_id,
                "expected_revision": expected_revision,
            },
        ).mappings().first()
        if updated is None:
            raise ApiError(
                412,
                "stale_revision",
                "The supplied revision does not match the current settings.",
            )

        balance = calculate_account_balance(
            session,
            user_id=context.user_id,
        )
        revision = int(updated["revision"])
        session.commit()
    except ApiError:
        session.rollback()
        raise
    except SQLAlchemyError:
        session.rollback()
        raise

    return _view(revision, balance)
