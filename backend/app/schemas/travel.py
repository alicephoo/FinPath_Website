"""Pydantic request and response models for the Travel API."""

from __future__ import annotations

import re
from datetime import date
from typing import Any, Literal
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

_DATE_PATTERN = re.compile(r"^\d{4}-\d{2}-\d{2}$")
JSON_SAFE_INTEGER_MAX = 9_007_199_254_740_991


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


def _clean_text(
    value: str,
    *,
    label: str,
    maximum: int,
    required: bool = True,
) -> str:
    cleaned = value.strip()
    if required and not cleaned:
        raise ValueError(f"{label} is required.")
    if len(cleaned) > maximum:
        raise ValueError(f"{label} must be {maximum} characters or fewer.")
    return cleaned


def _validate_safe_amount(value: int, *, label: str, allow_zero: bool) -> None:
    if not allow_zero and value <= 0:
        raise ValueError(f"{label} must be greater than zero.")
    if value < 0:
        raise ValueError(f"{label} cannot be negative.")
    if value > JSON_SAFE_INTEGER_MAX:
        raise ValueError(f"{label} is outside the supported JSON integer range.")


class TripCreateRequest(_StrictModel):
    name: str
    destination: str
    start_date: date
    end_date: date

    @field_validator("start_date", "end_date", mode="before")
    @classmethod
    def validate_dates(cls, value: Any) -> Any:
        return _parse_date_only(value)

    @model_validator(mode="after")
    def validate_trip(self) -> "TripCreateRequest":
        self.name = _clean_text(self.name, label="Trip name", maximum=80)
        self.destination = _clean_text(
            self.destination,
            label="Destination",
            maximum=100,
        )
        if self.end_date < self.start_date:
            raise ValueError(
                "The return date cannot be before the departure date."
            )
        return self


class TripUpdateRequest(_StrictModel):
    name: str | None = None
    destination: str | None = None
    start_date: date | None = None
    end_date: date | None = None

    @field_validator("start_date", "end_date", mode="before")
    @classmethod
    def validate_dates(cls, value: Any) -> Any:
        return _parse_date_only(value)

    @model_validator(mode="after")
    def validate_patch(self) -> "TripUpdateRequest":
        if not self.model_fields_set:
            raise ValueError("Provide at least one field to update.")
        for field_name in (
            "name",
            "destination",
            "start_date",
            "end_date",
        ):
            if field_name in self.model_fields_set and getattr(
                self, field_name
            ) is None:
                raise ValueError(f"{field_name} cannot be null.")
        if "name" in self.model_fields_set:
            self.name = _clean_text(
                self.name or "",
                label="Trip name",
                maximum=80,
            )
        if "destination" in self.model_fields_set:
            self.destination = _clean_text(
                self.destination or "",
                label="Destination",
                maximum=100,
            )
        return self


class TripDeleteRequest(_StrictModel):
    confirm: Literal[True]


class TripBudgetCreateRequest(_StrictModel):
    category: str
    budget_kyat: int = Field(strict=True)

    @model_validator(mode="after")
    def validate_budget(self) -> "TripBudgetCreateRequest":
        self.category = _clean_text(
            self.category,
            label="Planned expense name",
            maximum=50,
        )
        _validate_safe_amount(
            self.budget_kyat,
            label="The planned amount",
            allow_zero=True,
        )
        return self


class TripBudgetUpdateRequest(_StrictModel):
    category: str | None = None
    budget_kyat: int | None = Field(default=None, strict=True)

    @model_validator(mode="after")
    def validate_patch(self) -> "TripBudgetUpdateRequest":
        if not self.model_fields_set:
            raise ValueError("Provide at least one field to update.")
        for field_name in ("category", "budget_kyat"):
            if field_name in self.model_fields_set and getattr(
                self, field_name
            ) is None:
                raise ValueError(f"{field_name} cannot be null.")
        if "category" in self.model_fields_set:
            self.category = _clean_text(
                self.category or "",
                label="Planned expense name",
                maximum=50,
            )
        if "budget_kyat" in self.model_fields_set:
            assert self.budget_kyat is not None
            _validate_safe_amount(
                self.budget_kyat,
                label="The planned amount",
                allow_zero=True,
            )
        return self


class TripSpendingCreateRequest(_StrictModel):
    description: str
    category: str
    amount_kyat: int = Field(strict=True)
    spending_date: date
    category_budget_id: UUID | None = None

    @field_validator("spending_date", mode="before")
    @classmethod
    def validate_spending_date(cls, value: Any) -> Any:
        return _parse_date_only(value)

    @model_validator(mode="after")
    def validate_spending(self) -> "TripSpendingCreateRequest":
        self.description = _clean_text(
            self.description,
            label="Spending description",
            maximum=100,
        )
        self.category = _clean_text(
            self.category,
            label="Spending category",
            maximum=100,
        )
        _validate_safe_amount(
            self.amount_kyat,
            label="The spending amount",
            allow_zero=False,
        )
        return self


class TripSpendingUpdateRequest(_StrictModel):
    description: str | None = None
    category: str | None = None
    amount_kyat: int | None = Field(default=None, strict=True)
    spending_date: date | None = None
    category_budget_id: UUID | None = None

    @field_validator("spending_date", mode="before")
    @classmethod
    def validate_spending_date(cls, value: Any) -> Any:
        return _parse_date_only(value)

    @model_validator(mode="after")
    def validate_patch(self) -> "TripSpendingUpdateRequest":
        if not self.model_fields_set:
            raise ValueError("Provide at least one field to update.")
        for field_name in (
            "description",
            "category",
            "amount_kyat",
            "spending_date",
        ):
            if field_name in self.model_fields_set and getattr(
                self, field_name
            ) is None:
                raise ValueError(f"{field_name} cannot be null.")
        if "description" in self.model_fields_set:
            self.description = _clean_text(
                self.description or "",
                label="Spending description",
                maximum=100,
            )
        if "category" in self.model_fields_set:
            self.category = _clean_text(
                self.category or "",
                label="Spending category",
                maximum=100,
            )
        if "amount_kyat" in self.model_fields_set:
            assert self.amount_kyat is not None
            _validate_safe_amount(
                self.amount_kyat,
                label="The spending amount",
                allow_zero=False,
            )
        return self


class TripResource(BaseModel):
    id: UUID
    name: str
    destination: str
    start_date: date
    end_date: date
    status: Literal["active", "completed", "cancelled"]
    budget_kyat: int
    spending_kyat: int


class TripBudgetResource(BaseModel):
    id: UUID
    category: str
    budget_kyat: int


class TripSpendingResource(BaseModel):
    id: UUID
    description: str
    category: str
    category_budget_id: UUID | None
    amount_kyat: int
    spending_date: date


class TripResponse(BaseModel):
    data: TripResource


class TripListResponse(BaseModel):
    data: list[TripResource]


class TripBudgetResponse(BaseModel):
    data: TripBudgetResource


class TripBudgetListResponse(BaseModel):
    data: list[TripBudgetResource]


class TripSpendingResponse(BaseModel):
    data: TripSpendingResource


class TripSpendingListResponse(BaseModel):
    data: list[TripSpendingResource]


class TripImportBudget(_StrictModel):
    category: str
    budget_kyat: int = Field(strict=True)

    @model_validator(mode="after")
    def validate_budget(self) -> "TripImportBudget":
        self.category = _clean_text(
            self.category,
            label="Planned expense name",
            maximum=50,
        )
        _validate_safe_amount(
            self.budget_kyat,
            label="The planned amount",
            allow_zero=True,
        )
        return self


class TripImportSpending(_StrictModel):
    description: str
    category: str
    amount_kyat: int = Field(strict=True)
    spending_date: date
    budget_category: str | None = None

    @field_validator("spending_date", mode="before")
    @classmethod
    def validate_spending_date(cls, value: Any) -> Any:
        return _parse_date_only(value)

    @model_validator(mode="after")
    def validate_spending(self) -> "TripImportSpending":
        self.description = _clean_text(
            self.description,
            label="Spending description",
            maximum=100,
        )
        self.category = _clean_text(
            self.category,
            label="Spending category",
            maximum=100,
        )
        _validate_safe_amount(
            self.amount_kyat,
            label="The spending amount",
            allow_zero=False,
        )
        if self.budget_category is not None:
            self.budget_category = _clean_text(
                self.budget_category,
                label="Budget category",
                maximum=50,
            )
        return self


class TripImportItem(_StrictModel):
    name: str
    destination: str
    start_date: date
    end_date: date
    budgets: list[TripImportBudget] = Field(default_factory=list, max_length=500)
    spending: list[TripImportSpending] = Field(
        default_factory=list,
        max_length=500,
    )

    @field_validator("start_date", "end_date", mode="before")
    @classmethod
    def validate_dates(cls, value: Any) -> Any:
        return _parse_date_only(value)

    @model_validator(mode="after")
    def validate_trip(self) -> "TripImportItem":
        self.name = _clean_text(self.name, label="Trip name", maximum=80)
        self.destination = _clean_text(
            self.destination,
            label="Destination",
            maximum=100,
        )
        if self.end_date < self.start_date:
            raise ValueError(
                "The return date cannot be before the departure date."
            )
        return self


class TripImportRequest(_StrictModel):
    trips: list[TripImportItem] = Field(min_length=1, max_length=100)


class TripImportResult(BaseModel):
    trips_created: int
    trips_skipped_duplicates: int
    budgets_created: int
    budgets_skipped_duplicates: int
    spending_created: int
    spending_skipped_duplicates: int


class TripImportResponse(BaseModel):
    data: TripImportResult
