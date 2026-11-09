"""registration_identity

Revision ID: c7a4e2f19b30
Revises: 7270e464aca9
Create Date: 2026-11-04 16:00:00.000000

Adds the dedicated registration login identity's least-privilege grants and a
non-SECURITY-DEFINER trigger that prevents that identity from assigning any
role other than the required ``user`` role (id 1).

The login role itself is provisioned once by a local PostgreSQL administrator
(see ``backend/README.md``); this migration owns the per-database grants and
trigger and must run after the role exists.
"""

from typing import Sequence, Union

from alembic import op

# revision identifiers, used by Alembic.
revision: str = "c7a4e2f19b30"
down_revision: Union[str, None] = "7270e464aca9"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None

REGISTRATION_ROLE = "finpath_registration"

REGISTRATION_ROLE_GUARD_FUNCTION = """
    CREATE FUNCTION public.enforce_registration_role_assignment()
    RETURNS trigger
    LANGUAGE plpgsql
    SET search_path = pg_catalog
    AS $function$
    BEGIN
        IF current_user = 'finpath_registration' AND NEW.role_id <> 1 THEN
            RAISE EXCEPTION USING
                ERRCODE = 'insufficient_privilege',
                MESSAGE = 'registration may only assign the user role';
        END IF;
        RETURN NEW;
    END;
    $function$
    """

REGISTRATION_ROLE_GUARD_TRIGGER = """
    CREATE TRIGGER trg_user_roles_registration_role_guard
    BEFORE INSERT OR UPDATE ON public.user_roles
    FOR EACH ROW EXECUTE FUNCTION public.enforce_registration_role_assignment()
    """

REGISTRATION_PRIVILEGE_DDL = (
    "GRANT USAGE ON SCHEMA public TO finpath_registration",
    "REVOKE ALL PRIVILEGES ON TABLE public.users FROM finpath_registration",
    "GRANT INSERT (id, email, password_hash, timezone) "
    "ON TABLE public.users TO finpath_registration",
    "REVOKE ALL PRIVILEGES ON TABLE public.user_roles FROM finpath_registration",
    "GRANT INSERT (user_id, role_id) "
    "ON TABLE public.user_roles TO finpath_registration",
    "REVOKE ALL PRIVILEGES ON TABLE public.user_financial_settings "
    "FROM finpath_registration",
    "GRANT INSERT (user_id) "
    "ON TABLE public.user_financial_settings TO finpath_registration",
    "REVOKE ALL PRIVILEGES ON TABLE public.auth_action_tokens "
    "FROM finpath_registration",
    "GRANT INSERT (id, user_id, token_type, token_hash, expires_at) "
    "ON TABLE public.auth_action_tokens TO finpath_registration",
    "REVOKE ALL PRIVILEGES ON TABLE public.idempotency_records "
    "FROM finpath_registration",
    "GRANT SELECT (principal_scope_hash, http_method, route_key, key_hash, "
    "request_hash, state, response_status, response_body, response_headers, "
    "expires_at, completed_at) "
    "ON TABLE public.idempotency_records TO finpath_registration",
    "GRANT INSERT (id, owner_user_id, principal_scope_hash, http_method, "
    "route_key, key_hash, request_hash, expires_at) "
    "ON TABLE public.idempotency_records TO finpath_registration",
    "GRANT UPDATE (state, response_status, response_body, response_headers, "
    "completed_at) "
    "ON TABLE public.idempotency_records TO finpath_registration",
)


def upgrade() -> None:
    op.execute(REGISTRATION_ROLE_GUARD_FUNCTION)
    op.execute(REGISTRATION_ROLE_GUARD_TRIGGER)
    for statement in REGISTRATION_PRIVILEGE_DDL:
        op.execute(statement)


def downgrade() -> None:
    op.execute(
        "DROP TRIGGER IF EXISTS trg_user_roles_registration_role_guard "
        "ON public.user_roles"
    )
    op.execute(
        "DROP FUNCTION IF EXISTS "
        "public.enforce_registration_role_assignment()"
    )
    op.execute(
        "REVOKE ALL PRIVILEGES ON TABLE public.users "
        "FROM finpath_registration"
    )
    op.execute(
        "REVOKE ALL PRIVILEGES ON TABLE public.user_roles "
        "FROM finpath_registration"
    )
    op.execute(
        "REVOKE ALL PRIVILEGES ON TABLE public.user_financial_settings "
        "FROM finpath_registration"
    )
    op.execute(
        "REVOKE ALL PRIVILEGES ON TABLE public.auth_action_tokens "
        "FROM finpath_registration"
    )
    op.execute(
        "REVOKE ALL PRIVILEGES ON TABLE public.idempotency_records "
        "FROM finpath_registration"
    )
