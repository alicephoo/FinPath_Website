# Alembic migrations

Alembic requires `FINPATH_MIGRATION_DATABASE_URL`, loaded as a secret through
Pydantic Settings. It never falls back to the runtime `FINPATH_DATABASE_URL`.
The migration URL is not stored in `alembic.ini` or migration revisions.
Offline SQL output masks the password; SQLAlchemy engine parameter hiding is
enabled.

Use the dedicated `finpath_migrator` role for migration commands. Migrations
are explicit and never run during application startup. The runtime
`finpath_app` role is not a schema owner and receives no DDL privileges.
