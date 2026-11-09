import importlib.util
from pathlib import Path
import re

from alembic.config import Config
from alembic.script import ScriptDirectory

from app.db.base import Base, NAMING_CONVENTION
import app.models

BACKEND_DIR = Path(__file__).resolve().parents[1]


def test_alembic_configuration_discovers_migrations_and_shared_metadata() -> None:
    config = Config(str(BACKEND_DIR / "alembic.ini"))
    scripts = ScriptDirectory.from_config(config)

    assert Path(scripts.dir) == BACKEND_DIR / "migrations"
    assert config.get_main_option("sqlalchemy.url") == ""
    assert Base.metadata.naming_convention == NAMING_CONVENTION
    assert len(Base.metadata.tables) == 20
    heads = scripts.get_heads()
    assert len(heads) == 1
    revision = scripts.get_revision(heads[0])
    assert revision is not None
    assert revision.revision == "c4f7a1b9e2d6"
    assert revision.down_revision == "b9e4d2a7c1f3"
    assert Path(revision.path).stem.endswith("_trip_status")

    trip_status = scripts.get_revision("c4f7a1b9e2d6")
    assert trip_status is not None
    assert trip_status.down_revision == "b9e4d2a7c1f3"
    assert Path(trip_status.path).stem.endswith("_trip_status")

    registration_token_guard = scripts.get_revision("f4a1c2d3e5b6")
    assert registration_token_guard is not None
    assert registration_token_guard.down_revision == "e1b9c0d4a7f2"
    assert Path(registration_token_guard.path).stem.endswith(
        "_registration_token_guard"
    )

    registration_function_privileges = scripts.get_revision("e1b9c0d4a7f2")
    assert registration_function_privileges is not None
    assert registration_function_privileges.down_revision == "c7a4e2f19b30"
    assert Path(registration_function_privileges.path).stem.endswith(
        "_registration_function_privileges"
    )

    registration_identity = scripts.get_revision("c7a4e2f19b30")
    assert registration_identity is not None
    assert registration_identity.down_revision == "7270e464aca9"
    assert Path(registration_identity.path).stem.endswith(
        "_registration_identity"
    )

    initial = scripts.get_revision("7270e464aca9")
    assert initial is not None
    assert initial.down_revision is None
    assert Path(initial.path).stem.endswith("_initial_schema")


def test_migration_configuration_does_not_store_database_credentials() -> None:
    config_text = (BACKEND_DIR / "alembic.ini").read_text()
    env_text = (BACKEND_DIR / "migrations" / "env.py").read_text()
    migration_readme = (BACKEND_DIR / "migrations" / "README.md").read_text()

    assert "sqlalchemy.url =" in config_text
    assert "MigrationSettings()" in env_text
    assert "migration_settings.database_url.get_secret_value()" in env_text
    assert "from app.db.session import engine" not in env_text
    assert "hide_password=True" in env_text
    assert "target_metadata = Base.metadata" in env_text
    assert "import app.models" in env_text
    assert "FINPATH_MIGRATION_DATABASE_URL" in migration_readme
    assert "postgresql://" not in config_text


def test_initial_revision_contains_reversible_protection_ddl() -> None:
    config = Config(str(BACKEND_DIR / "alembic.ini"))
    scripts = ScriptDirectory.from_config(config)
    revision = scripts.get_revision("7270e464aca9")
    assert revision is not None
    assert Path(revision.path).name == "7270e464aca9_initial_schema.py"

    spec = importlib.util.spec_from_file_location(
        "finpath_initial_schema_protection_ddl",
        revision.path,
    )
    assert spec is not None and spec.loader is not None
    migration = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(migration)

    function_names = {
        re.search(r"CREATE FUNCTION public\.(\w+)\(", ddl).group(1)
        for ddl in migration.PROTECTION_FUNCTION_DDL
    }
    trigger_names = {
        re.search(r"CREATE (?:CONSTRAINT )?TRIGGER (\w+)", ddl).group(1)
        for ddl in migration.PROTECTION_TRIGGER_DDL
    }
    assert migration.revision == "7270e464aca9"
    assert len(function_names) == 10
    assert len(trigger_names) == 10
    assert function_names == set(migration.PROTECTION_FUNCTION_DROP_DDL)
    assert trigger_names == {
        trigger_name
        for trigger_name, _ in migration.PROTECTION_TRIGGER_DROP_DDL
    }
    assert len(Base.metadata.tables) == 20
    revoked_tables = {
        re.search(r"REVOKE ALL PRIVILEGES ON TABLE public\.(\w+)", ddl).group(1)
        for ddl in migration.RUNTIME_PRIVILEGE_DDL
        if ddl.startswith("REVOKE ALL PRIVILEGES ON TABLE")
    }
    assert revoked_tables == set(Base.metadata.tables)
    assert "SECURITY DEFINER" in migration.CLEANUP_FUNCTION_DDL
    assert "SET search_path = pg_catalog" in migration.CLEANUP_FUNCTION_DDL
    assert "state = 'completed'" in migration.CLEANUP_FUNCTION_DDL
    assert "completed_at <= pg_catalog.clock_timestamp() - INTERVAL '24 hours'" in (
        migration.CLEANUP_FUNCTION_DDL
    )
    cleanup_sql = " ".join(migration.CLEANUP_FUNCTION_DDL.split())
    assert (
        "refresh_expires_at <= pg_catalog.clock_timestamp() - INTERVAL '30 days'"
        in cleanup_sql
    )
    assert "public.loan_repayments" not in migration.CLEANUP_FUNCTION_DDL
    assert [
        ddl
        for ddl in migration.RUNTIME_PRIVILEGE_DDL
        if "public.user_roles" in ddl
    ] == [
        "REVOKE ALL PRIVILEGES ON TABLE public.user_roles FROM PUBLIC, finpath_app",
        "GRANT SELECT ON TABLE public.user_roles TO finpath_app",
    ]
    downgrade_text = Path(revision.path).read_text().split("def downgrade()", 1)[1]
    assert "DROP FUNCTION public.cleanup_expired_operational_records()" in downgrade_text


def test_registration_identity_migration_uses_least_privilege_guard() -> None:
    config = Config(str(BACKEND_DIR / "alembic.ini"))
    scripts = ScriptDirectory.from_config(config)
    revision = scripts.get_revision("c7a4e2f19b30")
    assert revision is not None
    assert Path(revision.path).name == "c7a4e2f19b30_registration_identity.py"

    spec = importlib.util.spec_from_file_location(
        "finpath_registration_identity_ddl",
        revision.path,
    )
    assert spec is not None and spec.loader is not None
    migration = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(migration)

    function_ddl = " ".join(migration.REGISTRATION_ROLE_GUARD_FUNCTION.split())
    assert "SECURITY DEFINER" not in function_ddl
    assert "current_user = 'finpath_registration'" in function_ddl
    assert "NEW.role_id <> 1" in function_ddl
    assert "insufficient_privilege" in function_ddl

    grants = " ".join(migration.REGISTRATION_PRIVILEGE_DDL)
    assert "GRANT INSERT (id, email, password_hash, timezone)" in grants
    assert "GRANT INSERT (user_id, role_id)" in grants
    assert "GRANT INSERT (user_id)" in grants
    assert "ON TABLE public.transactions" not in grants
    assert "ON TABLE public.loans" not in grants
    assert "ON TABLE public.savings_entries" not in grants
    assert "GRANT DELETE" not in grants
    assert "GRANT UPDATE ON TABLE" not in grants

    downgrade_text = Path(revision.path).read_text().split("def downgrade()", 1)[1]
    assert "DROP TRIGGER IF EXISTS trg_user_roles_registration_role_guard" in (
        downgrade_text
    )
    assert "DROP FUNCTION IF EXISTS" in downgrade_text


def test_registration_function_privileges_revoke_public_execute() -> None:
    config = Config(str(BACKEND_DIR / "alembic.ini"))
    scripts = ScriptDirectory.from_config(config)
    revision = scripts.get_revision("e1b9c0d4a7f2")
    assert revision is not None
    assert Path(revision.path).name == (
        "e1b9c0d4a7f2_registration_function_privileges.py"
    )

    migration_text = Path(revision.path).read_text()
    assert "REVOKE ALL PRIVILEGES ON FUNCTION" in migration_text
    assert "enforce_registration_role_assignment()" in migration_text
    assert "FROM PUBLIC, finpath_app, finpath_registration" in migration_text
    assert "SECURITY DEFINER" not in migration_text


def test_registration_token_guard_is_scoped_and_not_security_definer() -> None:
    config = Config(str(BACKEND_DIR / "alembic.ini"))
    scripts = ScriptDirectory.from_config(config)
    revision = scripts.get_revision("f4a1c2d3e5b6")
    assert revision is not None
    assert Path(revision.path).name == (
        "f4a1c2d3e5b6_registration_token_guard.py"
    )

    migration_text = Path(revision.path).read_text()
    assert "enforce_registration_action_token()" in migration_text
    assert "trg_auth_action_tokens_registration_guard" in migration_text
    assert "current_user = 'finpath_registration'" in migration_text
    assert "NEW.token_type <> 'email_verification'" in migration_text
    assert "NEW.target_email IS NOT NULL" in migration_text
    assert "REVOKE ALL PRIVILEGES ON FUNCTION" in migration_text
    assert "FROM PUBLIC, finpath_app, finpath_registration" in migration_text
    assert "SECURITY DEFINER" not in migration_text


def test_registration_verification_grant_is_column_scoped_and_reversible() -> None:
    config = Config(str(BACKEND_DIR / "alembic.ini"))
    scripts = ScriptDirectory.from_config(config)
    revision = scripts.get_revision("b9e4d2a7c1f3")
    assert revision is not None
    assert Path(revision.path).name == (
        "b9e4d2a7c1f3_registration_verification_grant.py"
    )

    spec = importlib.util.spec_from_file_location(
        "finpath_registration_verification_grant_ddl",
        revision.path,
    )
    assert spec is not None and spec.loader is not None
    migration = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(migration)

    grants = " ".join(migration.REGISTRATION_VERIFICATION_PRIVILEGE_DDL)
    assert (
        "GRANT INSERT (email_verified_at) "
        "ON TABLE public.users TO finpath_registration"
    ) in grants
    assert "GRANT UPDATE" not in grants
    assert "GRANT ALL" not in grants
    assert "transactions" not in grants
    assert "loans" not in grants
    assert "savings_entries" not in grants

    revoke = " ".join(migration.REGISTRATION_VERIFICATION_REVOKE_DDL)
    assert (
        "REVOKE INSERT (email_verified_at) "
        "ON TABLE public.users FROM finpath_registration"
    ) == revoke
