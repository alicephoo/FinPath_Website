"""Pydantic response models for the Dashboard API."""

from __future__ import annotations

from datetime import date
from typing import Literal

from pydantic import BaseModel

from app.schemas.savings import SavingsGoalResource
from app.schemas.transactions import TransactionResource


class DashboardAccountBalanceResource(BaseModel):
    status: Literal["configured", "not_configured"]
    starting_balance_kyat: int | None
    effective_date: date | None
    amount_kyat: int | None
    as_of_date: date


class DashboardOverviewData(BaseModel):
    account_balance: DashboardAccountBalanceResource
    all_time_income_kyat: int
    all_time_expense_kyat: int
    travel_expense_kyat: int
    total_expense_kyat: int
    current_month_income_kyat: int
    current_month_expense_kyat: int
    outstanding_loan_principal_kyat: int
    active_loan_count: int
    loan_repayments_kyat: int
    actual_savings_balance_kyat: int
    savings_target_kyat: int
    active_savings_goal_count: int
    savings_goals: list[SavingsGoalResource]
    recent_transactions: list[TransactionResource]


class DashboardOverviewResponse(BaseModel):
    data: DashboardOverviewData
