from __future__ import annotations

from uuid import uuid4

import pytest
from sqlalchemy import create_engine, text
from sqlalchemy.engine import Engine

from app.db import seed
from app.db.base import Base
import app.models
from tests.pg_test_env import require_test_database_urls


@pytest.fixture
def seed_schema():
    _, migration_url, test_database = require_test_database_urls()
    engine: Engine = create_engine(
        migration_url,
        hide_parameters=True,
        pool_pre_ping=True,
    )
    schema = f"finpath_seed_test_{uuid4().hex}"
    created = False
    try:
        with engine.begin() as setup:
            assert setup.execute(text("SELECT current_user")).scalar_one() == (
                "finpath_migrator"
            )
            assert setup.execute(
                text("SELECT current_database()")
            ).scalar_one() == test_database
            setup.execute(text(f'CREATE SCHEMA "{schema}"'))
        created = True
        with engine.begin() as setup:
            translated = setup.execution_options(
                schema_translate_map={None: schema}
            )
            Base.metadata.create_all(
                translated,
                tables=[
                    Base.metadata.tables["roles"],
                    Base.metadata.tables["transaction_categories"],
                ],
            )
        yield engine, schema
    finally:
        if created:
            with engine.begin() as cleanup:
                cleanup.execute(text(f'DROP SCHEMA "{schema}" CASCADE'))
        engine.dispose()


def test_seed_is_idempotent_and_preserves_existing_rows(seed_schema) -> None:
    engine, schema = seed_schema

    with engine.begin() as connection:
        first = seed.seed_reference_data(connection, schema=schema)
    assert first == seed.SeedResult(roles_inserted=2, categories_inserted=11)

    with engine.begin() as connection:
        connection.execute(
            text(
                f'UPDATE "{schema}".roles SET description = :description '
                "WHERE id = 1"
            ),
            {"description": "Operator-customized"},
        )

    with engine.begin() as connection:
        second = seed.seed_reference_data(connection, schema=schema)
    assert second == seed.SeedResult(roles_inserted=0, categories_inserted=0)

    with engine.connect() as connection:
        roles = connection.execute(
            text(
                f'SELECT id, name, description FROM "{schema}".roles '
                "ORDER BY id"
            )
        ).all()
        categories = connection.execute(
            text(
                f'SELECT id, name, type, is_active '
                f'FROM "{schema}".transaction_categories'
            )
        ).all()

    assert [(row.id, row.name) for row in roles] == [
        (1, "user"),
        (2, "admin"),
    ]
    assert roles[0].description == "Operator-customized"
    assert len(categories) == 11
    assert {(row.type, row.name) for row in categories} == {
        (category.type, category.name)
        for category in seed.CATEGORY_SEEDS
    }
    assert all(row.is_active for row in categories)


def test_seed_refuses_a_conflicting_category_identifier(seed_schema) -> None:
    engine, schema = seed_schema

    with engine.begin() as connection:
        connection.execute(
            text(
                f'INSERT INTO "{schema}".transaction_categories '
                "(id, name, type) VALUES (:id, 'Salary', 'income')"
            ),
            {"id": uuid4()},
        )

    with pytest.raises(RuntimeError, match="does not map to"):
        with engine.begin() as connection:
            seed.seed_reference_data(connection, schema=schema)

    # The failed seed transaction rolled back, so the conflict row remains the
    # only Salary category.
    with engine.connect() as connection:
        assert connection.execute(
            text(
                f'SELECT count(*) FROM "{schema}".transaction_categories '
                "WHERE type = 'income' AND lower(name) = 'salary'"
            )
        ).scalar_one() == 1
