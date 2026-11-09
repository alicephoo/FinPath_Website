"""Pydantic request and response models for the Transactions API."""

from __future__ import annotations

import re
from datetime import date, datetime
from typing import Any, Literal
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

TransactionType = Literal["income", "expense"]

_DATE_PATTERN = re.compile(r"^\d{4}-\d{2}-\d{2}$")


def _parse_date_only(value: Any) -> Any:
    """Accept only a real ``YYYY-MM-DD`` calendar date.

    Pydantic's ``date`` parser also accepts midnight ISO datetimes. Financial
    records use local-calendar date-only values, so reject anything else rather
    than silently truncating a timestamp.
    """
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


def _clean_description(value: str) -> str:
    cleaned = value.strip()
    if not cleaned:
        raise ValueError("Description is required.")
    if len(cleaned) > 80:
        raise ValueError("Description must be 80 characters or fewer.")
    return cleaned


class TransactionCreateRequest(_StrictModel):
    description: str
    type: TransactionType
    category_id: UUID
    # ``strict`` rejects JSON floats, strings, and booleans. Whole-Kyat values
    # must be sent as JSON integers.
    amount_kyat: int = Field(strict=True)
    transaction_date: date

    @field_validator("transaction_date", mode="before")
    @classmethod
    def validate_transaction_date(cls, value: Any) -> Any:
        return _parse_date_only(value)

    @model_validator(mode="after")
    def validate_description(self) -> "TransactionCreateRequest":
        self.description = _clean_description(self.description)
        return self


class TransactionUpdateRequest(_StrictModel):
    description: str | None = None
    type: TransactionType | None = None
    category_id: UUID | None = None
    amount_kyat: int | None = Field(default=None, strict=True)
    transaction_date: date | None = None

    @field_validator("transaction_date", mode="before")
    @classmethod
    def validate_transaction_date(cls, value: Any) -> Any:
        return _parse_date_only(value)

    @model_validator(mode="after")
    def validate_patch(self) -> "TransactionUpdateRequest":
        if not self.model_fields_set:
            raise ValueError("Provide at least one field to update.")
        for field_name in self.model_fields_set:
            if getattr(self, field_name) is None:
                raise ValueError(f"{field_name} cannot be null.")
        if "description" in self.model_fields_set:
            self.description = _clean_description(self.description or "")
        return self


class TransactionResource(BaseModel):
    id: UUID
    description: str
    type: TransactionType
    category_id: UUID
    category: str
    amount_kyat: int
    transaction_date: date
    created_at: datetime
    updated_at: datetime


class TransactionResponse(BaseModel):
    data: TransactionResource


class TransactionPagination(BaseModel):
    limit: int
    next_cursor: str | None


class TransactionListResponse(BaseModel):
    data: list[TransactionResource]
    pagination: TransactionPagination


class TransactionCategoryResource(BaseModel):
    id: UUID
    name: str
    type: TransactionType


class TransactionCategoryListResponse(BaseModel):
    data: list[TransactionCategoryResource]
