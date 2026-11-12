"""Owner-scoped dashboard reports and CSV export.

The report queries reuse the authoritative transaction/loan/savings/travel
services wherever possible. All reads are scoped by the authenticated user ID
and run in a repeatable-read snapshot so CSV rows and summary totals cannot mix
pre- and post-mutation state.
"""

from __future__ import annotations

import re
from datetime import date
from decimal import Decimal
from typing import Any
from uuid import UUID

from sqlalchemy import text
from sqlalchemy.orm import Session

from app.api.errors import ApiError
from app.services import dashboard as dashboard_service
from app.services import loans as loan_service
from app.services import savings as savings_service
from app.services import transactions as transaction_service
from app.services import travel as travel_service
from app.services.auth import AuthContext

JSON_SAFE_INTEGER_MAX = 9_007_199_254_740_991
_FORMULA_PREFIX = re.compile(
    r"^[\s\u0000-\u001f\u007f-\u009f]*[=+\-@]"
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


def _safe_sum(values: list[int], *, label: str) -> int:
    total = sum(values, 0)
    if not (-JSON_SAFE_INTEGER_MAX <= total <= JSON_SAFE_INTEGER_MAX):
        raise ApiError(
            500,
            "aggregate_out_of_range",
            f"The {label} cannot be represented safely.",
        )
    return total


def _six_month_bounds(report_date: date) -> tuple[date, date]:
    current_month = report_date.replace(day=1)
    month_index = (
        (current_month.year * 12) + (current_month.month - 1) - 5
    )
    year, zero_based_month = divmod(month_index, 12)
    return date(year, zero_based_month + 1, 1), report_date


def resolve_bounds(
    from_date: date | None,
    to_date: date | None,
) -> tuple[date, date]:
    if from_date is None and to_date is None:
        return _six_month_bounds(date.today())
    if from_date is None or to_date is None:
        raise ApiError(
            422,
            "invalid_date_range",
            "Provide both from and to, or omit both for the last six months.",
        )
    transaction_service.validate_date_range(from_date, to_date)
    return from_date, to_date


def _begin_consistent_read(session: Session) -> None:
    session.rollback()
    session.execute(
        text("SET TRANSACTION ISOLATION LEVEL REPEATABLE READ")
    )


def _month_starts(from_date: date, to_date: date) -> list[date]:
    starts: list[date] = []
    year = from_date.year
    month = from_date.month
    end_month = to_date.year * 12 + to_date.month
    while year * 12 + month <= end_month:
        starts.append(date(year, month, 1))
        if month == 12:
            year += 1
            month = 1
        else:
            month += 1
    return starts


def _query_cash_flow_rows(
    session: Session,
    *,
    user_id: Any,
    from_date: date,
    to_date: date,
) -> list[dict[str, Any]]:
    rows = session.execute(
        text(
            """
            SELECT
                date_trunc('month', transaction_date)::date AS month_start,
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
              AND transaction_date >= :from_date
              AND transaction_date <= :to_date
            GROUP BY date_trunc('month', transaction_date)::date
            ORDER BY month_start ASC
            """
        ),
        {
            "user_id": user_id,
            "from_date": from_date,
            "to_date": to_date,
        },
    ).mappings().all()
    by_month = {
        row["month_start"].strftime("%Y-%m"): row for row in rows
    }
    result: list[dict[str, Any]] = []
    for month_start in _month_starts(from_date, to_date):
        key = month_start.strftime("%Y-%m")
        row = by_month.get(key)
        result.append(
            {
                "month": key,
                "income_kyat": _safe_int(
                    Decimal(row["income"] if row else 0),
                    label="monthly income",
                ),
                "expense_kyat": _safe_int(
                    Decimal(row["expense"] if row else 0),
                    label="monthly expense",
                ),
            }
        )
    return result


def _query_expense_categories(
    session: Session,
    *,
    user_id: Any,
    from_date: date,
    to_date: date,
) -> list[dict[str, Any]]:
    rows = session.execute(
        text(
            """
            SELECT c.name AS category,
                   COALESCE(SUM(t.amount_kyat::numeric), 0) AS expense
            FROM transactions AS t
            JOIN transaction_categories AS c ON c.id = t.category_id
            WHERE t.user_id = :user_id
              AND t.type = 'expense'
              AND t.transaction_date >= :from_date
              AND t.transaction_date <= :to_date
            GROUP BY c.name
            ORDER BY expense DESC, lower(c.name) ASC
            """
        ),
        {
            "user_id": user_id,
            "from_date": from_date,
            "to_date": to_date,
        },
    ).mappings().all()
    return [
        {
            "category": row["category"],
            "expense_kyat": _safe_int(
                Decimal(row["expense"]),
                label="category expense",
            ),
        }
        for row in rows
    ]


def get_cash_flow(
    session: Session,
    *,
    context: AuthContext,
    from_date: date | None,
    to_date: date | None,
) -> dict[str, Any]:
    resolved_from, resolved_to = resolve_bounds(from_date, to_date)
    _begin_consistent_read(session)
    months = _query_cash_flow_rows(
        session,
        user_id=context.user_id,
        from_date=resolved_from,
        to_date=resolved_to,
    )
    return {
        "data": {
            "from": resolved_from.isoformat(),
            "to": resolved_to.isoformat(),
            "months": months,
        }
    }


def get_expenses_by_category(
    session: Session,
    *,
    context: AuthContext,
    from_date: date | None,
    to_date: date | None,
) -> dict[str, Any]:
    resolved_from, resolved_to = resolve_bounds(from_date, to_date)
    _begin_consistent_read(session)
    categories = _query_expense_categories(
        session,
        user_id=context.user_id,
        from_date=resolved_from,
        to_date=resolved_to,
    )
    return {
        "data": {
            "from": resolved_from.isoformat(),
            "to": resolved_to.isoformat(),
            "categories": categories,
        }
    }


def _all_transactions(
    session: Session,
    *,
    context: AuthContext,
) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    cursor: str | None = None
    while True:
        page = transaction_service.list_transactions(
            session,
            context=context,
            transaction_type=None,
            category_id=None,
            from_date=None,
            to_date=None,
            q=None,
            limit=100,
            cursor=cursor,
        )
        rows.extend(page["data"])
        cursor = page["pagination"]["next_cursor"]
        if cursor is None:
            return rows


def _csv_cell(value: str | int, *, protect: bool = True) -> str:
    text_value = str(value)
    if protect and _FORMULA_PREFIX.match(text_value):
        text_value = "'" + text_value
    if any(character in text_value for character in ',"\r\n'):
        text_value = '"' + text_value.replace('"', '""') + '"'
    return text_value


def _csv_row(values: list[str | int], *, protect: list[bool]) -> str:
    return ",".join(
        _csv_cell(value, protect=should_protect)
        for value, should_protect in zip(values, protect)
    )


def export_dashboard_csv(
    session: Session,
    *,
    context: AuthContext,
) -> str:
    overview = dashboard_service.get_overview(
        session,
        context=context,
        recent_limit=dashboard_service.MAX_RECENT_LIMIT,
    )["data"]
    report_date = date.fromisoformat(overview["account_balance"]["as_of_date"])
    report_from, report_to = _six_month_bounds(report_date)
    months = _query_cash_flow_rows(
        session,
        user_id=context.user_id,
        from_date=report_from,
        to_date=report_to,
    )
    categories = _query_expense_categories(
        session,
        user_id=context.user_id,
        from_date=report_from,
        to_date=report_to,
    )
    transactions = _all_transactions(session, context=context)

    loans = loan_service.list_loans(session, context=context)["data"]
    savings_goals = savings_service.list_goals(
        session,
        context=context,
        status="all",
    )["data"]
    trips = travel_service.list_trips(session, context=context)["data"]

    rows: list[list[str | int]] = []
    header = [
        "Section",
        "Record",
        "Date or period",
        "Category",
        "Amount (MMK)",
        "Comparison (MMK)",
        "Status or notes",
    ]
    rows.append(header)

    balance = overview["account_balance"]
    balance_amount: int | str = (
        balance["amount_kyat"]
        if balance["amount_kyat"] is not None
        else ""
    )
    rows.append([
        "Summary",
        "Calculated account balance",
        f"As of {balance['as_of_date']}",
        "",
        balance_amount,
        "",
        (
            f"Starting balance {balance['starting_balance_kyat']} MMK as of "
            f"the start of {balance['effective_date']}; transactions "
            f"on or after that date applied"
            if balance["status"] == "configured"
            else "Manually enter a starting balance and effective date"
        ),
    ])
    rows.append([
        "Summary",
        "All-time recorded income",
        "All time",
        "",
        overview["all_time_income_kyat"],
        "",
        "Transactions only",
    ])
    rows.append([
        "Summary",
        "All-time transaction expenses",
        "All time",
        "",
        overview["all_time_expense_kyat"],
        "",
        "Transactions only",
    ])
    rows.append([
        "Summary",
        "Recognized travel expense",
        "All time",
        "",
        overview["travel_expense_kyat"],
        "",
        "Active trips recognize the greater of plan and spending; completed/cancelled trips recognize spending",
    ])
    rows.append([
        "Summary",
        "Total expenses",
        "All time",
        "",
        overview["total_expense_kyat"],
        "",
        "Transaction expenses plus recognized travel",
    ])
    rows.append([
        "Summary",
        "Net transaction flow",
        "All time",
        "",
        _safe_sum(
            [
                overview["all_time_income_kyat"],
                -overview["all_time_expense_kyat"],
            ],
            label="net transaction flow",
        ),
        "",
        "Recorded income minus recorded transaction expenses; not an account balance",
    ])
    rows.append([
        "Summary",
        "Current-month income",
        balance["as_of_date"][:7],
        "",
        overview["current_month_income_kyat"],
        "",
        "Transactions only",
    ])
    rows.append([
        "Summary",
        "Current-month transaction expenses",
        balance["as_of_date"][:7],
        "",
        overview["current_month_expense_kyat"],
        "",
        "Transactions only",
    ])
    rows.append([
        "Summary",
        "Outstanding loan principal",
        "Current",
        "",
        overview["outstanding_loan_principal_kyat"],
        "",
        "Liability; original principal is not an expense",
    ])
    rows.append([
        "Summary",
        "Recorded principal repayments",
        "All time",
        "",
        overview["loan_repayments_kyat"],
        "",
        "Shown separately; not included in transaction expenses",
    ])
    rows.append([
        "Summary",
        "Net savings balance",
        "Current",
        "",
        overview["actual_savings_balance_kyat"],
        overview["savings_target_kyat"],
        "Recorded contributions less withdrawals; excludes planned savings",
    ])
    rows.append([
        "Summary",
        "Travel budget total",
        "Current plans",
        "",
        _safe_sum(
            [int(trip["budget_kyat"]) for trip in trips],
            label="travel budget total",
        ),
        "",
        "Planned active/completed trips; not an expense",
    ])
    rows.append([
        "Summary",
        "Trip-owned actual spending",
        "Recorded entries",
        "",
        _safe_sum(
            [int(trip["spending_kyat"]) for trip in trips],
            label="trip spending total",
        ),
        "",
        "Separate from transactions; recognized travel is included in Total expenses",
    ])

    for month in months:
        rows.append([
            "Cash flow",
            "Recorded transactions",
            month["month"],
            "",
            month["income_kyat"],
            month["expense_kyat"],
            "Income in Amount; transaction expenses in Comparison",
        ])

    for category in categories:
        rows.append([
            "Expense category",
            category["category"],
            f"{report_from.isoformat()} to {report_to.isoformat()}",
            category["category"],
            category["expense_kyat"],
            "",
            "Transaction expenses only",
        ])

    for transaction in transactions:
        rows.append([
            "Transaction",
            transaction["description"],
            transaction["transaction_date"],
            transaction["category"],
            transaction["amount_kyat"],
            "",
            transaction["type"],
        ])

    for loan in loans:
        rows.append([
            "Loan",
            loan["name"],
            loan["start_date"],
            "Outstanding principal",
            loan["outstanding_principal_kyat"],
            loan["total_repaid_kyat"],
            "Amount is outstanding principal; Comparison is recorded repayments, not expenses",
        ])
        repayments = loan_service.list_repayments(
            session,
            context=context,
            loan_id=UUID(loan["id"]),
        )["data"]
        for repayment in repayments:
            note = repayment["note"] or ""
            rows.append([
                "Loan repayment",
                f"{loan['name']}" + (f" · {note}" if note else ""),
                repayment["repayment_date"],
                "Principal repayment",
                repayment["amount_kyat"],
                "",
                "Separate from transaction expenses",
            ])

    for goal in savings_goals:
        rows.append([
            "Savings goal",
            goal["name"],
            "Current",
            "Actual balance",
            goal["actual_balance_kyat"],
            goal["target_kyat"],
            f"{goal['status']}; planned and missed amounts excluded",
        ])
        entries = savings_service.list_entries(
            session,
            context=context,
            goal_id=UUID(goal["id"]),
        )["data"]
        for entry in entries:
            note = entry["note"] or ""
            rows.append([
                "Savings entry",
                f"{goal['name']}" + (f" · {note}" if note else ""),
                entry["entry_date"],
                entry["entry_type"],
                entry["amount_kyat"],
                "",
                "Recorded savings activity",
            ])

    for trip in trips:
        rows.append([
            "Travel budget",
            trip["name"],
            f"{trip['start_date']} to {trip['end_date']}",
            trip["destination"],
            trip["budget_kyat"],
            trip["spending_kyat"],
            f"{trip['status']}; budget in Amount, spending in Comparison; separate from expenses",
        ])
        budgets = travel_service.list_budgets(
            session,
            context=context,
            trip_id=UUID(trip["id"]),
        )["data"]
        for budget in budgets:
            rows.append([
                "Planned expense",
                trip["name"],
                "Current plan",
                budget["category"],
                budget["budget_kyat"],
                "",
                "Trip budget allocation; not an expense",
            ])
        spending = travel_service.list_spending(
            session,
            context=context,
            trip_id=UUID(trip["id"]),
        )["data"]
        for entry in spending:
            rows.append([
                "Trip spending",
                f"{trip['name']} · {entry['description']}",
                entry["spending_date"],
                entry["category"],
                entry["amount_kyat"],
                "",
                "Trip-owned actual spending; not linked to transaction records",
            ])

    protect = [True, True, True, True, False, False, True]
    csv_lines = [_csv_row(row, protect=protect) for row in rows]
    return "\r\n".join(csv_lines) + "\r\n"
