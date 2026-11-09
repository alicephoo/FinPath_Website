from datetime import date, datetime
from uuid import UUID as UUIDValue

from sqlalchemy import (
    BigInteger,
    Boolean,
    CheckConstraint,
    Date,
    DateTime,
    ForeignKey,
    ForeignKeyConstraint,
    Index,
    String,
    text,
    UniqueConstraint,
    desc,
    func,
    text,
)
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import Mapped, mapped_column

from app.db.base import Base


class TransactionCategory(Base):
    __tablename__ = "transaction_categories"
    __table_args__ = (
        CheckConstraint("type IN ('income', 'expense')", name="type"),
        UniqueConstraint("id", "type"),
        Index(
            "uq_transaction_categories_type_name_lower",
            "type",
            func.lower(text("name")),
            unique=True,
        ),
    )

    id: Mapped[UUIDValue] = mapped_column(
        UUID(as_uuid=True),
        primary_key=True,
        nullable=False,
    )
    name: Mapped[str] = mapped_column(String(80), nullable=False)
    type: Mapped[str] = mapped_column(String(8), nullable=False)
    is_active: Mapped[bool] = mapped_column(
        Boolean,
        nullable=False,
        server_default=text("true"),
    )


class Transaction(Base):
    __tablename__ = "transactions"
    __table_args__ = (
        UniqueConstraint("id", "user_id"),
        ForeignKeyConstraint(
            ["category_id", "type"],
            ["transaction_categories.id", "transaction_categories.type"],
            ondelete="RESTRICT",
        ),
        CheckConstraint("type IN ('income', 'expense')", name="type"),
        CheckConstraint("amount_kyat > 0", name="amount_positive"),
        Index(
            "ix_transactions_user_id_transaction_date_id",
            "user_id",
            desc("transaction_date"),
            "id",
        ),
        Index(
            "ix_transactions_user_id_category_id_transaction_date",
            "user_id",
            "category_id",
            desc("transaction_date"),
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
    category_id: Mapped[UUIDValue] = mapped_column(
        UUID(as_uuid=True),
        nullable=False,
    )
    type: Mapped[str] = mapped_column(String(8), nullable=False)
    description: Mapped[str] = mapped_column(String(80), nullable=False)
    amount_kyat: Mapped[int] = mapped_column(BigInteger, nullable=False)
    transaction_date: Mapped[date] = mapped_column(Date, nullable=False)
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
