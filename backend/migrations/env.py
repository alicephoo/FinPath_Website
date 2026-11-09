from logging.config import fileConfig

from alembic import context
from sqlalchemy import create_engine
from sqlalchemy.engine import make_url

from app.core.config import MigrationSettings
from app.db.base import Base
import app.models

config = context.config
migration_settings = MigrationSettings()
migration_engine = create_engine(
    migration_settings.database_url.get_secret_value(),
    pool_pre_ping=True,
    hide_parameters=True,
)

if config.config_file_name is not None:
    fileConfig(config.config_file_name)

target_metadata = Base.metadata


def run_migrations_offline() -> None:
    masked_url = make_url(
        migration_settings.database_url.get_secret_value()
    ).render_as_string(hide_password=True)
    context.configure(
        url=masked_url,
        target_metadata=target_metadata,
        literal_binds=True,
        dialect_opts={"paramstyle": "named"},
        compare_type=True,
        compare_server_default=True,
    )

    with context.begin_transaction():
        context.run_migrations()


def run_migrations_online() -> None:
    try:
        with migration_engine.connect() as connection:
            context.configure(
                connection=connection,
                target_metadata=target_metadata,
                compare_type=True,
                compare_server_default=True,
            )

            with context.begin_transaction():
                context.run_migrations()
    finally:
        migration_engine.dispose()


if context.is_offline_mode():
    run_migrations_offline()
else:
    run_migrations_online()
