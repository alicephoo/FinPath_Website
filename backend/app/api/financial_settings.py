"""Authenticated starting-balance financial settings API routes."""

from __future__ import annotations

from typing import Annotated

from fastapi import APIRouter, Depends, Header
from fastapi.responses import JSONResponse
from sqlalchemy.orm import Session

from app.api.dependencies import get_current_auth
from app.db.session import get_db
from app.schemas.financial_settings import (
    FinancialSettingsResponse,
    FinancialSettingsUpdateRequest,
)
from app.services import financial_settings as financial_settings_service
from app.services.auth import AuthContext

router = APIRouter(prefix="/api/v1", tags=["financial-settings"])

IfMatch = Annotated[str | None, Header(alias="If-Match")]


@router.get(
    "/users/me/financial-settings",
    response_model=FinancialSettingsResponse,
)
def get_financial_settings(
    context: AuthContext = Depends(get_current_auth),
    db: Session = Depends(get_db),
) -> JSONResponse:
    view = financial_settings_service.get_settings(db, context=context)
    return JSONResponse(
        status_code=200,
        content=view.body,
        headers={"ETag": view.etag},
    )


@router.put(
    "/users/me/financial-settings",
    response_model=FinancialSettingsResponse,
)
def update_financial_settings(
    payload: FinancialSettingsUpdateRequest,
    if_match: IfMatch = None,
    context: AuthContext = Depends(get_current_auth),
    db: Session = Depends(get_db),
) -> JSONResponse:
    view = financial_settings_service.update_settings(
        db,
        context=context,
        payload=payload,
        if_match=if_match,
    )
    return JSONResponse(
        status_code=200,
        content=view.body,
        headers={"ETag": view.etag},
    )
