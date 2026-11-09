"""Pydantic response models for the owner-scoped report endpoints."""

from __future__ import annotations

from datetime import date

from pydantic import BaseModel, ConfigDict, Field


class CashFlowMonthResource(BaseModel):
    month: str
    income_kyat: int
    expense_kyat: int


class CashFlowReportData(BaseModel):
    model_config = ConfigDict(populate_by_name=True)

    from_date: date = Field(alias="from")
    to_date: date = Field(alias="to")
    months: list[CashFlowMonthResource]


class CashFlowReportResponse(BaseModel):
    data: CashFlowReportData


class ExpenseCategoryResource(BaseModel):
    category: str
    expense_kyat: int


class ExpenseCategoryReportData(BaseModel):
    model_config = ConfigDict(populate_by_name=True)

    from_date: date = Field(alias="from")
    to_date: date = Field(alias="to")
    categories: list[ExpenseCategoryResource]


class ExpenseCategoryReportResponse(BaseModel):
    data: ExpenseCategoryReportData
