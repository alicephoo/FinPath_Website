"""Authenticated Dashboard API routes."""

from __future__ import annotations

from fastapi import APIRouter, Depends, Query
from sqlalchemy.orm import Session

from app.api.dependencies import get_current_auth
from app.db.session import get_db
from app.schemas.dashboard import DashboardOverviewResponse
from app.services import dashboard as dashboard_service
from app.services.auth import AuthContext

router = APIRouter(prefix="/api/v1", tags=["dashboard"])


@router.get(
    "/dashboard/overview",
    response_model=DashboardOverviewResponse,
)
def get_dashboard_overview(
    recent_limit: int = Query(
        default=dashboard_service.DEFAULT_RECENT_LIMIT,
        ge=1,
        le=dashboard_service.MAX_RECENT_LIMIT,
    ),
    context: AuthContext = Depends(get_current_auth),
    db: Session = Depends(get_db),
) -> dict[str, object]:
    return dashboard_service.get_overview(
        db,
        context=context,
        recent_limit=recent_limit,
    )
