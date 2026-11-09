"""Pydantic request and response models for the Loans API."""

from __future__ import annotations

import re
from datetime import date, datetime
from typing import Any, Literal
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

CorrectionType = Literal["reversal"]

_DATE_PATTERN = re.compile(r"^\d{4}-\d{2}-\d{2}$")


def _parse_date_only(value: Any) -> Any:
    """Accept only a real ``YYYY-MM-DD`` calendar date."""
    if value is None:
        return value
    if not isinstance(value, str) or not _DATE_PATTERN.fullmatch(value):
        raise ValueError("Use a valid YYYY-MM-DD date.")
    try:
        return date.fromisoformat(value)
    except ValueError:
        raise ValueError("Use a valid calendar date.") from None


class _StrictModel(BaseModel):
    model_config = ConfigDict(extra="forbid", str_strip_whitespace=False)


def _clean_name(value: str) -> str:
    cleaned = value.strip()
    if not cleaned:
        raise ValueError("Loan name is required.")
    if len(cleaned) > 70:
        raise ValueError("Loan name must be 70 characters or fewer.")
    return cleaned


def _clean_note(value: str | None) -> str | None:
    if value is None:
        return None
    cleaned = value.strip()
    if len(cleaned) > 80:
        raise ValueError("Repayment note must be 80 characters or fewer.")
    return cleaned or None


def _clean_reason(value: str) -> str:
    cleaned = value.strip()
    if not cleaned:
        raise ValueError("Correction reason is required.")
    if len(cleaned) > 200:
        raise ValueError("Correction reason must be 200 characters or fewer.")
    return cleaned


class LoanCreateRequest(_StrictModel):
    name: str
    principal_kyat: int = Field(strict=True)
    start_date: date

    @field_validator("start_date", mode="before")
    @classmethod
    def validate_start_date(cls, value: Any) -> Any:
        return _parse_date_only(value)

    @model_validator(mode="after")
    def validate_name(self) -> "LoanCreateRequest":
        self.name = _clean_name(self.name)
        return self


class LoanUpdateRequest(_StrictModel):
    name: str | None = None
    principal_kyat: int | None = Field(default=None, strict=True)
    start_date: date | None = None

    @field_validator("start_date", mode="before")
    @classmethod
    def validate_start_date(cls, value: Any) -> Any:
        return _parse_date_only(value)

    @model_validator(mode="after")
    def validate_patch(self) -> "LoanUpdateRequest":
        if not self.model_fields_set:
            raise ValueError("Provide at least one field to update.")
        for field_name in self.model_fields_set:
            if getattr(self, field_name) is None:
                raise ValueError(f"{field_name} cannot be null.")
        if "name" in self.model_fields_set:
            self.name = _clean_name(self.name or "")
        return self


class RepaymentCreateRequest(_StrictModel):
    amount_kyat: int = Field(strict=True)
    repayment_date: date
    note: str | None = None

    @field_validator("repayment_date", mode="before")
    @classmethod
    def validate_repayment_date(cls, value: Any) -> Any:
        return _parse_date_only(value)

    @field_validator("note")
    @classmethod
    def validate_note(cls, value: str | None) -> str | None:
        return _clean_note(value)


class CorrectionCreateRequest(_StrictModel):
    reason: str

    @field_validator("reason")
    @classmethod
    def validate_reason(cls, value: str) -> str:
        return _clean_reason(value)


class LoanResource(BaseModel):
    id: UUID
    name: str
    principal_kyat: int
    start_date: date
    total_repaid_kyat: int
    outstanding_principal_kyat: int
    created_at: datetime
    updated_at: datetime


class LoanResponse(BaseModel):
    data: LoanResource


class LoanListResponse(BaseModel):
    data: list[LoanResource]


class RepaymentResource(BaseModel):
    id: UUID
    amount_kyat: int
    repayment_date: date
    note: str | None
    created_at: datetime
    corrected: bool


class RepaymentResponse(BaseModel):
    data: RepaymentResource


class RepaymentListResponse(BaseModel):
    data: list[RepaymentResource]


class CorrectionResource(BaseModel):
    id: UUID
    original_repayment_id: UUID
    amount_kyat: int
    type: CorrectionType
    reason: str
    created_by_user_id: UUID
    created_at: datetime


class CorrectionResponseData(BaseModel):
    original_repayment: RepaymentResource
    correction: CorrectionResource
    outstanding_principal_kyat: int


class CorrectionResponse(BaseModel):
    data: CorrectionResponseData
