"""Transactions and predefined-category API routes."""

from __future__ import annotations

from typing import Annotated
from uuid import UUID

from fastapi import APIRouter, Depends, Header, Query
from fastapi.responses import JSONResponse, Response
from sqlalchemy.orm import Session

from app.api.dependencies import get_current_auth
from app.db.session import get_db
from app.schemas.transactions import (
    TransactionCategoryListResponse,
    TransactionCreateRequest,
    TransactionListResponse,
    TransactionResponse,
    TransactionType,
    TransactionUpdateRequest,
)
from app.services import transactions as transaction_service
from app.services.auth import AuthContext

router = APIRouter(prefix="/api/v1", tags=["transactions"])

IdempotencyKey = Annotated[str | None, Header(alias="Idempotency-Key")]
IfMatch = Annotated[str | None, Header(alias="If-Match")]


@router.get(
    "/transaction-categories",
    response_model=TransactionCategoryListResponse,
)
def list_transaction_categories(
    type: TransactionType | None = None,
    context: AuthContext = Depends(get_current_auth),
    db: Session = Depends(get_db),
) -> dict[str, object]:
    del context
    return {
        "data": transaction_service.list_categories(
            db,
            transaction_type=type,
        )
    }


@router.get("/transactions", response_model=TransactionListResponse)
def list_transactions(
    type: TransactionType | None = None,
    category_id: UUID | None = None,
    from_date: str | None = Query(default=None, alias="from"),
    to_date: str | None = Query(default=None, alias="to"),
    q: str | None = Query(default=None, max_length=200),
    limit: int = Query(default=50, ge=1, le=100),
    cursor: str | None = Query(default=None, max_length=512),
    context: AuthContext = Depends(get_current_auth),
    db: Session = Depends(get_db),
) -> dict[str, object]:
    return transaction_service.list_transactions(
        db,
        context=context,
        transaction_type=type,
        category_id=category_id,
        from_date=transaction_service.parse_query_date(from_date, "from"),
        to_date=transaction_service.parse_query_date(to_date, "to"),
        q=q,
        limit=limit,
        cursor=cursor,
    )


@router.post(
    "/transactions",
    status_code=201,
    response_model=TransactionResponse,
)
def create_transaction(
    payload: TransactionCreateRequest,
    idempotency_key: IdempotencyKey = None,
    context: AuthContext = Depends(get_current_auth),
    db: Session = Depends(get_db),
) -> JSONResponse:
    result = transaction_service.create_transaction(
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
    "/transactions/{transaction_id}",
    response_model=TransactionResponse,
)
def get_transaction(
    transaction_id: UUID,
    context: AuthContext = Depends(get_current_auth),
    db: Session = Depends(get_db),
) -> JSONResponse:
    view = transaction_service.get_transaction(
        db,
        context=context,
        transaction_id=transaction_id,
    )
    return JSONResponse(
        status_code=200,
        content=view.body,
        headers={"ETag": view.etag},
    )


@router.patch(
    "/transactions/{transaction_id}",
    response_model=TransactionResponse,
)
def update_transaction(
    transaction_id: UUID,
    payload: TransactionUpdateRequest,
    if_match: IfMatch = None,
    context: AuthContext = Depends(get_current_auth),
    db: Session = Depends(get_db),
) -> JSONResponse:
    view = transaction_service.update_transaction(
        db,
        context=context,
        transaction_id=transaction_id,
        payload=payload,
        if_match=if_match,
    )
    return JSONResponse(
        status_code=200,
        content=view.body,
        headers={"ETag": view.etag},
    )


@router.delete("/transactions/{transaction_id}", status_code=204)
def delete_transaction(
    transaction_id: UUID,
    if_match: IfMatch = None,
    idempotency_key: IdempotencyKey = None,
    context: AuthContext = Depends(get_current_auth),
    db: Session = Depends(get_db),
) -> Response:
    result = transaction_service.delete_transaction(
        db,
        context=context,
        transaction_id=transaction_id,
        if_match=if_match,
        idempotency_key=idempotency_key or "",
    )
    return Response(status_code=result.status_code, headers=result.headers)
