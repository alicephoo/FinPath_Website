"""Trip, planned-expense, and actual-spending API routes."""

from __future__ import annotations

from typing import Annotated
from uuid import UUID

from fastapi import APIRouter, Depends, Header
from fastapi.responses import JSONResponse, Response
from sqlalchemy.orm import Session

from app.api.dependencies import get_current_auth
from app.db.session import get_db
from app.schemas.travel import (
    TripBudgetCreateRequest,
    TripBudgetListResponse,
    TripBudgetResponse,
    TripBudgetUpdateRequest,
    TripCreateRequest,
    TripDeleteRequest,
    TripImportRequest,
    TripImportResponse,
    TripListResponse,
    TripResponse,
    TripSpendingCreateRequest,
    TripSpendingListResponse,
    TripSpendingResponse,
    TripSpendingUpdateRequest,
    TripUpdateRequest,
)
from app.services import travel as travel_service
from app.services.auth import AuthContext

router = APIRouter(prefix="/api/v1", tags=["travel"])

IdempotencyKey = Annotated[str | None, Header(alias="Idempotency-Key")]
IfMatch = Annotated[str | None, Header(alias="If-Match")]


@router.get("/trips", response_model=TripListResponse)
def list_trips(
    context: AuthContext = Depends(get_current_auth),
    db: Session = Depends(get_db),
) -> dict[str, object]:
    return travel_service.list_trips(db, context=context)


@router.post("/trips", status_code=201, response_model=TripResponse)
def create_trip(
    payload: TripCreateRequest,
    idempotency_key: IdempotencyKey = None,
    context: AuthContext = Depends(get_current_auth),
    db: Session = Depends(get_db),
) -> JSONResponse:
    result = travel_service.create_trip(
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


@router.post(
    "/trips/import",
    status_code=200,
    response_model=TripImportResponse,
)
def import_trips(
    payload: TripImportRequest,
    idempotency_key: IdempotencyKey = None,
    context: AuthContext = Depends(get_current_auth),
    db: Session = Depends(get_db),
) -> JSONResponse:
    result = travel_service.import_trips(
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


@router.get("/trips/{trip_id}", response_model=TripResponse)
def get_trip(
    trip_id: UUID,
    context: AuthContext = Depends(get_current_auth),
    db: Session = Depends(get_db),
) -> JSONResponse:
    view = travel_service.get_trip(
        db,
        context=context,
        trip_id=trip_id,
    )
    return JSONResponse(
        status_code=200,
        content=view.body,
        headers={"ETag": view.etag},
    )


@router.patch("/trips/{trip_id}", response_model=TripResponse)
def update_trip(
    trip_id: UUID,
    payload: TripUpdateRequest,
    if_match: IfMatch = None,
    context: AuthContext = Depends(get_current_auth),
    db: Session = Depends(get_db),
) -> JSONResponse:
    view = travel_service.update_trip(
        db,
        context=context,
        trip_id=trip_id,
        payload=payload,
        if_match=if_match,
    )
    return JSONResponse(
        status_code=200,
        content=view.body,
        headers={"ETag": view.etag},
    )


@router.post(
    "/trips/{trip_id}/complete",
    status_code=200,
    response_model=TripResponse,
)
def complete_trip(
    trip_id: UUID,
    if_match: IfMatch = None,
    idempotency_key: IdempotencyKey = None,
    context: AuthContext = Depends(get_current_auth),
    db: Session = Depends(get_db),
) -> JSONResponse:
    result = travel_service.complete_trip(
        db,
        context=context,
        trip_id=trip_id,
        if_match=if_match,
        idempotency_key=idempotency_key or "",
    )
    return JSONResponse(
        status_code=result.status_code,
        content=result.body,
        headers=result.headers,
    )


@router.delete("/trips/{trip_id}", status_code=204)
def delete_trip(
    trip_id: UUID,
    payload: TripDeleteRequest,
    if_match: IfMatch = None,
    idempotency_key: IdempotencyKey = None,
    context: AuthContext = Depends(get_current_auth),
    db: Session = Depends(get_db),
) -> Response:
    del payload
    result = travel_service.delete_trip(
        db,
        context=context,
        trip_id=trip_id,
        if_match=if_match,
        idempotency_key=idempotency_key or "",
    )
    return Response(status_code=result.status_code, headers=result.headers)


@router.get(
    "/trips/{trip_id}/budgets",
    response_model=TripBudgetListResponse,
)
def list_trip_budgets(
    trip_id: UUID,
    context: AuthContext = Depends(get_current_auth),
    db: Session = Depends(get_db),
) -> dict[str, object]:
    return travel_service.list_budgets(
        db,
        context=context,
        trip_id=trip_id,
    )


@router.post(
    "/trips/{trip_id}/budgets",
    status_code=201,
    response_model=TripBudgetResponse,
)
def create_trip_budget(
    trip_id: UUID,
    payload: TripBudgetCreateRequest,
    idempotency_key: IdempotencyKey = None,
    context: AuthContext = Depends(get_current_auth),
    db: Session = Depends(get_db),
) -> JSONResponse:
    result = travel_service.create_budget(
        db,
        context=context,
        trip_id=trip_id,
        payload=payload,
        idempotency_key=idempotency_key or "",
    )
    return JSONResponse(
        status_code=result.status_code,
        content=result.body,
        headers=result.headers,
    )


@router.get(
    "/trips/{trip_id}/budgets/{budget_id}",
    response_model=TripBudgetResponse,
)
def get_trip_budget(
    trip_id: UUID,
    budget_id: UUID,
    context: AuthContext = Depends(get_current_auth),
    db: Session = Depends(get_db),
) -> JSONResponse:
    view = travel_service.get_budget(
        db,
        context=context,
        trip_id=trip_id,
        budget_id=budget_id,
    )
    return JSONResponse(
        status_code=200,
        content=view.body,
        headers={"ETag": view.etag},
    )


@router.patch(
    "/trips/{trip_id}/budgets/{budget_id}",
    response_model=TripBudgetResponse,
)
def update_trip_budget(
    trip_id: UUID,
    budget_id: UUID,
    payload: TripBudgetUpdateRequest,
    if_match: IfMatch = None,
    context: AuthContext = Depends(get_current_auth),
    db: Session = Depends(get_db),
) -> JSONResponse:
    view = travel_service.update_budget(
        db,
        context=context,
        trip_id=trip_id,
        budget_id=budget_id,
        payload=payload,
        if_match=if_match,
    )
    return JSONResponse(
        status_code=200,
        content=view.body,
        headers={"ETag": view.etag},
    )


@router.delete("/trips/{trip_id}/budgets/{budget_id}", status_code=204)
def delete_trip_budget(
    trip_id: UUID,
    budget_id: UUID,
    if_match: IfMatch = None,
    idempotency_key: IdempotencyKey = None,
    context: AuthContext = Depends(get_current_auth),
    db: Session = Depends(get_db),
) -> Response:
    result = travel_service.delete_budget(
        db,
        context=context,
        trip_id=trip_id,
        budget_id=budget_id,
        if_match=if_match,
        idempotency_key=idempotency_key or "",
    )
    return Response(status_code=result.status_code, headers=result.headers)


@router.get(
    "/trips/{trip_id}/spending",
    response_model=TripSpendingListResponse,
)
def list_trip_spending(
    trip_id: UUID,
    context: AuthContext = Depends(get_current_auth),
    db: Session = Depends(get_db),
) -> dict[str, object]:
    return travel_service.list_spending(
        db,
        context=context,
        trip_id=trip_id,
    )


@router.post(
    "/trips/{trip_id}/spending",
    status_code=201,
    response_model=TripSpendingResponse,
)
def create_trip_spending(
    trip_id: UUID,
    payload: TripSpendingCreateRequest,
    idempotency_key: IdempotencyKey = None,
    context: AuthContext = Depends(get_current_auth),
    db: Session = Depends(get_db),
) -> JSONResponse:
    result = travel_service.create_spending(
        db,
        context=context,
        trip_id=trip_id,
        payload=payload,
        idempotency_key=idempotency_key or "",
    )
    return JSONResponse(
        status_code=result.status_code,
        content=result.body,
        headers=result.headers,
    )


@router.get(
    "/trips/{trip_id}/spending/{spending_id}",
    response_model=TripSpendingResponse,
)
def get_trip_spending(
    trip_id: UUID,
    spending_id: UUID,
    context: AuthContext = Depends(get_current_auth),
    db: Session = Depends(get_db),
) -> JSONResponse:
    view = travel_service.get_spending(
        db,
        context=context,
        trip_id=trip_id,
        spending_id=spending_id,
    )
    return JSONResponse(
        status_code=200,
        content=view.body,
        headers={"ETag": view.etag},
    )


@router.patch(
    "/trips/{trip_id}/spending/{spending_id}",
    response_model=TripSpendingResponse,
)
def update_trip_spending(
    trip_id: UUID,
    spending_id: UUID,
    payload: TripSpendingUpdateRequest,
    if_match: IfMatch = None,
    context: AuthContext = Depends(get_current_auth),
    db: Session = Depends(get_db),
) -> JSONResponse:
    view = travel_service.update_spending(
        db,
        context=context,
        trip_id=trip_id,
        spending_id=spending_id,
        payload=payload,
        if_match=if_match,
    )
    return JSONResponse(
        status_code=200,
        content=view.body,
        headers={"ETag": view.etag},
    )


@router.delete(
    "/trips/{trip_id}/spending/{spending_id}",
    status_code=204,
)
def delete_trip_spending(
    trip_id: UUID,
    spending_id: UUID,
    if_match: IfMatch = None,
    idempotency_key: IdempotencyKey = None,
    context: AuthContext = Depends(get_current_auth),
    db: Session = Depends(get_db),
) -> Response:
    result = travel_service.delete_spending(
        db,
        context=context,
        trip_id=trip_id,
        spending_id=spending_id,
        if_match=if_match,
        idempotency_key=idempotency_key or "",
    )
    return Response(status_code=result.status_code, headers=result.headers)
