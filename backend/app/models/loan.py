from datetime import date, datetime
from uuid import UUID as UUIDValue

from sqlalchemy import (
    BigInteger,
    CheckConstraint,
    Date,
    DateTime,
    ForeignKey,
    ForeignKeyConstraint,
    Index,
    String,
    UniqueConstraint,
    desc,
    func,
    text,
)
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import Mapped, mapped_column

from app.db.base import Base


class Loan(Base):
    __tablename__ = "loans"
    __table_args__ = (
        UniqueConstraint("id", "user_id"),
        CheckConstraint("principal_kyat > 0", name="principal_positive"),
        Index(
            "ix_loans_user_id_start_date",
            "user_id",
            desc("start_date"),
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
    name: Mapped[str] = mapped_column(String(70), nullable=False)
    principal_kyat: Mapped[int] = mapped_column(BigInteger, nullable=False)
    start_date: Mapped[date] = mapped_column(Date, nullable=False)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
        server_default=func.now(),
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
        server_default=func.now(),
    )
    revision: Mapped[int] = mapped_column(
        BigInteger,
        nullable=False,
        server_default=text("1"),
    )


class LoanRepayment(Base):
    __tablename__ = "loan_repayments"
    __table_args__ = (
        ForeignKeyConstraint(
            ["loan_id", "user_id"],
            ["loans.id", "loans.user_id"],
            ondelete="RESTRICT",
        ),
        UniqueConstraint("id", "loan_id", "user_id"),
        CheckConstraint("amount_kyat > 0", name="amount_positive"),
        Index(
            "ix_loan_repayments_user_id_loan_id_repayment_date",
            "user_id",
            "loan_id",
            desc("repayment_date"),
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
    amount_kyat: Mapped[int] = mapped_column(BigInteger, nullable=False)
    repayment_date: Mapped[date] = mapped_column(Date, nullable=False)
    note: Mapped[str | None] = mapped_column(String(80), nullable=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
        server_default=func.now(),
    )
    revision: Mapped[int] = mapped_column(
        BigInteger,
        nullable=False,
        server_default=text("1"),
    )
