"""initial_schema

Revision ID: 7270e464aca9
Revises: 
Create Date: 2026-11-04 07:17:39.109801
"""

from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

# revision identifiers, used by Alembic.
revision: str = '7270e464aca9'
down_revision: Union[str, None] = None
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


PROTECTION_FUNCTION_DDL = (
    """
    CREATE FUNCTION public.prevent_admin_audit_log_mutation()
    RETURNS trigger
    LANGUAGE plpgsql
    SET search_path = pg_catalog
    AS $function$
    BEGIN
        IF TG_OP = 'UPDATE'
            AND pg_catalog.pg_trigger_depth() > 1
            AND OLD.target_user_id IS NOT NULL
            AND NEW.target_user_id IS NULL
            AND (pg_catalog.to_jsonb(NEW) - 'target_user_id')
                IS NOT DISTINCT FROM
                (pg_catalog.to_jsonb(OLD) - 'target_user_id') THEN
            RETURN NEW;
        END IF;

        RAISE EXCEPTION USING
            ERRCODE = 'check_violation',
            MESSAGE = 'admin_audit_logs records are append-only';
    END;
    $function$
    """,
    """
    CREATE FUNCTION public.protect_loan_repayment()
    RETURNS trigger
    LANGUAGE plpgsql
    SET search_path = pg_catalog
    AS $function$
    BEGIN
        IF TG_OP = 'DELETE' THEN
            RAISE EXCEPTION USING
                ERRCODE = 'check_violation',
                MESSAGE = 'loan_repayments cannot be deleted';
        END IF;

        IF (pg_catalog.to_jsonb(NEW) - 'revision')
            IS DISTINCT FROM (pg_catalog.to_jsonb(OLD) - 'revision') THEN
            RAISE EXCEPTION USING
                ERRCODE = 'check_violation',
                MESSAGE = 'loan repayment facts are immutable';
        END IF;

        IF NEW.revision IS DISTINCT FROM OLD.revision + 1 THEN
            RAISE EXCEPTION USING
                ERRCODE = 'check_violation',
                MESSAGE = 'loan repayment revision must advance by exactly one';
        END IF;

        RETURN NEW;
    END;
    $function$
    """,
    """
    CREATE FUNCTION public.validate_loan_repayment_correction()
    RETURNS trigger
    LANGUAGE plpgsql
    SET search_path = pg_catalog
    AS $function$
    DECLARE
        original public.loan_repayments%ROWTYPE;
    BEGIN
        IF TG_OP <> 'INSERT' THEN
            RAISE EXCEPTION USING
                ERRCODE = 'check_violation',
                MESSAGE = 'loan_repayment_corrections are append-only';
        END IF;

        SELECT repayment.*
        INTO original
        FROM public.loan_repayments AS repayment
        WHERE repayment.id = NEW.repayment_id;

        IF NOT FOUND THEN
            RAISE EXCEPTION USING
                ERRCODE = 'foreign_key_violation',
                MESSAGE = 'loan repayment correction references no repayment';
        END IF;

        IF original.loan_id IS DISTINCT FROM NEW.loan_id
            OR original.user_id IS DISTINCT FROM NEW.user_id
            OR original.amount_kyat IS DISTINCT FROM NEW.amount_kyat
            OR NEW.created_by_user_id IS DISTINCT FROM NEW.user_id THEN
            RAISE EXCEPTION USING
                ERRCODE = 'check_violation',
                MESSAGE = 'loan repayment correction must exactly reverse its owner repayment';
        END IF;

        IF pg_catalog.length(pg_catalog.btrim(NEW.reason)) = 0 THEN
            RAISE EXCEPTION USING
                ERRCODE = 'check_violation',
                MESSAGE = 'loan repayment correction reason must not be blank';
        END IF;

        RETURN NEW;
    END;
    $function$
    """,
    """
    CREATE FUNCTION public.protect_savings_entry()
    RETURNS trigger
    LANGUAGE plpgsql
    SET search_path = pg_catalog
    AS $function$
    DECLARE
        original public.savings_entries%ROWTYPE;
        day_plan_id uuid;
        day_status text;
    BEGIN
        IF TG_OP = 'DELETE' THEN
            RAISE EXCEPTION USING
                ERRCODE = 'check_violation',
                MESSAGE = 'savings_entries cannot be deleted';
        END IF;

        IF TG_OP = 'UPDATE' THEN
            IF OLD.plan_day_id IS NULL OR NEW.plan_day_id IS NOT NULL
                OR (pg_catalog.to_jsonb(NEW) - 'plan_day_id')
                    IS DISTINCT FROM (pg_catalog.to_jsonb(OLD) - 'plan_day_id') THEN
                RAISE EXCEPTION USING
                    ERRCODE = 'check_violation',
                    MESSAGE = 'savings entry facts are immutable';
            END IF;

            SELECT day.plan_id
            INTO day_plan_id
            FROM public.savings_plan_days AS day
            WHERE day.id = OLD.plan_day_id
                AND day.user_id = OLD.user_id
                AND day.goal_id = OLD.goal_id
                AND day.status = 'pending'
            FOR UPDATE;

            IF NOT FOUND OR EXISTS (
                SELECT 1
                FROM public.savings_plan_days AS terminal_day
                WHERE terminal_day.plan_id = day_plan_id
                    AND terminal_day.status IN ('completed', 'missed')
            ) THEN
                RAISE EXCEPTION USING
                    ERRCODE = 'check_violation',
                    MESSAGE = 'savings entry may be detached only from an editable pending plan';
            END IF;

            RETURN NEW;
        END IF;

        IF NEW.correction_of_entry_id IS NOT NULL THEN
            SELECT entry.*
            INTO original
            FROM public.savings_entries AS entry
            WHERE entry.id = NEW.correction_of_entry_id
                AND entry.user_id = NEW.user_id
                AND entry.goal_id = NEW.goal_id;

            IF NOT FOUND
                OR original.amount_kyat IS DISTINCT FROM NEW.amount_kyat
                OR original.entry_type = NEW.entry_type
                OR original.entry_date IS DISTINCT FROM NEW.entry_date
                OR NEW.plan_day_id IS NOT NULL
                OR pg_catalog.length(pg_catalog.btrim(NEW.correction_reason)) = 0 THEN
                RAISE EXCEPTION USING
                    ERRCODE = 'check_violation',
                    MESSAGE = 'savings correction must match amount and date, reverse entry type, and include a reason';
            END IF;
        END IF;

        IF NEW.plan_day_id IS NOT NULL THEN
            SELECT day.status
            INTO day_status
            FROM public.savings_plan_days AS day
            WHERE day.id = NEW.plan_day_id
                AND day.user_id = NEW.user_id
                AND day.goal_id = NEW.goal_id
            FOR SHARE;

            IF NOT FOUND OR day_status <> 'pending' THEN
                RAISE EXCEPTION USING
                    ERRCODE = 'check_violation',
                    MESSAGE = 'savings contributions may be linked only to pending plan days';
            END IF;
        END IF;

        RETURN NEW;
    END;
    $function$
    """,
    """
    CREATE FUNCTION public.assert_savings_entry_detached_during_rebuild()
    RETURNS trigger
    LANGUAGE plpgsql
    SET search_path = pg_catalog
    AS $function$
    BEGIN
        IF OLD.plan_day_id IS NOT NULL
            AND NEW.plan_day_id IS NULL
            AND EXISTS (
                SELECT 1
                FROM public.savings_plan_days AS day
                WHERE day.id = OLD.plan_day_id
            ) THEN
            RAISE EXCEPTION USING
                ERRCODE = 'check_violation',
                MESSAGE = 'savings entry detachment must accompany pending schedule rebuild';
        END IF;

        RETURN NULL;
    END;
    $function$
    """,
    """
    CREATE FUNCTION public.protect_savings_plan_day()
    RETURNS trigger
    LANGUAGE plpgsql
    SET search_path = pg_catalog
    AS $function$
    DECLARE
        plan_start_date date;
        plan_duration smallint;
    BEGIN
        IF TG_OP = 'INSERT' THEN
            IF NEW.status <> 'pending' THEN
                RAISE EXCEPTION USING
                    ERRCODE = 'check_violation',
                    MESSAGE = 'savings plan days must be inserted as pending';
            END IF;
        ELSIF TG_OP = 'DELETE' THEN
            IF OLD.status <> 'pending' THEN
                RAISE EXCEPTION USING
                    ERRCODE = 'check_violation',
                    MESSAGE = 'terminal savings plan days cannot be deleted';
            END IF;
            RETURN OLD;
        ELSE
            IF OLD.status <> 'pending' THEN
                RAISE EXCEPTION USING
                    ERRCODE = 'check_violation',
                    MESSAGE = 'terminal savings plan days cannot be updated';
            END IF;

            IF NEW.id IS DISTINCT FROM OLD.id
                OR NEW.user_id IS DISTINCT FROM OLD.user_id
                OR NEW.goal_id IS DISTINCT FROM OLD.goal_id
                OR NEW.plan_id IS DISTINCT FROM OLD.plan_id
                OR NEW.day_number IS DISTINCT FROM OLD.day_number THEN
                RAISE EXCEPTION USING
                    ERRCODE = 'check_violation',
                    MESSAGE = 'pending savings plan day identity is immutable';
            END IF;
        END IF;

        SELECT plan.start_date, plan.duration_days
        INTO plan_start_date, plan_duration
        FROM public.savings_plans AS plan
        WHERE plan.id = NEW.plan_id
            AND plan.goal_id = NEW.goal_id
            AND plan.user_id = NEW.user_id;

        IF NOT FOUND
            OR NEW.day_number > plan_duration
            OR NEW.planned_date <> plan_start_date + (NEW.day_number::integer - 1) THEN
            RAISE EXCEPTION USING
                ERRCODE = 'check_violation',
                MESSAGE = 'savings plan day must match its plan schedule';
        END IF;

        RETURN NEW;
    END;
    $function$
    """,
    """
    CREATE FUNCTION public.assert_pending_savings_schedule_rebuilt()
    RETURNS trigger
    LANGUAGE plpgsql
    SET search_path = pg_catalog
    AS $function$
    DECLARE
        expected_days smallint;
        actual_days bigint;
        first_day smallint;
        last_day smallint;
        non_pending_days bigint;
    BEGIN
        SELECT plan.duration_days
        INTO expected_days
        FROM public.savings_plans AS plan
        WHERE plan.id = OLD.plan_id
            AND plan.goal_id = OLD.goal_id
            AND plan.user_id = OLD.user_id;

        IF NOT FOUND THEN
            RAISE EXCEPTION USING
                ERRCODE = 'check_violation',
                MESSAGE = 'pending schedule rebuild requires its savings plan';
        END IF;

        SELECT
            pg_catalog.count(*),
            pg_catalog.min(day.day_number),
            pg_catalog.max(day.day_number),
            pg_catalog.count(*) FILTER (WHERE day.status <> 'pending')
        INTO actual_days, first_day, last_day, non_pending_days
        FROM public.savings_plan_days AS day
        WHERE day.plan_id = OLD.plan_id;

        IF actual_days <> expected_days
            OR first_day <> 1
            OR last_day <> expected_days
            OR non_pending_days <> 0 THEN
            RAISE EXCEPTION USING
                ERRCODE = 'check_violation',
                MESSAGE = 'pending schedule rebuild must replace the complete plan schedule';
        END IF;

        RETURN NULL;
    END;
    $function$
    """,
    """
    CREATE FUNCTION public.protect_savings_plan_schedule()
    RETURNS trigger
    LANGUAGE plpgsql
    SET search_path = pg_catalog
    AS $function$
    BEGIN
        IF NEW.id IS DISTINCT FROM OLD.id
            OR NEW.user_id IS DISTINCT FROM OLD.user_id
            OR NEW.goal_id IS DISTINCT FROM OLD.goal_id THEN
            RAISE EXCEPTION USING
                ERRCODE = 'check_violation',
                MESSAGE = 'savings plan identity is immutable';
        END IF;

        IF (NEW.start_date IS DISTINCT FROM OLD.start_date
            OR NEW.duration_days IS DISTINCT FROM OLD.duration_days
            OR NEW.daily_amount_kyat IS DISTINCT FROM OLD.daily_amount_kyat)
            THEN
            PERFORM 1
            FROM public.savings_plan_days AS day
            WHERE day.plan_id = OLD.id
            FOR SHARE;

            IF EXISTS (
                SELECT 1
                FROM public.savings_plan_days AS day
                WHERE day.plan_id = OLD.id
                    AND day.status IN ('completed', 'missed')
            ) THEN
                RAISE EXCEPTION USING
                    ERRCODE = 'check_violation',
                    MESSAGE = 'savings plan schedule cannot change after terminal day history';
            END IF;
        END IF;

        RETURN NEW;
    END;
    $function$
    """,
    """
    CREATE FUNCTION public.assert_savings_plan_schedule_consistent()
    RETURNS trigger
    LANGUAGE plpgsql
    SET search_path = pg_catalog
    AS $function$
    DECLARE
        expected_days smallint;
        plan_start date;
        actual_days bigint;
        first_day smallint;
        last_day smallint;
        invalid_days bigint;
    BEGIN
        IF NEW.start_date IS NOT DISTINCT FROM OLD.start_date
            AND NEW.duration_days IS NOT DISTINCT FROM OLD.duration_days
            AND NEW.daily_amount_kyat IS NOT DISTINCT FROM OLD.daily_amount_kyat THEN
            RETURN NULL;
        END IF;

        SELECT plan.duration_days, plan.start_date
        INTO expected_days, plan_start
        FROM public.savings_plans AS plan
        WHERE plan.id = NEW.id;

        SELECT
            pg_catalog.count(*),
            pg_catalog.min(day.day_number),
            pg_catalog.max(day.day_number),
            pg_catalog.count(*) FILTER (
                WHERE day.status <> 'pending'
                    OR day.planned_date <>
                        plan_start + (day.day_number::integer - 1)
                    OR day.day_number > expected_days
            )
        INTO actual_days, first_day, last_day, invalid_days
        FROM public.savings_plan_days AS day
        WHERE day.plan_id = NEW.id;

        IF actual_days <> expected_days
            OR first_day <> 1
            OR last_day <> expected_days
            OR invalid_days <> 0 THEN
            RAISE EXCEPTION USING
                ERRCODE = 'check_violation',
                MESSAGE = 'savings plan schedule update requires a complete pending schedule rebuild';
        END IF;

        RETURN NULL;
    END;
    $function$
    """,
    """
    CREATE FUNCTION public.protect_user_lifecycle()
    RETURNS trigger
    LANGUAGE plpgsql
    SET search_path = pg_catalog
    AS $function$
    BEGIN
        IF OLD.status = 'closed' AND NEW.status IS DISTINCT FROM 'closed' THEN
            RAISE EXCEPTION USING
                ERRCODE = 'check_violation',
                MESSAGE = 'closed accounts cannot be reactivated';
        END IF;

        RETURN NEW;
    END;
    $function$
    """,
)

PROTECTION_TRIGGER_DDL = (
    """
    CREATE TRIGGER trg_admin_audit_logs_append_only
    BEFORE UPDATE OR DELETE ON public.admin_audit_logs
    FOR EACH ROW EXECUTE FUNCTION public.prevent_admin_audit_log_mutation()
    """,
    """
    CREATE TRIGGER trg_loan_repayments_immutable_facts
    BEFORE UPDATE OR DELETE ON public.loan_repayments
    FOR EACH ROW EXECUTE FUNCTION public.protect_loan_repayment()
    """,
    """
    CREATE TRIGGER trg_loan_repayment_corrections_append_only
    BEFORE INSERT OR UPDATE OR DELETE ON public.loan_repayment_corrections
    FOR EACH ROW EXECUTE FUNCTION public.validate_loan_repayment_correction()
    """,
    """
    CREATE TRIGGER trg_savings_entries_immutable_facts
    BEFORE INSERT OR UPDATE OR DELETE ON public.savings_entries
    FOR EACH ROW EXECUTE FUNCTION public.protect_savings_entry()
    """,
    """
    CREATE CONSTRAINT TRIGGER trg_savings_entries_rebuild_detachment
    AFTER UPDATE ON public.savings_entries
    DEFERRABLE INITIALLY DEFERRED
    FOR EACH ROW EXECUTE FUNCTION public.assert_savings_entry_detached_during_rebuild()
    """,
    """
    CREATE TRIGGER trg_savings_plan_days_terminal_immutable
    BEFORE INSERT OR UPDATE OR DELETE ON public.savings_plan_days
    FOR EACH ROW EXECUTE FUNCTION public.protect_savings_plan_day()
    """,
    """
    CREATE CONSTRAINT TRIGGER trg_savings_plan_days_rebuild_complete
    AFTER DELETE ON public.savings_plan_days
    DEFERRABLE INITIALLY DEFERRED
    FOR EACH ROW EXECUTE FUNCTION public.assert_pending_savings_schedule_rebuilt()
    """,
    """
    CREATE TRIGGER trg_savings_plans_schedule_immutable_after_terminal
    BEFORE UPDATE ON public.savings_plans
    FOR EACH ROW EXECUTE FUNCTION public.protect_savings_plan_schedule()
    """,
    """
    CREATE CONSTRAINT TRIGGER trg_savings_plans_rebuild_complete
    AFTER UPDATE ON public.savings_plans
    DEFERRABLE INITIALLY DEFERRED
    FOR EACH ROW EXECUTE FUNCTION public.assert_savings_plan_schedule_consistent()
    """,
    """
    CREATE TRIGGER trg_users_terminal_closed
    BEFORE UPDATE OF status ON public.users
    FOR EACH ROW EXECUTE FUNCTION public.protect_user_lifecycle()
    """,
)

PROTECTION_TRIGGER_DROP_DDL = (
    ("trg_users_terminal_closed", "users"),
    ("trg_savings_plans_rebuild_complete", "savings_plans"),
    ("trg_savings_plans_schedule_immutable_after_terminal", "savings_plans"),
    ("trg_savings_plan_days_rebuild_complete", "savings_plan_days"),
    ("trg_savings_plan_days_terminal_immutable", "savings_plan_days"),
    ("trg_savings_entries_rebuild_detachment", "savings_entries"),
    ("trg_savings_entries_immutable_facts", "savings_entries"),
    ("trg_loan_repayment_corrections_append_only", "loan_repayment_corrections"),
    ("trg_loan_repayments_immutable_facts", "loan_repayments"),
    ("trg_admin_audit_logs_append_only", "admin_audit_logs"),
)

PROTECTION_FUNCTION_DROP_DDL = (
    "protect_user_lifecycle",
    "assert_savings_plan_schedule_consistent",
    "protect_savings_plan_schedule",
    "assert_pending_savings_schedule_rebuilt",
    "protect_savings_plan_day",
    "assert_savings_entry_detached_during_rebuild",
    "protect_savings_entry",
    "validate_loan_repayment_correction",
    "protect_loan_repayment",
    "prevent_admin_audit_log_mutation",
)

RUNTIME_PRIVILEGE_DDL = (
    "REVOKE ALL PRIVILEGES ON TABLE public.roles FROM PUBLIC, finpath_app",
    "GRANT SELECT ON TABLE public.roles TO finpath_app",
    "REVOKE ALL PRIVILEGES ON TABLE public.transaction_categories FROM PUBLIC, finpath_app",
    "GRANT SELECT ON TABLE public.transaction_categories TO finpath_app",
    "REVOKE ALL PRIVILEGES ON TABLE public.users FROM PUBLIC, finpath_app",
    "GRANT SELECT ON TABLE public.users TO finpath_app",
    "GRANT INSERT (id, email, password_hash, timezone) ON TABLE public.users TO finpath_app",
    "GRANT UPDATE (email, password_hash, email_verified_at, pending_email, status, timezone, revision, updated_at) ON TABLE public.users TO finpath_app",
    "REVOKE ALL PRIVILEGES ON TABLE public.user_roles FROM PUBLIC, finpath_app",
    "GRANT SELECT ON TABLE public.user_roles TO finpath_app",
    "REVOKE ALL PRIVILEGES ON TABLE public.admin_audit_logs FROM PUBLIC, finpath_app",
    "GRANT SELECT ON TABLE public.admin_audit_logs TO finpath_app",
    "GRANT INSERT (id, actor_type, actor_user_id, action, target_user_id, target_type, target_id, details) ON TABLE public.admin_audit_logs TO finpath_app",
    "REVOKE ALL PRIVILEGES ON TABLE public.auth_sessions FROM PUBLIC, finpath_app",
    "GRANT SELECT ON TABLE public.auth_sessions TO finpath_app",
    "GRANT INSERT (id, user_id, session_family_id, refresh_token_hash, refresh_expires_at) ON TABLE public.auth_sessions TO finpath_app",
    "GRANT UPDATE (last_refreshed_at, revoked_at, replaced_by_session_id) ON TABLE public.auth_sessions TO finpath_app",
    "REVOKE ALL PRIVILEGES ON TABLE public.auth_action_tokens FROM PUBLIC, finpath_app",
    "GRANT SELECT ON TABLE public.auth_action_tokens TO finpath_app",
    "GRANT INSERT (id, user_id, token_type, token_hash, target_email, expires_at) ON TABLE public.auth_action_tokens TO finpath_app",
    "GRANT UPDATE (consumed_at) ON TABLE public.auth_action_tokens TO finpath_app",
    "REVOKE ALL PRIVILEGES ON TABLE public.idempotency_records FROM PUBLIC, finpath_app",
    "GRANT SELECT ON TABLE public.idempotency_records TO finpath_app",
    "GRANT INSERT (id, owner_user_id, principal_scope_hash, http_method, route_key, key_hash, request_hash, expires_at) ON TABLE public.idempotency_records TO finpath_app",
    "GRANT UPDATE (state, response_status, response_body, response_headers, completed_at) ON TABLE public.idempotency_records TO finpath_app",
    "REVOKE ALL PRIVILEGES ON TABLE public.user_financial_settings FROM PUBLIC, finpath_app",
    "GRANT SELECT ON TABLE public.user_financial_settings TO finpath_app",
    "GRANT INSERT (user_id, starting_balance_kyat, effective_date) ON TABLE public.user_financial_settings TO finpath_app",
    "GRANT UPDATE (starting_balance_kyat, effective_date, revision, updated_at) ON TABLE public.user_financial_settings TO finpath_app",
    "REVOKE ALL PRIVILEGES ON TABLE public.transactions FROM PUBLIC, finpath_app",
    "GRANT SELECT ON TABLE public.transactions TO finpath_app",
    "GRANT INSERT (id, user_id, category_id, type, description, amount_kyat, transaction_date) ON TABLE public.transactions TO finpath_app",
    "GRANT UPDATE (category_id, type, description, amount_kyat, transaction_date, updated_at, revision) ON TABLE public.transactions TO finpath_app",
    "GRANT DELETE ON TABLE public.transactions TO finpath_app",
    "REVOKE ALL PRIVILEGES ON TABLE public.loans FROM PUBLIC, finpath_app",
    "GRANT SELECT ON TABLE public.loans TO finpath_app",
    "GRANT INSERT (id, user_id, name, principal_kyat, start_date) ON TABLE public.loans TO finpath_app",
    "GRANT UPDATE (name, principal_kyat, start_date, updated_at, revision) ON TABLE public.loans TO finpath_app",
    "REVOKE ALL PRIVILEGES ON TABLE public.loan_repayments FROM PUBLIC, finpath_app",
    "GRANT SELECT ON TABLE public.loan_repayments TO finpath_app",
    "GRANT INSERT (id, user_id, loan_id, amount_kyat, repayment_date, note) ON TABLE public.loan_repayments TO finpath_app",
    "GRANT UPDATE (revision) ON TABLE public.loan_repayments TO finpath_app",
    "REVOKE ALL PRIVILEGES ON TABLE public.loan_repayment_corrections FROM PUBLIC, finpath_app",
    "GRANT SELECT ON TABLE public.loan_repayment_corrections TO finpath_app",
    "GRANT INSERT (id, user_id, loan_id, repayment_id, type, amount_kyat, reason, created_by_user_id) ON TABLE public.loan_repayment_corrections TO finpath_app",
    "REVOKE ALL PRIVILEGES ON TABLE public.savings_goals FROM PUBLIC, finpath_app",
    "GRANT SELECT ON TABLE public.savings_goals TO finpath_app",
    "GRANT INSERT (id, user_id, name, target_kyat, tone) ON TABLE public.savings_goals TO finpath_app",
    "GRANT UPDATE (name, target_kyat, tone, status, archived_at, revision, updated_at) ON TABLE public.savings_goals TO finpath_app",
    "REVOKE ALL PRIVILEGES ON TABLE public.savings_plans FROM PUBLIC, finpath_app",
    "GRANT SELECT ON TABLE public.savings_plans TO finpath_app",
    "GRANT INSERT (id, user_id, goal_id, start_date, duration_days, daily_amount_kyat) ON TABLE public.savings_plans TO finpath_app",
    "GRANT UPDATE (start_date, duration_days, daily_amount_kyat, updated_at, revision) ON TABLE public.savings_plans TO finpath_app",
    "REVOKE ALL PRIVILEGES ON TABLE public.savings_plan_days FROM PUBLIC, finpath_app",
    "GRANT SELECT ON TABLE public.savings_plan_days TO finpath_app",
    "GRANT INSERT (id, user_id, goal_id, plan_id, day_number, planned_date, base_amount_kyat, carried_forward_kyat, required_amount_kyat) ON TABLE public.savings_plan_days TO finpath_app",
    "GRANT UPDATE (day_number, planned_date, base_amount_kyat, carried_forward_kyat, required_amount_kyat, status, completion_date, actual_amount_kyat, reconciled_contributions_kyat, unpaid_carry_forward_kyat) ON TABLE public.savings_plan_days TO finpath_app",
    "GRANT DELETE ON TABLE public.savings_plan_days TO finpath_app",
    "REVOKE ALL PRIVILEGES ON TABLE public.savings_entries FROM PUBLIC, finpath_app",
    "GRANT SELECT ON TABLE public.savings_entries TO finpath_app",
    "GRANT INSERT (id, user_id, goal_id, plan_day_id, entry_type, amount_kyat, entry_date, note, correction_of_entry_id, correction_reason) ON TABLE public.savings_entries TO finpath_app",
    "GRANT UPDATE (plan_day_id) ON TABLE public.savings_entries TO finpath_app",
    "REVOKE ALL PRIVILEGES ON TABLE public.trips FROM PUBLIC, finpath_app",
    "GRANT SELECT ON TABLE public.trips TO finpath_app",
    "GRANT INSERT (id, user_id, name, destination, start_date, end_date) ON TABLE public.trips TO finpath_app",
    "GRANT UPDATE (name, destination, start_date, end_date, updated_at, revision) ON TABLE public.trips TO finpath_app",
    "GRANT DELETE ON TABLE public.trips TO finpath_app",
    "REVOKE ALL PRIVILEGES ON TABLE public.trip_category_budgets FROM PUBLIC, finpath_app",
    "GRANT SELECT ON TABLE public.trip_category_budgets TO finpath_app",
    "GRANT INSERT (id, user_id, trip_id, category, budget_kyat) ON TABLE public.trip_category_budgets TO finpath_app",
    "GRANT UPDATE (category, budget_kyat, updated_at, revision) ON TABLE public.trip_category_budgets TO finpath_app",
    "GRANT DELETE ON TABLE public.trip_category_budgets TO finpath_app",
    "REVOKE ALL PRIVILEGES ON TABLE public.trip_spending_entries FROM PUBLIC, finpath_app",
    "GRANT SELECT ON TABLE public.trip_spending_entries TO finpath_app",
    "GRANT INSERT (id, user_id, trip_id, category_budget_id, category, description, amount_kyat, spending_date) ON TABLE public.trip_spending_entries TO finpath_app",
    "GRANT UPDATE (category_budget_id, category, description, amount_kyat, spending_date, updated_at, revision) ON TABLE public.trip_spending_entries TO finpath_app",
    "GRANT DELETE ON TABLE public.trip_spending_entries TO finpath_app",
)

CLEANUP_FUNCTION_DDL = """
    CREATE FUNCTION public.cleanup_expired_operational_records()
    RETURNS bigint
    LANGUAGE plpgsql
    SECURITY DEFINER
    SET search_path = pg_catalog
    AS $function$
    DECLARE
        affected_rows bigint;
        total_rows bigint := 0;
    BEGIN
        DELETE FROM public.auth_action_tokens
        WHERE expires_at <= pg_catalog.clock_timestamp();
        GET DIAGNOSTICS affected_rows = ROW_COUNT;
        total_rows := total_rows + affected_rows;

        -- Retain revoked/expired sessions through the 30-day refresh-token
        -- reuse-detection window after refresh-token expiry. Active sessions
        -- have a future refresh_expires_at and are never deleted here.
        DELETE FROM public.auth_sessions
        WHERE refresh_expires_at
            <= pg_catalog.clock_timestamp() - INTERVAL '30 days';
        GET DIAGNOSTICS affected_rows = ROW_COUNT;
        total_rows := total_rows + affected_rows;

        DELETE FROM public.idempotency_records
        WHERE state = 'completed'
          AND completed_at <= pg_catalog.clock_timestamp() - INTERVAL '24 hours'
          AND expires_at <= pg_catalog.clock_timestamp();
        GET DIAGNOSTICS affected_rows = ROW_COUNT;
        total_rows := total_rows + affected_rows;

        RETURN total_rows;
    END;
    $function$
    """

CLEANUP_FUNCTION_NAME = "cleanup_expired_operational_records"


def upgrade() -> None:
    # ### commands auto generated by Alembic - please adjust! ###
    op.create_table('roles',
    sa.Column('id', sa.SmallInteger(), nullable=False),
    sa.Column('name', sa.String(length=32), nullable=False),
    sa.Column('description', sa.String(length=200), nullable=True),
    sa.PrimaryKeyConstraint('id', name=op.f('pk_roles')),
    sa.UniqueConstraint('name', name=op.f('uq_roles_name'))
    )
    op.create_table('transaction_categories',
    sa.Column('id', sa.UUID(), nullable=False),
    sa.Column('name', sa.String(length=80), nullable=False),
    sa.Column('type', sa.String(length=8), nullable=False),
    sa.Column('is_active', sa.Boolean(), server_default=sa.text('true'), nullable=False),
    sa.CheckConstraint("type IN ('income', 'expense')", name=op.f('ck_transaction_categories_type')),
    sa.PrimaryKeyConstraint('id', name=op.f('pk_transaction_categories')),
    sa.UniqueConstraint('id', 'type', name=op.f('uq_transaction_categories_id_type'))
    )
    op.create_index('uq_transaction_categories_type_name_lower', 'transaction_categories', ['type', sa.literal_column('lower(name)')], unique=True)
    op.create_table('users',
    sa.Column('id', sa.UUID(), nullable=False),
    sa.Column('email', sa.String(length=320), nullable=False),
    sa.Column('password_hash', sa.String(length=255), nullable=False),
    sa.Column('email_verified_at', sa.DateTime(timezone=True), nullable=True),
    sa.Column('pending_email', sa.String(length=320), nullable=True),
    sa.Column('status', sa.String(length=16), server_default=sa.text("'active'"), nullable=False),
    sa.Column('timezone', sa.String(length=64), nullable=False),
    sa.Column('revision', sa.BigInteger(), server_default=sa.text('1'), nullable=False),
    sa.Column('created_at', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=False),
    sa.Column('updated_at', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=False),
    sa.CheckConstraint("status IN ('active', 'suspended', 'closed')", name=op.f('ck_users_status')),
    sa.PrimaryKeyConstraint('id', name=op.f('pk_users'))
    )
    op.create_index('ix_users_status', 'users', ['status'], unique=False)
    op.create_index('uq_users_email_lower', 'users', [sa.literal_column('lower(email)')], unique=True)
    op.create_index('uq_users_pending_email_lower', 'users', [sa.literal_column('lower(pending_email)')], unique=True, postgresql_where=sa.text('pending_email IS NOT NULL'))
    op.create_table('admin_audit_logs',
    sa.Column('id', sa.UUID(), nullable=False),
    sa.Column('actor_type', sa.String(length=16), nullable=False),
    sa.Column('actor_user_id', sa.UUID(), nullable=True),
    sa.Column('action', sa.String(length=80), nullable=False),
    sa.Column('target_user_id', sa.UUID(), nullable=True),
    sa.Column('target_type', sa.String(length=80), nullable=False),
    sa.Column('target_id', sa.UUID(), nullable=True),
    sa.Column('occurred_at', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=False),
    sa.Column('details', postgresql.JSONB(astext_type=sa.Text()), nullable=True),
    sa.CheckConstraint("(actor_type = 'user' AND actor_user_id IS NOT NULL) OR (actor_type IN ('bootstrap_cli', 'system') AND actor_user_id IS NULL)", name=op.f('ck_admin_audit_logs_actor_user_id_matches_actor_type')),
    sa.CheckConstraint("actor_type IN ('user', 'bootstrap_cli', 'system')", name=op.f('ck_admin_audit_logs_actor_type')),
    sa.ForeignKeyConstraint(['actor_user_id'], ['users.id'], name=op.f('fk_admin_audit_logs_actor_user_id_users'), ondelete='RESTRICT'),
    sa.ForeignKeyConstraint(['target_user_id'], ['users.id'], name=op.f('fk_admin_audit_logs_target_user_id_users'), ondelete='SET NULL'),
    sa.PrimaryKeyConstraint('id', name=op.f('pk_admin_audit_logs'))
    )
    op.create_index('ix_admin_audit_logs_actor_user_id_occurred_at', 'admin_audit_logs', ['actor_user_id', sa.literal_column('occurred_at DESC')], unique=False)
    op.create_index('ix_admin_audit_logs_target_user_id_occurred_at', 'admin_audit_logs', ['target_user_id', sa.literal_column('occurred_at DESC')], unique=False)
    op.create_table('auth_action_tokens',
    sa.Column('id', sa.UUID(), nullable=False),
    sa.Column('user_id', sa.UUID(), nullable=False),
    sa.Column('token_type', sa.String(length=32), nullable=False),
    sa.Column('token_hash', sa.String(length=255), nullable=False),
    sa.Column('target_email', sa.String(length=320), nullable=True),
    sa.Column('created_at', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=False),
    sa.Column('expires_at', sa.DateTime(timezone=True), nullable=False),
    sa.Column('consumed_at', sa.DateTime(timezone=True), nullable=True),
    sa.CheckConstraint("(token_type = 'email_change_verification' AND target_email IS NOT NULL) OR (token_type <> 'email_change_verification')", name=op.f('ck_auth_action_tokens_target_email_for_email_change')),
    sa.CheckConstraint("token_type IN ('email_verification', 'password_reset', 'email_change_verification')", name=op.f('ck_auth_action_tokens_token_type')),
    sa.ForeignKeyConstraint(['user_id'], ['users.id'], name=op.f('fk_auth_action_tokens_user_id_users'), ondelete='CASCADE'),
    sa.PrimaryKeyConstraint('id', name=op.f('pk_auth_action_tokens')),
    sa.UniqueConstraint('token_hash', name=op.f('uq_auth_action_tokens_token_hash'))
    )
    op.create_index('ix_auth_action_tokens_user_id_token_type_expires_at', 'auth_action_tokens', ['user_id', 'token_type', 'expires_at'], unique=False)
    op.create_table('auth_sessions',
    sa.Column('id', sa.UUID(), nullable=False),
    sa.Column('user_id', sa.UUID(), nullable=False),
    sa.Column('session_family_id', sa.UUID(), nullable=False),
    sa.Column('refresh_token_hash', sa.String(length=255), nullable=False),
    sa.Column('created_at', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=False),
    sa.Column('refresh_expires_at', sa.DateTime(timezone=True), nullable=False),
    sa.Column('last_refreshed_at', sa.DateTime(timezone=True), nullable=True),
    sa.Column('revoked_at', sa.DateTime(timezone=True), nullable=True),
    sa.Column('replaced_by_session_id', sa.UUID(), nullable=True),
    sa.ForeignKeyConstraint(['replaced_by_session_id'], ['auth_sessions.id'], name=op.f('fk_auth_sessions_replaced_by_session_id_auth_sessions'), ondelete='SET NULL'),
    sa.ForeignKeyConstraint(['user_id'], ['users.id'], name=op.f('fk_auth_sessions_user_id_users'), ondelete='CASCADE'),
    sa.PrimaryKeyConstraint('id', name=op.f('pk_auth_sessions')),
    sa.UniqueConstraint('refresh_token_hash', name=op.f('uq_auth_sessions_refresh_token_hash')),
    sa.UniqueConstraint('replaced_by_session_id', name=op.f('uq_auth_sessions_replaced_by_session_id'))
    )
    op.create_index('ix_auth_sessions_session_family_id_refresh_expires_at', 'auth_sessions', ['session_family_id', 'refresh_expires_at'], unique=False)
    op.create_index('ix_auth_sessions_user_id_revoked_at_refresh_expires_at', 'auth_sessions', ['user_id', 'revoked_at', 'refresh_expires_at'], unique=False)
    op.create_table('idempotency_records',
    sa.Column('id', sa.UUID(), nullable=False),
    sa.Column('owner_user_id', sa.UUID(), nullable=True),
    sa.Column('principal_scope_hash', sa.CHAR(length=64), nullable=False),
    sa.Column('http_method', sa.String(length=8), nullable=False),
    sa.Column('route_key', sa.String(length=200), nullable=False),
    sa.Column('key_hash', sa.CHAR(length=64), nullable=False),
    sa.Column('request_hash', sa.CHAR(length=64), nullable=False),
    sa.Column('state', sa.String(length=12), server_default=sa.text("'in_progress'"), nullable=False),
    sa.Column('response_status', sa.SmallInteger(), nullable=True),
    sa.Column('response_body', postgresql.JSONB(astext_type=sa.Text()), nullable=True),
    sa.Column('response_headers', postgresql.JSONB(astext_type=sa.Text()), nullable=True),
    sa.Column('created_at', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=False),
    sa.Column('expires_at', sa.DateTime(timezone=True), nullable=False),
    sa.Column('completed_at', sa.DateTime(timezone=True), nullable=True),
    sa.CheckConstraint("(state = 'in_progress' AND response_status IS NULL AND response_body IS NULL AND response_headers IS NULL AND completed_at IS NULL) OR (state = 'completed' AND response_status IS NOT NULL AND response_body IS NOT NULL AND response_headers IS NOT NULL AND completed_at IS NOT NULL)", name=op.f('ck_idempotency_records_response_matches_state')),
    sa.CheckConstraint("state IN ('in_progress', 'completed')", name=op.f('ck_idempotency_records_state')),
    sa.ForeignKeyConstraint(['owner_user_id'], ['users.id'], name=op.f('fk_idempotency_records_owner_user_id_users'), ondelete='SET NULL'),
    sa.PrimaryKeyConstraint('id', name=op.f('pk_idempotency_records')),
    sa.UniqueConstraint('principal_scope_hash', 'http_method', 'route_key', 'key_hash', name='uq_idempotency_records_scope_method_route_key')
    )
    op.create_index('ix_idempotency_records_expires_at', 'idempotency_records', ['expires_at'], unique=False)
    op.create_table('loans',
    sa.Column('id', sa.UUID(), nullable=False),
    sa.Column('user_id', sa.UUID(), nullable=False),
    sa.Column('name', sa.String(length=70), nullable=False),
    sa.Column('principal_kyat', sa.BigInteger(), nullable=False),
    sa.Column('start_date', sa.Date(), nullable=False),
    sa.Column('created_at', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=False),
    sa.Column('updated_at', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=False),
    sa.Column('revision', sa.BigInteger(), server_default=sa.text('1'), nullable=False),
    sa.CheckConstraint('principal_kyat > 0', name=op.f('ck_loans_principal_positive')),
    sa.ForeignKeyConstraint(['user_id'], ['users.id'], name=op.f('fk_loans_user_id_users'), ondelete='RESTRICT'),
    sa.PrimaryKeyConstraint('id', name=op.f('pk_loans')),
    sa.UniqueConstraint('id', 'user_id', name=op.f('uq_loans_id_user_id'))
    )
    op.create_index('ix_loans_user_id_start_date', 'loans', ['user_id', sa.literal_column('start_date DESC')], unique=False)
    op.create_table('savings_goals',
    sa.Column('id', sa.UUID(), nullable=False),
    sa.Column('user_id', sa.UUID(), nullable=False),
    sa.Column('name', sa.String(length=80), nullable=False),
    sa.Column('target_kyat', sa.BigInteger(), nullable=False),
    sa.Column('tone', sa.String(length=16), nullable=True),
    sa.Column('status', sa.String(length=12), server_default=sa.text("'active'"), nullable=False),
    sa.Column('archived_at', sa.DateTime(timezone=True), nullable=True),
    sa.Column('revision', sa.BigInteger(), server_default=sa.text('1'), nullable=False),
    sa.Column('created_at', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=False),
    sa.Column('updated_at', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=False),
    sa.CheckConstraint("(status = 'archived' AND archived_at IS NOT NULL) OR (status = 'active' AND archived_at IS NULL)", name=op.f('ck_savings_goals_archive_timestamp_matches_status')),
    sa.CheckConstraint("status IN ('active', 'archived')", name=op.f('ck_savings_goals_status')),
    sa.CheckConstraint('target_kyat > 0', name=op.f('ck_savings_goals_target_positive')),
    sa.ForeignKeyConstraint(['user_id'], ['users.id'], name=op.f('fk_savings_goals_user_id_users'), ondelete='RESTRICT'),
    sa.PrimaryKeyConstraint('id', name=op.f('pk_savings_goals')),
    sa.UniqueConstraint('id', 'user_id', name=op.f('uq_savings_goals_id_user_id'))
    )
    op.create_index('ix_savings_goals_user_id_status_created_at', 'savings_goals', ['user_id', 'status', sa.literal_column('created_at DESC')], unique=False)
    op.create_table('transactions',
    sa.Column('id', sa.UUID(), nullable=False),
    sa.Column('user_id', sa.UUID(), nullable=False),
    sa.Column('category_id', sa.UUID(), nullable=False),
    sa.Column('type', sa.String(length=8), nullable=False),
    sa.Column('description', sa.String(length=80), nullable=False),
    sa.Column('amount_kyat', sa.BigInteger(), nullable=False),
    sa.Column('transaction_date', sa.Date(), nullable=False),
    sa.Column('created_at', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=False),
    sa.Column('updated_at', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=False),
    sa.Column('revision', sa.BigInteger(), server_default=sa.text('1'), nullable=False),
    sa.CheckConstraint("type IN ('income', 'expense')", name=op.f('ck_transactions_type')),
    sa.CheckConstraint('amount_kyat > 0', name=op.f('ck_transactions_amount_positive')),
    sa.ForeignKeyConstraint(['category_id', 'type'], ['transaction_categories.id', 'transaction_categories.type'], name=op.f('fk_transactions_category_id_type_transaction_categories'), ondelete='RESTRICT'),
    sa.ForeignKeyConstraint(['user_id'], ['users.id'], name=op.f('fk_transactions_user_id_users'), ondelete='RESTRICT'),
    sa.PrimaryKeyConstraint('id', name=op.f('pk_transactions')),
    sa.UniqueConstraint('id', 'user_id', name=op.f('uq_transactions_id_user_id'))
    )
    op.create_index('ix_transactions_user_id_category_id_transaction_date', 'transactions', ['user_id', 'category_id', sa.literal_column('transaction_date DESC')], unique=False)
    op.create_index('ix_transactions_user_id_transaction_date_id', 'transactions', ['user_id', sa.literal_column('transaction_date DESC'), 'id'], unique=False)
    op.create_table('trips',
    sa.Column('id', sa.UUID(), nullable=False),
    sa.Column('user_id', sa.UUID(), nullable=False),
    sa.Column('name', sa.String(length=80), nullable=False),
    sa.Column('destination', sa.String(length=100), nullable=False),
    sa.Column('start_date', sa.Date(), nullable=False),
    sa.Column('end_date', sa.Date(), nullable=False),
    sa.Column('created_at', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=False),
    sa.Column('updated_at', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=False),
    sa.Column('revision', sa.BigInteger(), server_default=sa.text('1'), nullable=False),
    sa.CheckConstraint('end_date >= start_date', name=op.f('ck_trips_date_order')),
    sa.ForeignKeyConstraint(['user_id'], ['users.id'], name=op.f('fk_trips_user_id_users'), ondelete='RESTRICT'),
    sa.PrimaryKeyConstraint('id', name=op.f('pk_trips')),
    sa.UniqueConstraint('id', 'user_id', name=op.f('uq_trips_id_user_id'))
    )
    op.create_index('ix_trips_user_id_start_date_end_date', 'trips', ['user_id', 'start_date', 'end_date'], unique=False)
    op.create_table('user_financial_settings',
    sa.Column('user_id', sa.UUID(), nullable=False),
    sa.Column('starting_balance_kyat', sa.BigInteger(), nullable=True),
    sa.Column('effective_date', sa.Date(), nullable=True),
    sa.Column('revision', sa.BigInteger(), server_default=sa.text('1'), nullable=False),
    sa.Column('updated_at', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=False),
    sa.CheckConstraint('(starting_balance_kyat IS NULL) = (effective_date IS NULL)', name=op.f('ck_user_financial_settings_starting_balance_and_date_set_together')),
    sa.ForeignKeyConstraint(['user_id'], ['users.id'], name=op.f('fk_user_financial_settings_user_id_users'), ondelete='RESTRICT'),
    sa.PrimaryKeyConstraint('user_id', name=op.f('pk_user_financial_settings'))
    )
    op.create_table('user_roles',
    sa.Column('user_id', sa.UUID(), nullable=False),
    sa.Column('role_id', sa.SmallInteger(), nullable=False),
    sa.Column('granted_by_user_id', sa.UUID(), nullable=True),
    sa.Column('granted_at', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=False),
    sa.ForeignKeyConstraint(['granted_by_user_id'], ['users.id'], name=op.f('fk_user_roles_granted_by_user_id_users'), ondelete='SET NULL'),
    sa.ForeignKeyConstraint(['role_id'], ['roles.id'], name=op.f('fk_user_roles_role_id_roles'), ondelete='RESTRICT'),
    sa.ForeignKeyConstraint(['user_id'], ['users.id'], name=op.f('fk_user_roles_user_id_users'), ondelete='RESTRICT'),
    sa.PrimaryKeyConstraint('user_id', 'role_id', name=op.f('pk_user_roles'))
    )
    op.create_index('ix_user_roles_role_id_user_id', 'user_roles', ['role_id', 'user_id'], unique=False)
    op.create_table('loan_repayments',
    sa.Column('id', sa.UUID(), nullable=False),
    sa.Column('user_id', sa.UUID(), nullable=False),
    sa.Column('loan_id', sa.UUID(), nullable=False),
    sa.Column('amount_kyat', sa.BigInteger(), nullable=False),
    sa.Column('repayment_date', sa.Date(), nullable=False),
    sa.Column('note', sa.String(length=80), nullable=True),
    sa.Column('created_at', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=False),
    sa.Column('revision', sa.BigInteger(), server_default=sa.text('1'), nullable=False),
    sa.CheckConstraint('amount_kyat > 0', name=op.f('ck_loan_repayments_amount_positive')),
    sa.ForeignKeyConstraint(['loan_id', 'user_id'], ['loans.id', 'loans.user_id'], name=op.f('fk_loan_repayments_loan_id_user_id_loans'), ondelete='RESTRICT'),
    sa.ForeignKeyConstraint(['user_id'], ['users.id'], name=op.f('fk_loan_repayments_user_id_users'), ondelete='RESTRICT'),
    sa.PrimaryKeyConstraint('id', name=op.f('pk_loan_repayments')),
    sa.UniqueConstraint('id', 'loan_id', 'user_id', name=op.f('uq_loan_repayments_id_loan_id_user_id'))
    )
    op.create_index('ix_loan_repayments_user_id_loan_id_repayment_date', 'loan_repayments', ['user_id', 'loan_id', sa.literal_column('repayment_date DESC')], unique=False)
    op.create_table('savings_plans',
    sa.Column('id', sa.UUID(), nullable=False),
    sa.Column('user_id', sa.UUID(), nullable=False),
    sa.Column('goal_id', sa.UUID(), nullable=False),
    sa.Column('start_date', sa.Date(), nullable=False),
    sa.Column('duration_days', sa.SmallInteger(), nullable=False),
    sa.Column('daily_amount_kyat', sa.BigInteger(), nullable=False),
    sa.Column('created_at', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=False),
    sa.Column('updated_at', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=False),
    sa.Column('revision', sa.BigInteger(), server_default=sa.text('1'), nullable=False),
    sa.CheckConstraint('daily_amount_kyat > 0', name=op.f('ck_savings_plans_daily_amount_positive')),
    sa.CheckConstraint('duration_days BETWEEN 1 AND 365', name=op.f('ck_savings_plans_duration_days')),
    sa.ForeignKeyConstraint(['goal_id', 'user_id'], ['savings_goals.id', 'savings_goals.user_id'], name=op.f('fk_savings_plans_goal_id_user_id_savings_goals'), ondelete='RESTRICT'),
    sa.ForeignKeyConstraint(['user_id'], ['users.id'], name=op.f('fk_savings_plans_user_id_users'), ondelete='RESTRICT'),
    sa.PrimaryKeyConstraint('id', name=op.f('pk_savings_plans')),
    sa.UniqueConstraint('goal_id', name='uq_savings_plans_goal_id'),
    sa.UniqueConstraint('id', 'goal_id', 'user_id', name=op.f('uq_savings_plans_id_goal_id_user_id'))
    )
    op.create_table('trip_category_budgets',
    sa.Column('id', sa.UUID(), nullable=False),
    sa.Column('user_id', sa.UUID(), nullable=False),
    sa.Column('trip_id', sa.UUID(), nullable=False),
    sa.Column('category', sa.String(length=50), nullable=False),
    sa.Column('budget_kyat', sa.BigInteger(), nullable=False),
    sa.Column('created_at', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=False),
    sa.Column('updated_at', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=False),
    sa.Column('revision', sa.BigInteger(), server_default=sa.text('1'), nullable=False),
    sa.CheckConstraint('budget_kyat >= 0', name=op.f('ck_trip_category_budgets_budget_nonnegative')),
    sa.ForeignKeyConstraint(['trip_id', 'user_id'], ['trips.id', 'trips.user_id'], name=op.f('fk_trip_category_budgets_trip_id_user_id_trips'), ondelete='CASCADE'),
    sa.ForeignKeyConstraint(['user_id'], ['users.id'], name=op.f('fk_trip_category_budgets_user_id_users'), ondelete='RESTRICT'),
    sa.PrimaryKeyConstraint('id', name=op.f('pk_trip_category_budgets')),
    sa.UniqueConstraint('trip_id', 'id', name=op.f('uq_trip_category_budgets_trip_id_id'))
    )
    op.create_index('ix_trip_category_budgets_user_id_trip_id', 'trip_category_budgets', ['user_id', 'trip_id'], unique=False)
    op.create_index('uq_trip_category_budgets_trip_category_lower', 'trip_category_budgets', ['trip_id', sa.literal_column('lower(category)')], unique=True)
    op.create_table('loan_repayment_corrections',
    sa.Column('id', sa.UUID(), nullable=False),
    sa.Column('user_id', sa.UUID(), nullable=False),
    sa.Column('loan_id', sa.UUID(), nullable=False),
    sa.Column('repayment_id', sa.UUID(), nullable=False),
    sa.Column('type', sa.String(length=16), nullable=False),
    sa.Column('amount_kyat', sa.BigInteger(), nullable=False),
    sa.Column('reason', sa.String(length=200), nullable=False),
    sa.Column('created_by_user_id', sa.UUID(), nullable=False),
    sa.Column('created_at', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=False),
    sa.CheckConstraint("type = 'reversal'", name=op.f('ck_loan_repayment_corrections_type')),
    sa.CheckConstraint('amount_kyat > 0', name=op.f('ck_loan_repayment_corrections_amount_positive')),
    sa.CheckConstraint('created_by_user_id = user_id', name=op.f('ck_loan_repayment_corrections_created_by_user_matches_owner')),
    sa.ForeignKeyConstraint(['created_by_user_id'], ['users.id'], name=op.f('fk_loan_repayment_corrections_created_by_user_id_users'), ondelete='RESTRICT'),
    sa.ForeignKeyConstraint(['loan_id', 'user_id'], ['loans.id', 'loans.user_id'], name=op.f('fk_loan_repayment_corrections_loan_id_user_id_loans'), ondelete='RESTRICT'),
    sa.ForeignKeyConstraint(['repayment_id', 'loan_id', 'user_id'], ['loan_repayments.id', 'loan_repayments.loan_id', 'loan_repayments.user_id'], name=op.f('fk_loan_repayment_corrections_repayment_id_loan_id_user_id_loan_repayments'), ondelete='RESTRICT'),
    sa.ForeignKeyConstraint(['user_id'], ['users.id'], name=op.f('fk_loan_repayment_corrections_user_id_users'), ondelete='RESTRICT'),
    sa.PrimaryKeyConstraint('id', name=op.f('pk_loan_repayment_corrections')),
    sa.UniqueConstraint('repayment_id', name=op.f('uq_loan_repayment_corrections_repayment_id'))
    )
    op.create_index('ix_loan_repayment_corrections_user_id_loan_id_created_at', 'loan_repayment_corrections', ['user_id', 'loan_id', sa.literal_column('created_at DESC')], unique=False)
    op.create_table('savings_plan_days',
    sa.Column('id', sa.UUID(), nullable=False),
    sa.Column('user_id', sa.UUID(), nullable=False),
    sa.Column('goal_id', sa.UUID(), nullable=False),
    sa.Column('plan_id', sa.UUID(), nullable=False),
    sa.Column('day_number', sa.SmallInteger(), nullable=False),
    sa.Column('planned_date', sa.Date(), nullable=False),
    sa.Column('base_amount_kyat', sa.BigInteger(), nullable=False),
    sa.Column('carried_forward_kyat', sa.BigInteger(), nullable=False),
    sa.Column('required_amount_kyat', sa.BigInteger(), nullable=False),
    sa.Column('status', sa.String(length=10), server_default=sa.text("'pending'"), nullable=False),
    sa.Column('completion_date', sa.Date(), nullable=True),
    sa.Column('actual_amount_kyat', sa.BigInteger(), nullable=True),
    sa.Column('reconciled_contributions_kyat', sa.BigInteger(), nullable=True),
    sa.Column('unpaid_carry_forward_kyat', sa.BigInteger(), nullable=True),
    sa.CheckConstraint("(status = 'pending' AND completion_date IS NULL AND actual_amount_kyat IS NULL AND reconciled_contributions_kyat IS NULL AND unpaid_carry_forward_kyat IS NULL) OR (status = 'completed' AND completion_date IS NOT NULL AND actual_amount_kyat IS NOT NULL AND reconciled_contributions_kyat IS NOT NULL AND unpaid_carry_forward_kyat IS NOT NULL AND unpaid_carry_forward_kyat = 0) OR (status = 'missed' AND completion_date IS NULL AND actual_amount_kyat IS NULL AND reconciled_contributions_kyat IS NOT NULL AND unpaid_carry_forward_kyat IS NOT NULL)", name=op.f('ck_savings_plan_days_terminal_snapshot_matches_status')),
    sa.CheckConstraint("status IN ('pending', 'completed', 'missed')", name=op.f('ck_savings_plan_days_status')),
    sa.CheckConstraint('base_amount_kyat > 0', name=op.f('ck_savings_plan_days_base_amount_positive')),
    sa.CheckConstraint('carried_forward_kyat >= 0', name=op.f('ck_savings_plan_days_carried_forward_nonnegative')),
    sa.CheckConstraint('day_number BETWEEN 1 AND 365', name=op.f('ck_savings_plan_days_day_number')),
    sa.CheckConstraint('reconciled_contributions_kyat IS NULL OR reconciled_contributions_kyat >= 0', name=op.f('ck_savings_plan_days_reconciled_contributions_nonnegative')),
    sa.CheckConstraint('required_amount_kyat > 0', name=op.f('ck_savings_plan_days_required_amount_positive')),
    sa.CheckConstraint('unpaid_carry_forward_kyat IS NULL OR unpaid_carry_forward_kyat >= 0', name=op.f('ck_savings_plan_days_unpaid_carry_forward_nonnegative')),
    sa.ForeignKeyConstraint(['goal_id', 'user_id'], ['savings_goals.id', 'savings_goals.user_id'], name=op.f('fk_savings_plan_days_goal_id_user_id_savings_goals'), ondelete='RESTRICT'),
    sa.ForeignKeyConstraint(['plan_id', 'goal_id', 'user_id'], ['savings_plans.id', 'savings_plans.goal_id', 'savings_plans.user_id'], name=op.f('fk_savings_plan_days_plan_id_goal_id_user_id_savings_plans'), ondelete='RESTRICT'),
    sa.ForeignKeyConstraint(['user_id'], ['users.id'], name=op.f('fk_savings_plan_days_user_id_users'), ondelete='RESTRICT'),
    sa.PrimaryKeyConstraint('id', name=op.f('pk_savings_plan_days')),
    sa.UniqueConstraint('plan_id', 'day_number', name=op.f('uq_savings_plan_days_plan_id_day_number')),
    sa.UniqueConstraint('plan_id', 'planned_date', name=op.f('uq_savings_plan_days_plan_id_planned_date')),
    sa.UniqueConstraint('user_id', 'goal_id', 'id', name=op.f('uq_savings_plan_days_user_id_goal_id_id'))
    )
    op.create_index('ix_savings_plan_days_user_id_goal_id_planned_date', 'savings_plan_days', ['user_id', 'goal_id', 'planned_date'], unique=False)
    op.create_table('trip_spending_entries',
    sa.Column('id', sa.UUID(), nullable=False),
    sa.Column('user_id', sa.UUID(), nullable=False),
    sa.Column('trip_id', sa.UUID(), nullable=False),
    sa.Column('category_budget_id', sa.UUID(), nullable=True),
    sa.Column('category', sa.String(length=100), nullable=False),
    sa.Column('description', sa.String(length=100), nullable=False),
    sa.Column('amount_kyat', sa.BigInteger(), nullable=False),
    sa.Column('spending_date', sa.Date(), nullable=False),
    sa.Column('created_at', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=False),
    sa.Column('updated_at', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=False),
    sa.Column('revision', sa.BigInteger(), server_default=sa.text('1'), nullable=False),
    sa.CheckConstraint('amount_kyat > 0', name=op.f('ck_trip_spending_entries_amount_positive')),
    sa.ForeignKeyConstraint(['trip_id', 'category_budget_id'], ['trip_category_budgets.trip_id', 'trip_category_budgets.id'], name=op.f('fk_trip_spending_entries_trip_id_category_budget_id_trip_category_budgets')),
    sa.ForeignKeyConstraint(['trip_id', 'user_id'], ['trips.id', 'trips.user_id'], name=op.f('fk_trip_spending_entries_trip_id_user_id_trips'), ondelete='CASCADE'),
    sa.ForeignKeyConstraint(['user_id'], ['users.id'], name=op.f('fk_trip_spending_entries_user_id_users'), ondelete='RESTRICT'),
    sa.PrimaryKeyConstraint('id', name=op.f('pk_trip_spending_entries'))
    )
    op.create_index('ix_trip_spending_entries_trip_id_category_budget_id', 'trip_spending_entries', ['trip_id', 'category_budget_id'], unique=False)
    op.create_index('ix_trip_spending_entries_user_id_trip_id_spending_date', 'trip_spending_entries', ['user_id', 'trip_id', sa.literal_column('spending_date DESC')], unique=False)
    op.create_table('savings_entries',
    sa.Column('id', sa.UUID(), nullable=False),
    sa.Column('user_id', sa.UUID(), nullable=False),
    sa.Column('goal_id', sa.UUID(), nullable=False),
    sa.Column('plan_day_id', sa.UUID(), nullable=True),
    sa.Column('entry_type', sa.String(length=12), nullable=False),
    sa.Column('amount_kyat', sa.BigInteger(), nullable=False),
    sa.Column('entry_date', sa.Date(), nullable=False),
    sa.Column('note', sa.String(length=100), nullable=True),
    sa.Column('created_at', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=False),
    sa.Column('correction_of_entry_id', sa.UUID(), nullable=True),
    sa.Column('correction_reason', sa.String(length=200), nullable=True),
    sa.CheckConstraint("entry_type IN ('contribution', 'withdrawal')", name=op.f('ck_savings_entries_entry_type')),
    sa.CheckConstraint("plan_day_id IS NULL OR entry_type = 'contribution'", name=op.f('ck_savings_entries_plan_day_only_for_contributions')),
    sa.CheckConstraint('(correction_of_entry_id IS NULL AND correction_reason IS NULL) OR (correction_of_entry_id IS NOT NULL AND correction_reason IS NOT NULL AND plan_day_id IS NULL)', name=op.f('ck_savings_entries_correction_fields_consistent')),
    sa.CheckConstraint('correction_reason IS NULL OR length(trim(correction_reason)) > 0', name=op.f('ck_savings_entries_correction_reason_nonempty')),
    sa.CheckConstraint('amount_kyat > 0', name=op.f('ck_savings_entries_amount_positive')),
    sa.ForeignKeyConstraint(['goal_id', 'user_id'], ['savings_goals.id', 'savings_goals.user_id'], name=op.f('fk_savings_entries_goal_id_user_id_savings_goals'), ondelete='RESTRICT'),
    sa.ForeignKeyConstraint(['user_id', 'goal_id', 'correction_of_entry_id'], ['savings_entries.user_id', 'savings_entries.goal_id', 'savings_entries.id'], name=op.f('fk_savings_entries_user_id_goal_id_correction_of_entry_id_savings_entries')),
    sa.ForeignKeyConstraint(['user_id', 'goal_id', 'plan_day_id'], ['savings_plan_days.user_id', 'savings_plan_days.goal_id', 'savings_plan_days.id'], name=op.f('fk_savings_entries_user_id_goal_id_plan_day_id_savings_plan_days')),
    sa.ForeignKeyConstraint(['user_id'], ['users.id'], name=op.f('fk_savings_entries_user_id_users'), ondelete='RESTRICT'),
    sa.PrimaryKeyConstraint('id', name=op.f('pk_savings_entries')),
    sa.UniqueConstraint('user_id', 'goal_id', 'id', name=op.f('uq_savings_entries_user_id_goal_id_id'))
    )
    op.create_index('ix_savings_entries_user_id_goal_id_entry_date_id', 'savings_entries', ['user_id', 'goal_id', sa.literal_column('entry_date DESC'), 'id'], unique=False)
    op.create_index('ix_savings_entries_user_id_goal_id_plan_day_id', 'savings_entries', ['user_id', 'goal_id', 'plan_day_id'], unique=False)
    op.create_index('uq_savings_entries_correction_of_entry_id', 'savings_entries', ['correction_of_entry_id'], unique=True, postgresql_where=sa.text('correction_of_entry_id IS NOT NULL'))
    for statement in PROTECTION_FUNCTION_DDL:
        op.execute(statement)
    for statement in PROTECTION_TRIGGER_DDL:
        op.execute(statement)
    for statement in RUNTIME_PRIVILEGE_DDL:
        op.execute(statement)
    op.execute(CLEANUP_FUNCTION_DDL)
    op.execute(
        "REVOKE ALL PRIVILEGES ON FUNCTION "
        "public.cleanup_expired_operational_records() FROM PUBLIC, finpath_app"
    )
    op.execute(
        "GRANT EXECUTE ON FUNCTION "
        "public.cleanup_expired_operational_records() TO finpath_app"
    )
    # ### end Alembic commands ###


def downgrade() -> None:
    # ### commands auto generated by Alembic - please adjust! ###
    op.execute(
        "REVOKE ALL PRIVILEGES ON FUNCTION "
        "public.cleanup_expired_operational_records() FROM PUBLIC, finpath_app"
    )
    op.execute("DROP FUNCTION public.cleanup_expired_operational_records()")
    for trigger_name, table_name in PROTECTION_TRIGGER_DROP_DDL:
        op.execute(
            f"DROP TRIGGER {trigger_name} ON public.{table_name}"
        )
    for function_name in PROTECTION_FUNCTION_DROP_DDL:
        op.execute(f"DROP FUNCTION public.{function_name}()")
    op.drop_index('uq_savings_entries_correction_of_entry_id', table_name='savings_entries', postgresql_where=sa.text('correction_of_entry_id IS NOT NULL'))
    op.drop_index('ix_savings_entries_user_id_goal_id_plan_day_id', table_name='savings_entries')
    op.drop_index('ix_savings_entries_user_id_goal_id_entry_date_id', table_name='savings_entries')
    op.drop_table('savings_entries')
    op.drop_index('ix_trip_spending_entries_user_id_trip_id_spending_date', table_name='trip_spending_entries')
    op.drop_index('ix_trip_spending_entries_trip_id_category_budget_id', table_name='trip_spending_entries')
    op.drop_table('trip_spending_entries')
    op.drop_index('ix_savings_plan_days_user_id_goal_id_planned_date', table_name='savings_plan_days')
    op.drop_table('savings_plan_days')
    op.drop_index('ix_loan_repayment_corrections_user_id_loan_id_created_at', table_name='loan_repayment_corrections')
    op.drop_table('loan_repayment_corrections')
    op.drop_index('uq_trip_category_budgets_trip_category_lower', table_name='trip_category_budgets')
    op.drop_index('ix_trip_category_budgets_user_id_trip_id', table_name='trip_category_budgets')
    op.drop_table('trip_category_budgets')
    op.drop_table('savings_plans')
    op.drop_index('ix_loan_repayments_user_id_loan_id_repayment_date', table_name='loan_repayments')
    op.drop_table('loan_repayments')
    op.drop_index('ix_user_roles_role_id_user_id', table_name='user_roles')
    op.drop_table('user_roles')
    op.drop_table('user_financial_settings')
    op.drop_index('ix_trips_user_id_start_date_end_date', table_name='trips')
    op.drop_table('trips')
    op.drop_index('ix_transactions_user_id_transaction_date_id', table_name='transactions')
    op.drop_index('ix_transactions_user_id_category_id_transaction_date', table_name='transactions')
    op.drop_table('transactions')
    op.drop_index('ix_savings_goals_user_id_status_created_at', table_name='savings_goals')
    op.drop_table('savings_goals')
    op.drop_index('ix_loans_user_id_start_date', table_name='loans')
    op.drop_table('loans')
    op.drop_index('ix_idempotency_records_expires_at', table_name='idempotency_records')
    op.drop_table('idempotency_records')
    op.drop_index('ix_auth_sessions_user_id_revoked_at_refresh_expires_at', table_name='auth_sessions')
    op.drop_index('ix_auth_sessions_session_family_id_refresh_expires_at', table_name='auth_sessions')
    op.drop_table('auth_sessions')
    op.drop_index('ix_auth_action_tokens_user_id_token_type_expires_at', table_name='auth_action_tokens')
    op.drop_table('auth_action_tokens')
    op.drop_index('ix_admin_audit_logs_target_user_id_occurred_at', table_name='admin_audit_logs')
    op.drop_index('ix_admin_audit_logs_actor_user_id_occurred_at', table_name='admin_audit_logs')
    op.drop_table('admin_audit_logs')
    op.drop_index('uq_users_pending_email_lower', table_name='users', postgresql_where=sa.text('pending_email IS NOT NULL'))
    op.drop_index('uq_users_email_lower', table_name='users')
    op.drop_index('ix_users_status', table_name='users')
    op.drop_table('users')
    op.drop_index('uq_transaction_categories_type_name_lower', table_name='transaction_categories')
    op.drop_table('transaction_categories')
    op.drop_table('roles')
    # ### end Alembic commands ###
