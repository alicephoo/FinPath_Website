"""registration_token_guard

Revision ID: f4a1c2d3e5b6
Revises: e1b9c0d4a7f2
Create Date: 2026-11-04 16:35:00.000000

The registration identity needs to create the initial email-verification
token. It must not be able to mint password-reset or email-change tokens for
existing accounts, which would be a privilege-escalation path. A non-SECURITY
DEFINER trigger restricts its token writes to the email-verification purpose.
"""

from typing import Sequence, Union

from alembic import op

# revision identifiers, used by Alembic.
revision: str = "f4a1c2d3e5b6"
down_revision: Union[str, None] = "e1b9c0d4a7f2"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None

REGISTRATION_TOKEN_GUARD_FUNCTION = """
    CREATE FUNCTION public.enforce_registration_action_token()
    RETURNS trigger
    LANGUAGE plpgsql
    SET search_path = pg_catalog
    AS $function$
    BEGIN
        IF current_user = 'finpath_registration'
            AND (
                NEW.token_type <> 'email_verification'
                OR NEW.target_email IS NOT NULL
            ) THEN
            RAISE EXCEPTION USING
                ERRCODE = 'insufficient_privilege',
                MESSAGE = 'registration may only create email verification tokens';
        END IF;
        RETURN NEW;
    END;
    $function$
    """

REGISTRATION_TOKEN_GUARD_TRIGGER = """
    CREATE TRIGGER trg_auth_action_tokens_registration_guard
    BEFORE INSERT OR UPDATE ON public.auth_action_tokens
    FOR EACH ROW EXECUTE FUNCTION public.enforce_registration_action_token()
    """


def upgrade() -> None:
    op.execute(REGISTRATION_TOKEN_GUARD_FUNCTION)
    op.execute(REGISTRATION_TOKEN_GUARD_TRIGGER)
    op.execute(
        "REVOKE ALL PRIVILEGES ON FUNCTION "
        "public.enforce_registration_action_token() "
        "FROM PUBLIC, finpath_app, finpath_registration"
    )


def downgrade() -> None:
    op.execute(
        "DROP TRIGGER IF EXISTS trg_auth_action_tokens_registration_guard "
        "ON public.auth_action_tokens"
    )
    op.execute(
        "DROP FUNCTION IF EXISTS "
        "public.enforce_registration_action_token()"
    )
