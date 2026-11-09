"""Reusable FastAPI authentication and browser-origin dependencies."""

from __future__ import annotations

from typing import Annotated

from fastapi import Depends, Header
from sqlalchemy.orm import Session

from app.api.errors import ApiError
from app.core.config import settings
from app.db.session import get_db
from app.services.auth import AuthContext, authenticate_access_token


def require_trusted_origin(
    origin: Annotated[str | None, Header(alias="Origin")] = None,
    sec_fetch_site: Annotated[
        str | None, Header(alias="Sec-Fetch-Site")
    ] = None,
) -> None:
    if origin is not None:
        if origin not in settings.frontend_origin_list:
            raise ApiError(
                403,
                "origin_not_allowed",
                "The request origin is not allowed.",
            )
        if sec_fetch_site is not None and sec_fetch_site not in {
            "same-origin",
            "same-site",
            "none",
        }:
            raise ApiError(
                403,
                "origin_not_allowed",
                "The request origin is not allowed.",
            )
        return

    # Same-origin browser GETs (for example /auth/csrf) may omit Origin.
    # Sec-Fetch-Site is browser-controlled and cannot be set by page scripts,
    # so this is limited to requests the browser explicitly marks same-origin.
    if sec_fetch_site == "same-origin":
        return

    raise ApiError(
        403,
        "origin_not_allowed",
        "The request origin is not allowed.",
    )


def get_current_auth(
    authorization: Annotated[str | None, Header(alias="Authorization")] = None,
    db: Session = Depends(get_db),
) -> AuthContext:
    if not authorization or not authorization.startswith("Bearer "):
        raise ApiError(
            401,
            "authentication_required",
            "Authentication is required.",
        )
    token = authorization.removeprefix("Bearer ").strip()
    if not token:
        raise ApiError(
            401,
            "authentication_required",
            "Authentication is required.",
        )
    return authenticate_access_token(db, token)
