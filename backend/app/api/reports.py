"""Authenticated owner-scoped report and CSV routes."""

from __future__ import annotations

from fastapi import APIRouter, Depends, Query, Response
from sqlalchemy.orm import Session

from app.api.dependencies import get_current_auth
from app.db.session import get_db
from app.schemas.reports import (
    CashFlowReportResponse,
    ExpenseCategoryReportResponse,
)
from app.services import reports as reports_service
from app.services import transactions as transaction_service
from app.services.auth import AuthContext

router = APIRouter(prefix="/api/v1/reports", tags=["reports"])


def _parse_bound(value: str | None, field: str):
    return transaction_service.parse_query_date(value, field)


@router.get("/cash-flow", response_model=CashFlowReportResponse)
def get_cash_flow(
    from_date: str | None = Query(default=None, alias="from"),
    to_date: str | None = Query(default=None, alias="to"),
    context: AuthContext = Depends(get_current_auth),
    db: Session = Depends(get_db),
) -> dict[str, object]:
    return reports_service.get_cash_flow(
        db,
        context=context,
        from_date=_parse_bound(from_date, "from"),
        to_date=_parse_bound(to_date, "to"),
    )


@router.get(
    "/expenses-by-category",
    response_model=ExpenseCategoryReportResponse,
)
def get_expenses_by_category(
    from_date: str | None = Query(default=None, alias="from"),
    to_date: str | None = Query(default=None, alias="to"),
    context: AuthContext = Depends(get_current_auth),
    db: Session = Depends(get_db),
) -> dict[str, object]:
    return reports_service.get_expenses_by_category(
        db,
        context=context,
        from_date=_parse_bound(from_date, "from"),
        to_date=_parse_bound(to_date, "to"),
    )


@router.get("/export.csv")
def export_dashboard_csv(
    context: AuthContext = Depends(get_current_auth),
    db: Session = Depends(get_db),
) -> Response:
    csv_text = reports_service.export_dashboard_csv(
        db,
        context=context,
    )
    return Response(
        content=("\ufeff" + csv_text).encode("utf-8"),
        media_type="text/csv; charset=utf-8",
        headers={
            "Content-Disposition": (
                'attachment; filename="finpath-dashboard-report.csv"'
            ),
            "Cache-Control": "no-store",
        },
    )
