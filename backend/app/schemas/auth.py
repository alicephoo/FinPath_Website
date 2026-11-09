"""Pydantic request models for authentication endpoints."""

from __future__ import annotations

import re

from pydantic import BaseModel, ConfigDict, Field, field_validator

from app.core.config import settings

_EMAIL_PATTERN = re.compile(
    r"^[A-Za-z0-9.!#$%&'*+/=?^_`{|}~-]+@"
    r"[A-Za-z0-9](?:[A-Za-z0-9-]*[A-Za-z0-9])?"
    r"(?:\.[A-Za-z0-9](?:[A-Za-z0-9-]*[A-Za-z0-9])?)+$"
)


def _validate_email(value: str) -> str:
    normalized = value.strip().lower()
    if (
        len(normalized) > 320
        or not _EMAIL_PATTERN.fullmatch(normalized)
        or ".." in normalized
    ):
        raise ValueError("Enter a valid email address.")
    return normalized


def _validate_password(value: str) -> str:
    if len(value) < settings.password_min_length:
        raise ValueError(
            f"Password must be at least {settings.password_min_length} characters."
        )
    if len(value) > 1024:
        raise ValueError("Password is too long.")
    return value


class _StrictModel(BaseModel):
    model_config = ConfigDict(extra="forbid", str_strip_whitespace=False)


class RegistrationRequest(_StrictModel):
    email: str
    password: str

    @field_validator("email")
    @classmethod
    def validate_email(cls, value: str) -> str:
        return _validate_email(value)

    @field_validator("password")
    @classmethod
    def validate_password(cls, value: str) -> str:
        return _validate_password(value)


class LoginRequest(_StrictModel):
    email: str
    password: str = Field(min_length=1, max_length=1024)

    @field_validator("email")
    @classmethod
    def validate_email(cls, value: str) -> str:
        return _validate_email(value)


class EmailRequest(_StrictModel):
    email: str

    @field_validator("email")
    @classmethod
    def validate_email(cls, value: str) -> str:
        return _validate_email(value)


class TokenRequest(_StrictModel):
    token: str = Field(min_length=16, max_length=512)


class PasswordResetConsumeRequest(_StrictModel):
    token: str = Field(min_length=16, max_length=512)
    new_password: str

    @field_validator("new_password")
    @classmethod
    def validate_password(cls, value: str) -> str:
        return _validate_password(value)


class ChangePasswordRequest(_StrictModel):
    current_password: str = Field(min_length=1, max_length=1024)
    new_password: str

    @field_validator("new_password")
    @classmethod
    def validate_password(cls, value: str) -> str:
        return _validate_password(value)
