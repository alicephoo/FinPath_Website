from datetime import datetime
from uuid import UUID as UUIDValue

from sqlalchemy import (
    BigInteger,
    CheckConstraint,
    DateTime,
    ForeignKey,
    ForeignKeyConstraint,
    Index,
    String,
    UniqueConstraint,
    desc,
    func,
)
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import Mapped, mapped_column

from app.db.base import Base


class LoanRepaymentCorrection(Base):
    __tablename__ = "loan_repayment_corrections"
    __table_args__ = (
        ForeignKeyConstraint(
            ["loan_id", "user_id"],
            ["loans.id", "loans.user_id"],
            ondelete="RESTRICT",
        ),
        ForeignKeyConstraint(
            ["repayment_id", "loan_id", "user_id"],
            [
                "loan_repayments.id",
                "loan_repayments.loan_id",
                "loan_repayments.user_id",
            ],
            ondelete="RESTRICT",
        ),
        UniqueConstraint("repayment_id"),
        CheckConstraint("type = 'reversal'", name="type"),
        CheckConstraint("amount_kyat > 0", name="amount_positive"),
        CheckConstraint(
            "created_by_user_id = user_id",
            name="created_by_user_matches_owner",
        ),
        Index(
            "ix_loan_repayment_corrections_user_id_loan_id_created_at",
            "user_id",
            "loan_id",
            desc("created_at"),
        ),
    )

    id: Mapped[UUIDValue] = mapped_column(
        UUID(as_uuid=True),
        primary_key=True,
        nullable=False,
    )
    user_id: Mapped[UUIDValue] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("users.id", ondelete="RESTRICT"),
        nullable=False,
    )
    loan_id: Mapped[UUIDValue] = mapped_column(
        UUID(as_uuid=True),
        nullable=False,
    )
    repayment_id: Mapped[UUIDValue] = mapped_column(
        UUID(as_uuid=True),
        nullable=False,
    )
    type: Mapped[str] = mapped_column(String(16), nullable=False)
    amount_kyat: Mapped[int] = mapped_column(BigInteger, nullable=False)
    reason: Mapped[str] = mapped_column(String(200), nullable=False)
    created_by_user_id: Mapped[UUIDValue] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("users.id", ondelete="RESTRICT"),
        nullable=False,
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
        server_default=func.now(),
    )
