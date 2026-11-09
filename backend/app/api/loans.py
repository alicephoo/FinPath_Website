"""Loans and repayment API routes."""

from __future__ import annotations

from typing import Annotated
from uuid import UUID

from fastapi import APIRouter, Depends, Header
from fastapi.responses import JSONResponse
from sqlalchemy.orm import Session

from app.api.dependencies import get_current_auth
from app.db.session import get_db
from app.schemas.loans import (
    CorrectionCreateRequest,
    CorrectionResponse,
    LoanCreateRequest,
    LoanListResponse,
    LoanResponse,
    LoanUpdateRequest,
    RepaymentCreateRequest,
    RepaymentListResponse,
    RepaymentResponse,
)
from app.services import loans as loan_service
from app.services.auth import AuthContext

router = APIRouter(prefix="/api/v1", tags=["loans"])

IdempotencyKey = Annotated[str | None, Header(alias="Idempotency-Key")]
IfMatch = Annotated[str | None, Header(alias="If-Match")]


@router.get("/loans", response_model=LoanListResponse)
def list_loans(
    context: AuthContext = Depends(get_current_auth),
    db: Session = Depends(get_db),
) -> dict[str, object]:
    return loan_service.list_loans(db, context=context)


@router.post("/loans", status_code=201, response_model=LoanResponse)
def create_loan(
    payload: LoanCreateRequest,
    idempotency_key: IdempotencyKey = None,
    context: AuthContext = Depends(get_current_auth),
    db: Session = Depends(get_db),
) -> JSONResponse:
    result = loan_service.create_loan(
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


@router.get("/loans/{loan_id}", response_model=LoanResponse)
def get_loan(
    loan_id: UUID,
    context: AuthContext = Depends(get_current_auth),
    db: Session = Depends(get_db),
) -> JSONResponse:
    view = loan_service.get_loan(
        db,
        context=context,
        loan_id=loan_id,
    )
    return JSONResponse(
        status_code=200,
        content=view.body,
        headers={"ETag": view.etag},
    )


@router.patch("/loans/{loan_id}", response_model=LoanResponse)
def update_loan(
    loan_id: UUID,
    payload: LoanUpdateRequest,
    if_match: IfMatch = None,
    context: AuthContext = Depends(get_current_auth),
    db: Session = Depends(get_db),
) -> JSONResponse:
    view = loan_service.update_loan(
        db,
        context=context,
        loan_id=loan_id,
        payload=payload,
        if_match=if_match,
    )
    return JSONResponse(
        status_code=200,
        content=view.body,
        headers={"ETag": view.etag},
    )


@router.get(
    "/loans/{loan_id}/repayments",
    response_model=RepaymentListResponse,
)
def list_repayments(
    loan_id: UUID,
    context: AuthContext = Depends(get_current_auth),
    db: Session = Depends(get_db),
) -> dict[str, object]:
    return loan_service.list_repayments(
        db,
        context=context,
        loan_id=loan_id,
    )


@router.post(
    "/loans/{loan_id}/repayments",
    status_code=201,
    response_model=RepaymentResponse,
)
def create_repayment(
    loan_id: UUID,
    payload: RepaymentCreateRequest,
    idempotency_key: IdempotencyKey = None,
    context: AuthContext = Depends(get_current_auth),
    db: Session = Depends(get_db),
) -> JSONResponse:
    result = loan_service.create_repayment(
        db,
        context=context,
        loan_id=loan_id,
        payload=payload,
        idempotency_key=idempotency_key or "",
    )
    return JSONResponse(
        status_code=result.status_code,
        content=result.body,
        headers=result.headers,
    )


@router.get(
    "/loans/{loan_id}/repayments/{repayment_id}",
    response_model=RepaymentResponse,
)
def get_repayment(
    loan_id: UUID,
    repayment_id: UUID,
    context: AuthContext = Depends(get_current_auth),
    db: Session = Depends(get_db),
) -> JSONResponse:
    view = loan_service.get_repayment(
        db,
        context=context,
        loan_id=loan_id,
        repayment_id=repayment_id,
    )
    return JSONResponse(
        status_code=200,
        content=view.body,
        headers={"ETag": view.etag},
    )


@router.post(
    "/loans/{loan_id}/repayments/{repayment_id}/corrections",
    status_code=201,
    response_model=CorrectionResponse,
)
def correct_repayment(
    loan_id: UUID,
    repayment_id: UUID,
    payload: CorrectionCreateRequest,
    idempotency_key: IdempotencyKey = None,
    context: AuthContext = Depends(get_current_auth),
    db: Session = Depends(get_db),
) -> JSONResponse:
    result = loan_service.correct_repayment(
        db,
        context=context,
        loan_id=loan_id,
        repayment_id=repayment_id,
        payload=payload,
        idempotency_key=idempotency_key or "",
    )
    return JSONResponse(
        status_code=result.status_code,
        content=result.body,
        headers=result.headers,
    )
