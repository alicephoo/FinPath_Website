"""Authentication API routes."""

from __future__ import annotations

from datetime import datetime, timezone
from typing import Annotated

from fastapi import APIRouter, Depends, Header, Request, Response
from fastapi.responses import JSONResponse
from sqlalchemy.orm import Session

from app.api.dependencies import get_current_auth, require_trusted_origin
from app.core.config import settings
from app.db.session import get_db, get_registration_db
from app.schemas.auth import (
    ChangePasswordRequest,
    EmailRequest,
    LoginRequest,
    PasswordResetConsumeRequest,
    RegistrationRequest,
    TokenRequest,
)
from app.services import auth as auth_service
from app.services.email import EmailSender, get_email_sender

router = APIRouter(prefix="/api/v1/auth", tags=["authentication"])

IdempotencyKey = Annotated[
    str | None, Header(alias="Idempotency-Key")
]


def _set_refresh_cookie(
    response: Response,
    *,
    refresh_token: str,
    refresh_expires_at: datetime,
) -> None:
    remaining = int(
        (refresh_expires_at - datetime.now(timezone.utc)).total_seconds()
    )
    response.set_cookie(
        key=settings.refresh_cookie_name,
        value=refresh_token,
        max_age=max(remaining, 0),
        httponly=True,
        secure=settings.refresh_cookie_secure,
        samesite="lax",
        path=settings.refresh_cookie_path,
    )


def _clear_refresh_cookie(response: Response) -> None:
    response.delete_cookie(
        key=settings.refresh_cookie_name,
        path=settings.refresh_cookie_path,
        secure=settings.refresh_cookie_secure,
        httponly=True,
        samesite="lax",
    )


def _login_response(result: auth_service.LoginResult) -> JSONResponse:
    response = JSONResponse(status_code=200, content=result.body)
    _set_refresh_cookie(
        response,
        refresh_token=result.refresh_token,
        refresh_expires_at=result.refresh_expires_at,
    )
    return response


@router.post("/register", status_code=201)
def register(
    payload: RegistrationRequest,
    idempotency_key: IdempotencyKey = None,
    db: Session = Depends(get_registration_db),
    email_sender: EmailSender = Depends(get_email_sender),
) -> JSONResponse:
    result = auth_service.register_user(
        db,
        email=payload.email,
        password=payload.password,
        idempotency_key=idempotency_key or "",
        email_sender=email_sender,
    )
    return JSONResponse(
        status_code=result.status_code,
        content=result.body,
        headers=result.headers,
    )


@router.post("/login")
def login(
    payload: LoginRequest,
    request: Request,
    _: None = Depends(require_trusted_origin),
    db: Session = Depends(get_db),
) -> JSONResponse:
    result = auth_service.login_user(
        db,
        email=payload.email,
        password=payload.password,
    )
    return _login_response(result)


@router.post("/refresh")
def refresh(
    request: Request,
    _: None = Depends(require_trusted_origin),
    db: Session = Depends(get_db),
    csrf_token: Annotated[str | None, Header(alias="X-CSRF-Token")] = None,
) -> JSONResponse:
    result = auth_service.refresh_session(
        db,
        refresh_token=request.cookies.get(settings.refresh_cookie_name),
        csrf_token=csrf_token,
    )
    return _login_response(result)


@router.post("/logout", status_code=204)
def logout(
    request: Request,
    _: None = Depends(require_trusted_origin),
    db: Session = Depends(get_db),
    csrf_token: Annotated[str | None, Header(alias="X-CSRF-Token")] = None,
) -> Response:
    auth_service.logout_session(
        db,
        refresh_token=request.cookies.get(settings.refresh_cookie_name),
        csrf_token=csrf_token,
    )
    response = Response(status_code=204)
    _clear_refresh_cookie(response)
    return response


@router.get("/csrf")
def csrf(
    request: Request,
    _: None = Depends(require_trusted_origin),
    db: Session = Depends(get_db),
) -> dict[str, object]:
    token = auth_service.csrf_for_session(
        db,
        refresh_token=request.cookies.get(settings.refresh_cookie_name),
    )
    return {"data": {"csrf_token": token}}


@router.get("/me")
def me(
    context: auth_service.AuthContext = Depends(get_current_auth),
    db: Session = Depends(get_db),
) -> dict[str, object]:
    return {
        "data": auth_service.current_user_summary(db, context)
    }


@router.post("/change-password", status_code=204)
def change_password(
    payload: ChangePasswordRequest,
    context: auth_service.AuthContext = Depends(get_current_auth),
    db: Session = Depends(get_db),
) -> Response:
    auth_service.change_password(
        db,
        context=context,
        current_password=payload.current_password,
        new_password=payload.new_password,
    )
    return Response(status_code=204)


@router.post("/email-verification-requests", status_code=202)
def request_email_verification(
    payload: EmailRequest,
    idempotency_key: IdempotencyKey = None,
    db: Session = Depends(get_db),
    email_sender: EmailSender = Depends(get_email_sender),
) -> JSONResponse:
    result = auth_service.resend_email_verification(
        db,
        email=payload.email,
        idempotency_key=idempotency_key or "",
        email_sender=email_sender,
    )
    return JSONResponse(
        status_code=result.status_code,
        content=result.body,
        headers=result.headers,
    )


@router.post("/email-verification/consume")
def consume_email_verification(
    payload: TokenRequest,
    idempotency_key: IdempotencyKey = None,
    db: Session = Depends(get_db),
) -> JSONResponse:
    result = auth_service.consume_email_verification(
        db,
        token=payload.token,
        idempotency_key=idempotency_key or "",
    )
    return JSONResponse(
        status_code=result.status_code,
        content=result.body,
        headers=result.headers,
    )


@router.post("/password-reset-requests", status_code=202)
def request_password_reset(
    payload: EmailRequest,
    db: Session = Depends(get_db),
    email_sender: EmailSender = Depends(get_email_sender),
) -> dict[str, object]:
    auth_service.request_password_reset(
        db,
        email=payload.email,
        email_sender=email_sender,
    )
    return {"data": {"status": "accepted"}}


@router.post("/password-reset/consume")
def consume_password_reset(
    payload: PasswordResetConsumeRequest,
    idempotency_key: IdempotencyKey = None,
    db: Session = Depends(get_db),
) -> JSONResponse:
    result = auth_service.consume_password_reset(
        db,
        token=payload.token,
        new_password=payload.new_password,
        idempotency_key=idempotency_key or "",
    )
    return JSONResponse(
        status_code=result.status_code,
        content=result.body,
        headers=result.headers,
    )
