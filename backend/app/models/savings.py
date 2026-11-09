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
    SmallInteger,
    String,
    UniqueConstraint,
    desc,
    func,
    text,
)
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import Mapped, mapped_column

from app.db.base import Base


class SavingsGoal(Base):
    __tablename__ = "savings_goals"
    __table_args__ = (
        UniqueConstraint("id", "user_id"),
        CheckConstraint("target_kyat > 0", name="target_positive"),
        CheckConstraint(
            "status IN ('active', 'archived')",
            name="status",
        ),
        CheckConstraint(
            "(status = 'archived' AND archived_at IS NOT NULL) OR "
            "(status = 'active' AND archived_at IS NULL)",
            name="archive_timestamp_matches_status",
        ),
        Index(
            "ix_savings_goals_user_id_status_created_at",
            "user_id",
            "status",
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
    name: Mapped[str] = mapped_column(String(80), nullable=False)
    target_kyat: Mapped[int] = mapped_column(BigInteger, nullable=False)
    tone: Mapped[str | None] = mapped_column(String(16), nullable=True)
    status: Mapped[str] = mapped_column(
        String(12),
        nullable=False,
        server_default=text("'active'"),
    )
    archived_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True),
        nullable=True,
    )
    revision: Mapped[int] = mapped_column(
        BigInteger,
        nullable=False,
        server_default=text("1"),
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


class SavingsPlan(Base):
    __tablename__ = "savings_plans"
    __table_args__ = (
        ForeignKeyConstraint(
            ["goal_id", "user_id"],
            ["savings_goals.id", "savings_goals.user_id"],
            ondelete="RESTRICT",
        ),
        UniqueConstraint("goal_id", name="uq_savings_plans_goal_id"),
        UniqueConstraint("id", "goal_id", "user_id"),
        CheckConstraint("duration_days BETWEEN 1 AND 365", name="duration_days"),
        CheckConstraint("daily_amount_kyat > 0", name="daily_amount_positive"),
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
    goal_id: Mapped[UUIDValue] = mapped_column(
        UUID(as_uuid=True),
        nullable=False,
    )
    start_date: Mapped[date] = mapped_column(Date, nullable=False)
    duration_days: Mapped[int] = mapped_column(SmallInteger, nullable=False)
    daily_amount_kyat: Mapped[int] = mapped_column(BigInteger, nullable=False)
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


class SavingsPlanDay(Base):
    __tablename__ = "savings_plan_days"
    __table_args__ = (
        ForeignKeyConstraint(
            ["goal_id", "user_id"],
            ["savings_goals.id", "savings_goals.user_id"],
            ondelete="RESTRICT",
        ),
        ForeignKeyConstraint(
            ["plan_id", "goal_id", "user_id"],
            ["savings_plans.id", "savings_plans.goal_id", "savings_plans.user_id"],
            ondelete="RESTRICT",
        ),
        UniqueConstraint("plan_id", "day_number"),
        UniqueConstraint("plan_id", "planned_date"),
        UniqueConstraint("user_id", "goal_id", "id"),
        CheckConstraint("day_number BETWEEN 1 AND 365", name="day_number"),
        CheckConstraint("base_amount_kyat > 0", name="base_amount_positive"),
        CheckConstraint(
            "carried_forward_kyat >= 0",
            name="carried_forward_nonnegative",
        ),
        CheckConstraint("required_amount_kyat > 0", name="required_amount_positive"),
        CheckConstraint(
            "status IN ('pending', 'completed', 'missed')",
            name="status",
        ),
        CheckConstraint(
            "(status = 'pending' AND completion_date IS NULL "
            "AND actual_amount_kyat IS NULL "
            "AND reconciled_contributions_kyat IS NULL "
            "AND unpaid_carry_forward_kyat IS NULL) OR "
            "(status = 'completed' AND completion_date IS NOT NULL "
            "AND actual_amount_kyat IS NOT NULL "
            "AND reconciled_contributions_kyat IS NOT NULL "
            "AND unpaid_carry_forward_kyat IS NOT NULL "
            "AND unpaid_carry_forward_kyat = 0) OR "
            "(status = 'missed' AND completion_date IS NULL "
            "AND actual_amount_kyat IS NULL "
            "AND reconciled_contributions_kyat IS NOT NULL "
            "AND unpaid_carry_forward_kyat IS NOT NULL)",
            name="terminal_snapshot_matches_status",
        ),
        CheckConstraint(
            "reconciled_contributions_kyat IS NULL "
            "OR reconciled_contributions_kyat >= 0",
            name="reconciled_contributions_nonnegative",
        ),
        CheckConstraint(
            "unpaid_carry_forward_kyat IS NULL OR unpaid_carry_forward_kyat >= 0",
            name="unpaid_carry_forward_nonnegative",
        ),
        Index(
            "ix_savings_plan_days_user_id_goal_id_planned_date",
            "user_id",
            "goal_id",
            "planned_date",
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
    goal_id: Mapped[UUIDValue] = mapped_column(
        UUID(as_uuid=True),
        nullable=False,
    )
    plan_id: Mapped[UUIDValue] = mapped_column(
        UUID(as_uuid=True),
        nullable=False,
    )
    day_number: Mapped[int] = mapped_column(SmallInteger, nullable=False)
    planned_date: Mapped[date] = mapped_column(Date, nullable=False)
    base_amount_kyat: Mapped[int] = mapped_column(BigInteger, nullable=False)
    carried_forward_kyat: Mapped[int] = mapped_column(BigInteger, nullable=False)
    required_amount_kyat: Mapped[int] = mapped_column(BigInteger, nullable=False)
    status: Mapped[str] = mapped_column(
        String(10),
        nullable=False,
        server_default=text("'pending'"),
    )
    completion_date: Mapped[date | None] = mapped_column(Date, nullable=True)
    actual_amount_kyat: Mapped[int | None] = mapped_column(
        BigInteger,
        nullable=True,
    )
    reconciled_contributions_kyat: Mapped[int | None] = mapped_column(
        BigInteger,
        nullable=True,
    )
    unpaid_carry_forward_kyat: Mapped[int | None] = mapped_column(
        BigInteger,
        nullable=True,
    )


class SavingsEntry(Base):
    __tablename__ = "savings_entries"
    __table_args__ = (
        ForeignKeyConstraint(
            ["goal_id", "user_id"],
            ["savings_goals.id", "savings_goals.user_id"],
            ondelete="RESTRICT",
        ),
        ForeignKeyConstraint(
            ["user_id", "goal_id", "plan_day_id"],
            [
                "savings_plan_days.user_id",
                "savings_plan_days.goal_id",
                "savings_plan_days.id",
            ],
        ),
        ForeignKeyConstraint(
            ["user_id", "goal_id", "correction_of_entry_id"],
            [
                "savings_entries.user_id",
                "savings_entries.goal_id",
                "savings_entries.id",
            ],
        ),
        UniqueConstraint("user_id", "goal_id", "id"),
        CheckConstraint(
            "entry_type IN ('contribution', 'withdrawal')",
            name="entry_type",
        ),
        CheckConstraint("amount_kyat > 0", name="amount_positive"),
        CheckConstraint(
            "(correction_of_entry_id IS NULL AND correction_reason IS NULL) OR "
            "(correction_of_entry_id IS NOT NULL AND correction_reason IS NOT NULL "
            "AND plan_day_id IS NULL)",
            name="correction_fields_consistent",
        ),
        CheckConstraint(
            "correction_reason IS NULL OR length(trim(correction_reason)) > 0",
            name="correction_reason_nonempty",
        ),
        CheckConstraint(
            "plan_day_id IS NULL OR entry_type = 'contribution'",
            name="plan_day_only_for_contributions",
        ),
        Index(
            "uq_savings_entries_correction_of_entry_id",
            "correction_of_entry_id",
            unique=True,
            postgresql_where=text("correction_of_entry_id IS NOT NULL"),
        ),
        Index(
            "ix_savings_entries_user_id_goal_id_entry_date_id",
            "user_id",
            "goal_id",
            desc("entry_date"),
            "id",
        ),
        Index(
            "ix_savings_entries_user_id_goal_id_plan_day_id",
            "user_id",
            "goal_id",
            "plan_day_id",
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
    goal_id: Mapped[UUIDValue] = mapped_column(
        UUID(as_uuid=True),
        nullable=False,
    )
    plan_day_id: Mapped[UUIDValue | None] = mapped_column(
        UUID(as_uuid=True),
        nullable=True,
    )
    entry_type: Mapped[str] = mapped_column(String(12), nullable=False)
    amount_kyat: Mapped[int] = mapped_column(BigInteger, nullable=False)
    entry_date: Mapped[date] = mapped_column(Date, nullable=False)
    note: Mapped[str | None] = mapped_column(String(100), nullable=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
        server_default=func.now(),
    )
    correction_of_entry_id: Mapped[UUIDValue | None] = mapped_column(
        UUID(as_uuid=True),
        nullable=True,
    )
    correction_reason: Mapped[str | None] = mapped_column(
        String(200),
        nullable=True,
    )
