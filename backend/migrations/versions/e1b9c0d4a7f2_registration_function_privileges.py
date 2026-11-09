"""registration_function_privileges

Revision ID: e1b9c0d4a7f2
Revises: c7a4e2f19b30
Create Date: 2026-11-04 16:20:00.000000

Restrict execute on the registration role-assignment guard function. Trigger
functions are invoked by the trigger machinery rather than by callers, so no
application identity needs direct EXECUTE on it.
"""

from typing import Sequence, Union

from alembic import op

# revision identifiers, used by Alembic.
revision: str = "e1b9c0d4a7f2"
down_revision: Union[str, None] = "c7a4e2f19b30"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.execute(
        "REVOKE ALL PRIVILEGES ON FUNCTION "
        "public.enforce_registration_role_assignment() "
        "FROM PUBLIC, finpath_app, finpath_registration"
    )


def downgrade() -> None:
    op.execute(
        "GRANT EXECUTE ON FUNCTION "
        "public.enforce_registration_role_assignment() TO PUBLIC"
    )
