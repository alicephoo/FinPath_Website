"""registration_verification_grant

Revision ID: b9e4d2a7c1f3
Revises: f4a1c2d3e5b6
Create Date: 2026-11-07

Allows the least-privilege registration identity to set ``email_verified_at``
when email verification is disabled for local development. The application
still controls this behavior through ``FINPATH_REQUIRE_EMAIL_VERIFICATION``;
the grant is limited to that one column and does not give the registration
identity general UPDATE access to users.
"""

from __future__ import annotations

from typing import Sequence, Union

from alembic import op

# revision identifiers, used by Alembic.
revision: str = "b9e4d2a7c1f3"
down_revision: Union[str, None] = "f4a1c2d3e5b6"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


REGISTRATION_VERIFICATION_PRIVILEGE_DDL = (
    "GRANT INSERT (email_verified_at) "
    "ON TABLE public.users TO finpath_registration",
)

REGISTRATION_VERIFICATION_REVOKE_DDL = (
    "REVOKE INSERT (email_verified_at) "
    "ON TABLE public.users FROM finpath_registration",
)


def upgrade() -> None:
    for statement in REGISTRATION_VERIFICATION_PRIVILEGE_DDL:
        op.execute(statement)


def downgrade() -> None:
    op.execute(REGISTRATION_VERIFICATION_REVOKE_DDL)
