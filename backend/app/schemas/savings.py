"""Pydantic request and response models for the Savings API."""

from __future__ import annotations

import re
from datetime import date, datetime
from typing import Any, Literal
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

SavingsGoalStatus = Literal["active", "archived"]
SavingsGoalStatusFilter = Literal["active", "archived", "all"]
SavingsEntryType = Literal["contribution", "withdrawal"]

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
        raise ValueError("Savings goal name is required.")
    if len(cleaned) > 80:
        raise ValueError("Savings goal name must be 80 characters or fewer.")
    return cleaned


def _clean_tone(value: str | None) -> str | None:
    if value is None:
        return None
    cleaned = value.strip()
    if len(cleaned) > 16:
        raise ValueError("Savings goal tone must be 16 characters or fewer.")
    return cleaned or None


def _clean_note(value: str | None) -> str | None:
    if value is None:
        return None
    cleaned = value.strip()
    if len(cleaned) > 100:
        raise ValueError("Savings entry note must be 100 characters or fewer.")
    return cleaned or None


class SavingsGoalCreateRequest(_StrictModel):
    name: str
    target_kyat: int = Field(strict=True)
    tone: str | None = None

    @field_validator("tone")
    @classmethod
    def validate_tone(cls, value: str | None) -> str | None:
        return _clean_tone(value)

    @model_validator(mode="after")
    def validate_name(self) -> "SavingsGoalCreateRequest":
        self.name = _clean_name(self.name)
        return self


class SavingsGoalUpdateRequest(_StrictModel):
    name: str | None = None
    target_kyat: int | None = Field(default=None, strict=True)
    tone: str | None = None

    @field_validator("tone")
    @classmethod
    def validate_tone(cls, value: str | None) -> str | None:
        return _clean_tone(value)

    @model_validator(mode="after")
    def validate_patch(self) -> "SavingsGoalUpdateRequest":
        if not self.model_fields_set:
            raise ValueError("Provide at least one field to update.")
        for field_name in ("name", "target_kyat"):
            if field_name in self.model_fields_set and getattr(
                self, field_name
            ) is None:
                raise ValueError(f"{field_name} cannot be null.")
        if "name" in self.model_fields_set:
            self.name = _clean_name(self.name or "")
        return self


class SavingsGoalArchiveRequest(_StrictModel):
    """Archive/restore requires an explicit confirmation flag."""

    confirm: bool = Field(default=False, strict=True)


class SavingsContributionCreateRequest(_StrictModel):
    amount_kyat: int = Field(strict=True)
    entry_date: date
    note: str | None = None

    @field_validator("entry_date", mode="before")
    @classmethod
    def validate_entry_date(cls, value: Any) -> Any:
        return _parse_date_only(value)

    @field_validator("note")
    @classmethod
    def validate_note(cls, value: str | None) -> str | None:
        return _clean_note(value)


class SavingsWithdrawalCreateRequest(SavingsContributionCreateRequest):
    """Withdrawal uses the same strictly validated entry fields."""


class SavingsGoalResource(BaseModel):
    id: UUID
    name: str
    target_kyat: int
    tone: str | None
    status: SavingsGoalStatus
    actual_balance_kyat: int
    remaining_kyat: int
    created_at: datetime
    updated_at: datetime


class SavingsGoalResponse(BaseModel):
    data: SavingsGoalResource


class SavingsGoalListResponse(BaseModel):
    data: list[SavingsGoalResource]


class SavingsEntryResource(BaseModel):
    id: UUID
    entry_type: SavingsEntryType
    amount_kyat: int
    entry_date: date
    note: str | None
    plan_day_id: UUID | None
    correction_of_entry_id: UUID | None
    correction_reason: str | None
    created_at: datetime


class SavingsEntryResponse(BaseModel):
    data: SavingsEntryResource


class SavingsEntryListResponse(BaseModel):
    data: list[SavingsEntryResource]
