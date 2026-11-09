"""Import model modules so Alembic can discover their tables."""

from app.models.auth import AdminAuditLog, AuthActionToken, AuthSession
from app.models.idempotency import IdempotencyRecord
from app.models.loan import Loan, LoanRepayment
from app.models.loan_repayment_correction import LoanRepaymentCorrection
from app.models.savings import (
    SavingsEntry,
    SavingsGoal,
    SavingsPlan,
    SavingsPlanDay,
)
from app.models.transaction import Transaction, TransactionCategory
from app.models.travel import Trip, TripCategoryBudget, TripSpendingEntry
from app.models.user import Role, User, UserFinancialSettings, UserRole

__all__ = [
    "AdminAuditLog",
    "AuthActionToken",
    "AuthSession",
    "IdempotencyRecord",
    "Loan",
    "LoanRepayment",
    "LoanRepaymentCorrection",
    "Role",
    "SavingsEntry",
    "SavingsGoal",
    "SavingsPlan",
    "SavingsPlanDay",
    "Transaction",
    "TransactionCategory",
    "Trip",
    "TripCategoryBudget",
    "TripSpendingEntry",
    "User",
    "UserFinancialSettings",
    "UserRole",
]
