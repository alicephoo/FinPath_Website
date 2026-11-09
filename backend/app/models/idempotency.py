from datetime import datetime
from uuid import UUID as UUIDValue

from sqlalchemy import (
    CHAR,
    CheckConstraint,
    DateTime,
    ForeignKey,
    Index,
    SmallInteger,
    String,
    UniqueConstraint,
    func,
    text,
)
from sqlalchemy.dialects.postgresql import JSONB, UUID
from sqlalchemy.orm import Mapped, mapped_column

from app.db.base import Base


class IdempotencyRecord(Base):
    __tablename__ = "idempotency_records"
    __table_args__ = (
        UniqueConstraint(
            "principal_scope_hash",
            "http_method",
            "route_key",
            "key_hash",
            name="uq_idempotency_records_scope_method_route_key",
        ),
        CheckConstraint(
            "state IN ('in_progress', 'completed')",
            name="state",
        ),
        CheckConstraint(
            "(state = 'in_progress' AND response_status IS NULL "
            "AND response_body IS NULL AND response_headers IS NULL "
            "AND completed_at IS NULL) OR "
            "(state = 'completed' AND response_status IS NOT NULL "
            "AND response_body IS NOT NULL AND response_headers IS NOT NULL "
            "AND completed_at IS NOT NULL)",
            name="response_matches_state",
        ),
        Index("ix_idempotency_records_expires_at", "expires_at"),
    )

    id: Mapped[UUIDValue] = mapped_column(
        UUID(as_uuid=True),
        primary_key=True,
        nullable=False,
    )
    owner_user_id: Mapped[UUIDValue | None] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("users.id", ondelete="SET NULL"),
        nullable=True,
    )
    principal_scope_hash: Mapped[str] = mapped_column(CHAR(64), nullable=False)
    http_method: Mapped[str] = mapped_column(String(8), nullable=False)
    route_key: Mapped[str] = mapped_column(String(200), nullable=False)
    key_hash: Mapped[str] = mapped_column(CHAR(64), nullable=False)
    request_hash: Mapped[str] = mapped_column(CHAR(64), nullable=False)
    state: Mapped[str] = mapped_column(
        String(12),
        nullable=False,
        server_default=text("'in_progress'"),
    )
    response_status: Mapped[int | None] = mapped_column(
        SmallInteger,
        nullable=True,
    )
    response_body: Mapped[object | None] = mapped_column(JSONB, nullable=True)
    response_headers: Mapped[dict[str, str] | None] = mapped_column(
        JSONB,
        nullable=True,
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
        server_default=func.now(),
    )
    expires_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
    )
    completed_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True),
        nullable=True,
    )
