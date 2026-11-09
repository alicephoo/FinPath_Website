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


class Trip(Base):
    __tablename__ = "trips"
    __table_args__ = (
        UniqueConstraint("id", "user_id"),
        CheckConstraint("end_date >= start_date", name="date_order"),
        CheckConstraint(
            "status IN ('active', 'completed', 'cancelled')",
            name="status_valid",
        ),
        Index(
            "ix_trips_user_id_start_date_end_date",
            "user_id",
            "start_date",
            "end_date",
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
    name: Mapped[str] = mapped_column(String(80), nullable=False)
    destination: Mapped[str] = mapped_column(String(100), nullable=False)
    start_date: Mapped[date] = mapped_column(Date, nullable=False)
    end_date: Mapped[date] = mapped_column(Date, nullable=False)
    status: Mapped[str] = mapped_column(
        String(16),
        nullable=False,
        server_default=text("'active'"),
    )
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


class TripCategoryBudget(Base):
    __tablename__ = "trip_category_budgets"
    __table_args__ = (
        ForeignKeyConstraint(
            ["trip_id", "user_id"],
            ["trips.id", "trips.user_id"],
            ondelete="CASCADE",
        ),
        UniqueConstraint("trip_id", "id"),
        CheckConstraint("budget_kyat >= 0", name="budget_nonnegative"),
        Index("ix_trip_category_budgets_user_id_trip_id", "user_id", "trip_id"),
        Index(
            "uq_trip_category_budgets_trip_category_lower",
            "trip_id",
            func.lower(text("category")),
            unique=True,
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
    trip_id: Mapped[UUIDValue] = mapped_column(
        UUID(as_uuid=True),
        nullable=False,
    )
    category: Mapped[str] = mapped_column(String(50), nullable=False)
    budget_kyat: Mapped[int] = mapped_column(BigInteger, nullable=False)
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


class TripSpendingEntry(Base):
    __tablename__ = "trip_spending_entries"
    __table_args__ = (
        ForeignKeyConstraint(
            ["trip_id", "user_id"],
            ["trips.id", "trips.user_id"],
            ondelete="CASCADE",
        ),
        ForeignKeyConstraint(
            ["trip_id", "category_budget_id"],
            ["trip_category_budgets.trip_id", "trip_category_budgets.id"],
        ),
        CheckConstraint("amount_kyat > 0", name="amount_positive"),
        Index(
            "ix_trip_spending_entries_user_id_trip_id_spending_date",
            "user_id",
            "trip_id",
            desc("spending_date"),
        ),
        Index(
            "ix_trip_spending_entries_trip_id_category_budget_id",
            "trip_id",
            "category_budget_id",
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
    trip_id: Mapped[UUIDValue] = mapped_column(
        UUID(as_uuid=True),
        nullable=False,
    )
    category_budget_id: Mapped[UUIDValue | None] = mapped_column(
        UUID(as_uuid=True),
        nullable=True,
    )
    category: Mapped[str] = mapped_column(String(100), nullable=False)
    description: Mapped[str] = mapped_column(String(100), nullable=False)
    amount_kyat: Mapped[int] = mapped_column(BigInteger, nullable=False)
    spending_date: Mapped[date] = mapped_column(Date, nullable=False)
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
