"""Owner-scoped dashboard aggregation service.

The dashboard deliberately reuses the transaction, loan, and savings service
functions so its totals follow the same calculation rules as the feature APIs.
It reads authoritative records only; no dashboard totals are persisted.
"""

from __future__ import annotations

from collections.abc import Iterable
from datetime import date, timedelta
from typing import Any

from sqlalchemy import text
from sqlalchemy.orm import Session

from app.api.errors import ApiError
from app.services import loans as loan_service
from app.services import savings as savings_service
from app.services import transactions as transaction_service
from app.services import travel as travel_service
from app.services.auth import AuthContext

JSON_SAFE_INTEGER_MAX = 9_007_199_254_740_991

DEFAULT_RECENT_LIMIT = 5
MAX_RECENT_LIMIT = 50


def _safe_sum(values: Iterable[int], *, label: str) -> int:
    total = sum(values, 0)
    if not (-JSON_SAFE_INTEGER_MAX <= total <= JSON_SAFE_INTEGER_MAX):
        raise ApiError(
            500,
            "aggregate_out_of_range",
            f"The {label} cannot be represented safely.",
        )
    return total


def _current_month_bounds(report_date: date) -> tuple[date, date]:
    month_start = report_date.replace(day=1)
    if month_start.month == 12:
        next_month_start = date(month_start.year + 1, 1, 1)
    else:
        next_month_start = date(month_start.year, month_start.month + 1, 1)
    return month_start, next_month_start - timedelta(days=1)


def _begin_consistent_read(session: Session) -> None:
    """Use one repeatable-read snapshot for every dashboard query.

    Authentication has already run on this session, so end that read
    transaction before starting the report snapshot. PostgreSQL only accepts
    ``SET TRANSACTION`` before the first query of a transaction.
    """
    session.rollback()
    session.execute(
        text("SET TRANSACTION ISOLATION LEVEL REPEATABLE READ")
    )


def get_overview(
    session: Session,
    *,
    context: AuthContext,
    recent_limit: int,
) -> dict[str, Any]:
    if not 1 <= recent_limit <= MAX_RECENT_LIMIT:
        raise ApiError(
            422,
            "validation_error",
            "The recent transaction limit is invalid.",
        )

    _begin_consistent_read(session)
    report_date = date.today()

    balance = transaction_service.calculate_account_balance(
        session,
        user_id=context.user_id,
        as_of_date=report_date,
    )
    all_time_totals = transaction_service.calculate_transaction_totals(
        session,
        user_id=context.user_id,
    )
    month_start, month_end = _current_month_bounds(report_date)
    current_month_totals = transaction_service.calculate_transaction_totals(
        session,
        user_id=context.user_id,
        from_date=month_start,
        to_date=month_end,
    )
    recent_transactions = transaction_service.list_transactions(
        session,
        context=context,
        transaction_type=None,
        category_id=None,
        from_date=None,
        to_date=None,
        q=None,
        limit=recent_limit,
        cursor=None,
    )["data"]

    loans = loan_service.list_loans(session, context=context)["data"]
    outstanding_loan_principal = _safe_sum(
        (int(loan["outstanding_principal_kyat"]) for loan in loans),
        label="outstanding loan principal",
    )
    loan_repayments = _safe_sum(
        (int(loan["total_repaid_kyat"]) for loan in loans),
        label="loan repayment total",
    )
    active_loan_count = sum(
        1
        for loan in loans
        if int(loan["outstanding_principal_kyat"]) > 0
    )

    savings_goals = savings_service.list_goals(
        session,
        context=context,
        status="all",
    )["data"]
    actual_savings_balance = _safe_sum(
        (int(goal["actual_balance_kyat"]) for goal in savings_goals),
        label="savings balance",
    )
    savings_target = _safe_sum(
        (int(goal["target_kyat"]) for goal in savings_goals),
        label="savings target total",
    )
    active_savings_goal_count = sum(
        1 for goal in savings_goals if goal["status"] == "active"
    )
    travel_expense = travel_service.calculate_recognized_travel_expense(
        session,
        user_id=context.user_id,
    )
    total_expense = _safe_sum(
        (all_time_totals.expense_kyat, travel_expense),
        label="total expense",
    )

    return {
        "data": {
            "account_balance": {
                "status": balance.status,
                "starting_balance_kyat": balance.starting_balance_kyat,
                "effective_date": (
                    balance.effective_date.isoformat()
                    if balance.effective_date is not None
                    else None
                ),
                "amount_kyat": balance.amount_kyat,
                "as_of_date": balance.as_of_date.isoformat(),
            },
            "all_time_income_kyat": all_time_totals.income_kyat,
            "all_time_expense_kyat": all_time_totals.expense_kyat,
            "travel_expense_kyat": travel_expense,
            "total_expense_kyat": total_expense,
            "current_month_income_kyat": current_month_totals.income_kyat,
            "current_month_expense_kyat": current_month_totals.expense_kyat,
            "outstanding_loan_principal_kyat": (
                outstanding_loan_principal
            ),
            "active_loan_count": active_loan_count,
            "loan_repayments_kyat": loan_repayments,
            "actual_savings_balance_kyat": actual_savings_balance,
            "savings_target_kyat": savings_target,
            "active_savings_goal_count": active_savings_goal_count,
            "savings_goals": savings_goals,
            "recent_transactions": recent_transactions,
        }
    }
