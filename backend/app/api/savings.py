"""Savings goal and contribution API routes."""

from __future__ import annotations

from typing import Annotated
from uuid import UUID

from fastapi import APIRouter, Depends, Header, Query
from fastapi.responses import JSONResponse
from sqlalchemy.orm import Session

from app.api.dependencies import get_current_auth
from app.db.session import get_db
from app.schemas.savings import (
    SavingsContributionCreateRequest,
    SavingsEntryListResponse,
    SavingsEntryResponse,
    SavingsGoalCreateRequest,
    SavingsGoalListResponse,
    SavingsGoalResponse,
    SavingsGoalStatusFilter,
    SavingsGoalUpdateRequest,
    SavingsWithdrawalCreateRequest,
)
from app.services import savings as savings_service
from app.services.auth import AuthContext

router = APIRouter(prefix="/api/v1", tags=["savings"])

IdempotencyKey = Annotated[str | None, Header(alias="Idempotency-Key")]
IfMatch = Annotated[str | None, Header(alias="If-Match")]


@router.get("/savings/goals", response_model=SavingsGoalListResponse)
def list_savings_goals(
    status: SavingsGoalStatusFilter = Query(default="active"),
    context: AuthContext = Depends(get_current_auth),
    db: Session = Depends(get_db),
) -> dict[str, object]:
    return savings_service.list_goals(
        db,
        context=context,
        status=status,
    )


@router.post(
    "/savings/goals",
    status_code=201,
    response_model=SavingsGoalResponse,
)
def create_savings_goal(
    payload: SavingsGoalCreateRequest,
    idempotency_key: IdempotencyKey = None,
    context: AuthContext = Depends(get_current_auth),
    db: Session = Depends(get_db),
) -> JSONResponse:
    result = savings_service.create_goal(
        db,
        context=context,
        payload=payload,
        idempotency_key=idempotency_key or "",
    )
    return JSONResponse(
        status_code=result.status_code,
        content=result.body,
        headers=result.headers,
    )


@router.get(
    "/savings/goals/{goal_id}",
    response_model=SavingsGoalResponse,
)
def get_savings_goal(
    goal_id: UUID,
    context: AuthContext = Depends(get_current_auth),
    db: Session = Depends(get_db),
) -> JSONResponse:
    view = savings_service.get_goal(
        db,
        context=context,
        goal_id=goal_id,
    )
    return JSONResponse(
        status_code=200,
        content=view.body,
        headers={"ETag": view.etag},
    )


@router.patch(
    "/savings/goals/{goal_id}",
    response_model=SavingsGoalResponse,
)
def update_savings_goal(
    goal_id: UUID,
    payload: SavingsGoalUpdateRequest,
    if_match: IfMatch = None,
    context: AuthContext = Depends(get_current_auth),
    db: Session = Depends(get_db),
) -> JSONResponse:
    view = savings_service.update_goal(
        db,
        context=context,
        goal_id=goal_id,
        payload=payload,
        if_match=if_match,
    )
    return JSONResponse(
        status_code=200,
        content=view.body,
        headers={"ETag": view.etag},
    )


@router.get(
    "/savings/goals/{goal_id}/entries",
    response_model=SavingsEntryListResponse,
)
def list_savings_entries(
    goal_id: UUID,
    context: AuthContext = Depends(get_current_auth),
    db: Session = Depends(get_db),
) -> dict[str, object]:
    return savings_service.list_entries(
        db,
        context=context,
        goal_id=goal_id,
    )


@router.post(
    "/savings/goals/{goal_id}/entries/contributions",
    status_code=201,
    response_model=SavingsEntryResponse,
)
def create_savings_contribution(
    goal_id: UUID,
    payload: SavingsContributionCreateRequest,
    idempotency_key: IdempotencyKey = None,
    context: AuthContext = Depends(get_current_auth),
    db: Session = Depends(get_db),
) -> JSONResponse:
    result = savings_service.create_contribution(
        db,
        context=context,
        goal_id=goal_id,
        payload=payload,
        idempotency_key=idempotency_key or "",
    )
    return JSONResponse(
        status_code=result.status_code,
        content=result.body,
        headers=result.headers,
    )

@router.post(
    "/savings/goals/{goal_id}/entries/withdrawals",
    status_code=201,
    response_model=SavingsEntryResponse,
)
def create_savings_withdrawal(
    goal_id: UUID,
    payload: SavingsWithdrawalCreateRequest,
    idempotency_key: IdempotencyKey = None,
    context: AuthContext = Depends(get_current_auth),
    db: Session = Depends(get_db),
) -> JSONResponse:
    result = savings_service.create_withdrawal(
        db,
        context=context,
        goal_id=goal_id,
        payload=payload,
        idempotency_key=idempotency_key or "",
    )
    return JSONResponse(
        status_code=result.status_code,
        content=result.body,
        headers=result.headers,
    )
