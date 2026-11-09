"""Pydantic request and response models for the Financial Settings API."""

from __future__ import annotations

import re
from datetime import date
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

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


class FinancialSettingsUpdateRequest(_StrictModel):
    # Starting balance is a setting, not an individual financial entry. It may
    # be zero or negative; only safe JSON integer bounds are enforced by the
    # service. ``strict`` rejects floats, strings, and booleans.
    starting_balance_kyat: int | None = Field(strict=True)
    effective_date: date | None

    @field_validator("effective_date", mode="before")
    @classmethod
    def validate_effective_date(cls, value: Any) -> Any:
        return _parse_date_only(value)

    @model_validator(mode="after")
    def validate_pair(self) -> "FinancialSettingsUpdateRequest":
        if (self.starting_balance_kyat is None) != (
            self.effective_date is None
        ):
            raise ValueError(
                "Starting balance and effective date must both be set or both "
                "be null."
            )
        return self


class FinancialSettingsResource(BaseModel):
    """The caller's starting-balance setting and derived balance status.

    The fields match the Dashboard account-balance resource so clients can
    consume the same shape in both places.
    """

    status: Literal["configured", "not_configured"]
    starting_balance_kyat: int | None
    effective_date: date | None
    amount_kyat: int | None
    as_of_date: date


class FinancialSettingsResponse(BaseModel):
    data: FinancialSettingsResource
