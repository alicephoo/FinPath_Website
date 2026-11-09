"""Deterministic, idempotent seeding of approved reference data.

The seed inserts only the two approved roles and the predefined first-release
transaction categories. It never deletes or overwrites existing rows, so
running it repeatedly is safe.

Run it with the migration/provisioning identity (``finpath_migrator``) after
the initial migration has been applied::

    cd backend
    .venv/bin/python -m app.db.seed

``FINPATH_MIGRATION_DATABASE_URL`` must be set. The runtime
``finpath_app`` identity is rejected because reference data is
migration/provisioning-managed and runtime writes are intentionally withheld.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from uuid import UUID, uuid5

from sqlalchemy import Connection, create_engine, text

# Fixed namespace so category identifiers are stable across runs and
# environments. Changing it would change every seeded category identifier.
CATEGORY_NAMESPACE = UUID("6f1a5c2e-8b7d-4c3a-9e2f-1a2b3c4d5e6f")

_SCHEMA_PATTERN = re.compile(r"^[A-Za-z_][A-Za-z0-9_]*$")


@dataclass(frozen=True)
class RoleSeed:
    id: int
    name: str
    description: str


@dataclass(frozen=True)
class CategorySeed:
    id: UUID
    name: str
    type: str


@dataclass(frozen=True)
class SeedResult:
    roles_inserted: int
    categories_inserted: int


# Stable identifiers: the role ids are part of the schema contract because
# user_roles.role_id is a SMALLINT foreign key.
ROLE_SEEDS: tuple[RoleSeed, ...] = (
    RoleSeed(1, "user", "Standard user account"),
    RoleSeed(2, "admin", "Administrator account"),
)

# Exactly the predefined first-release categories. Do not add categories here
# without an approved product change.
_CATEGORY_DEFINITIONS: tuple[tuple[str, str], ...] = (
    ("income", "Salary"),
    ("income", "Freelance"),
    ("income", "Other income"),
    ("expense", "Groceries"),
    ("expense", "Transport"),
    ("expense", "Housing"),
    ("expense", "Utilities"),
    ("expense", "Dining"),
    ("expense", "Health"),
    ("expense", "Shopping"),
    ("expense", "Other"),
)

CATEGORY_SEEDS: tuple[CategorySeed, ...] = tuple(
    CategorySeed(
        id=uuid5(CATEGORY_NAMESPACE, f"{category_type}:{name}"),
        name=name,
        type=category_type,
    )
    for category_type, name in _CATEGORY_DEFINITIONS
)


def _quote_schema(schema: str) -> str:
    if not _SCHEMA_PATTERN.fullmatch(schema):
        raise ValueError(f"Unsafe schema name: {schema!r}")
    return f'"{schema}"'


def _require_reference_tables(connection: Connection, quoted_schema: str) -> None:
    missing = [
        table
        for table in ("roles", "transaction_categories")
        if connection.execute(
            text("SELECT to_regclass(:relation)"),
            {"relation": f"{quoted_schema}.{table}"},
        ).scalar_one()
        is None
    ]
    if missing:
        raise RuntimeError(
            "Reference tables are missing ("
            + ", ".join(missing)
            + "). Apply the initial Alembic migration with the migration "
            "identity before seeding."
        )


def seed_reference_data(
    connection: Connection,
    *,
    schema: str = "public",
) -> SeedResult:
    """Insert the approved roles and categories without overwriting anything.

    The caller owns the transaction. Conflicts are ignored, then every expected
    stable identifier is verified so a conflicting row with a different name or
    identifier fails loudly instead of being silently accepted.
    """
    quoted_schema = _quote_schema(schema)
    _require_reference_tables(connection, quoted_schema)

    roles_inserted = 0
    for role in ROLE_SEEDS:
        result = connection.execute(
            text(
                f"INSERT INTO {quoted_schema}.roles (id, name, description) "
                "VALUES (:id, :name, :description) ON CONFLICT DO NOTHING"
            ),
            {"id": role.id, "name": role.name, "description": role.description},
        )
        roles_inserted += result.rowcount or 0

    role_rows = connection.execute(
        text(f"SELECT id, name FROM {quoted_schema}.roles")
    ).all()
    roles_by_id = {row.id: row.name for row in role_rows}
    for role in ROLE_SEEDS:
        if roles_by_id.get(role.id) != role.name:
            raise RuntimeError(
                f"Role id {role.id} does not map to {role.name!r}; "
                "refusing to overwrite existing reference data."
            )
        case_insensitive_matches = [
            role_id
            for role_id, name in roles_by_id.items()
            if name.lower() == role.name
        ]
        if case_insensitive_matches != [role.id]:
            raise RuntimeError(
                f"Ambiguous role name {role.name!r} in roles table; "
                "refusing to overwrite existing reference data."
            )

    categories_inserted = 0
    for category in CATEGORY_SEEDS:
        result = connection.execute(
            text(
                f"INSERT INTO {quoted_schema}.transaction_categories "
                "(id, name, type, is_active) "
                "VALUES (:id, :name, :type, true) ON CONFLICT DO NOTHING"
            ),
            {
                "id": category.id,
                "name": category.name,
                "type": category.type,
            },
        )
        categories_inserted += result.rowcount or 0

    category_rows = connection.execute(
        text(
            f"SELECT id, name, type "
            f"FROM {quoted_schema}.transaction_categories"
        )
    ).all()
    categories_by_id = {
        UUID(str(row.id)): (row.name, row.type) for row in category_rows
    }
    categories_by_key = {
        (row.type, row.name.lower()): UUID(str(row.id))
        for row in category_rows
    }
    for category in CATEGORY_SEEDS:
        if categories_by_id.get(category.id) != (category.name, category.type):
            raise RuntimeError(
                f"Category id {category.id} does not map to "
                f"{category.type!r}/{category.name!r}; refusing to overwrite "
                "existing reference data."
            )
        key = (category.type, category.name.lower())
        if categories_by_key.get(key) != category.id:
            raise RuntimeError(
                f"Category {category.type!r}/{category.name!r} is occupied by "
                "a different identifier; refusing to overwrite existing "
                "reference data."
            )

    return SeedResult(
        roles_inserted=roles_inserted,
        categories_inserted=categories_inserted,
    )


def main() -> int:
    """Seed the configured database using the migration identity."""
    from app.core.config import MigrationSettings
    from sqlalchemy.exc import SQLAlchemyError

    settings = MigrationSettings()
    engine = create_engine(
        settings.database_url.get_secret_value(),
        pool_pre_ping=True,
        hide_parameters=True,
    )
    current_user = ""
    current_database = ""
    try:
        with engine.begin() as connection:
            current_user = connection.execute(
                text("SELECT current_user")
            ).scalar_one()
            current_database = connection.execute(
                text("SELECT current_database()")
            ).scalar_one()
            if current_user == "finpath_app":
                raise SystemExit(
                    "Refusing to seed with the runtime role finpath_app. Use "
                    "the migration/provisioning identity "
                    "(FINPATH_MIGRATION_DATABASE_URL)."
                )
            result = seed_reference_data(connection)
    except SystemExit:
        raise
    except (RuntimeError, SQLAlchemyError) as exc:
        # Never print connection URLs or credentials. Report the error class
        # and a sanitized message only.
        message = str(exc).splitlines()[0]
        if "postgresql" in message.lower() or "password" in message.lower():
            message = exc.__class__.__name__
        print(f"Seeding failed: {message}")
        return 1
    finally:
        engine.dispose()

    print(
        f"Seeded reference data in database {current_database} as "
        f"{current_user}: {result.roles_inserted} role(s) and "
        f"{result.categories_inserted} category(ies) inserted; existing rows "
        "were preserved."
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
