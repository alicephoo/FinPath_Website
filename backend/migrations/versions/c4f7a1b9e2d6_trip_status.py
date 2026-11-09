"""trip_status

Revision ID: c4f7a1b9e2d6
Revises: b9e4d2a7c1f3
Create Date: 2026-11-07

Adds an explicit lifecycle status to trips so the application can distinguish
active allocations from completed or cancelled trips. Active trips reserve
their planned budget as an expense; completed and cancelled trips recognize
only their actual spending. Existing trips default to ``active`` and no
financial data is deleted or rewritten.

The runtime identity receives UPDATE on the new status column only.
"""

from __future__ import annotations

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

# revision identifiers, used by Alembic.
revision: str = "c4f7a1b9e2d6"
down_revision: Union[str, None] = "b9e4d2a7c1f3"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


TRIP_STATUS_PRIVILEGE_DDL = (
    "GRANT UPDATE (status) ON TABLE public.trips TO finpath_app",
)

TRIP_STATUS_REVOKE_DDL = (
    "REVOKE UPDATE (status) ON TABLE public.trips FROM finpath_app",
)


def upgrade() -> None:
    op.add_column(
        "trips",
        sa.Column(
            "status",
            sa.String(length=16),
            server_default=sa.text("'active'"),
            nullable=False,
        ),
    )
    op.create_check_constraint(
        op.f("ck_trips_status_valid"),
        "trips",
        "status IN ('active', 'completed', 'cancelled')",
    )
    for statement in TRIP_STATUS_PRIVILEGE_DDL:
        op.execute(statement)


def downgrade() -> None:
    for statement in TRIP_STATUS_REVOKE_DDL:
        op.execute(statement)
    op.drop_constraint(
        op.f("ck_trips_status_valid"),
        "trips",
        type_="check",
    )
    op.drop_column("trips", "status")
