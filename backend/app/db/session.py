import logging
from collections.abc import Generator

from sqlalchemy import create_engine, text
from sqlalchemy.exc import OperationalError, SQLAlchemyError
from sqlalchemy.orm import Session, sessionmaker

from app.core.config import settings

logger = logging.getLogger(__name__)


class DatabaseUnavailableError(Exception):
    pass


def _create_engine(database_url: str):
    return create_engine(
        database_url,
        pool_pre_ping=True,
        pool_size=5,
        max_overflow=10,
        pool_timeout=30,
        hide_parameters=True,
    )


engine = _create_engine(settings.database_url.get_secret_value())

# The registration identity is deliberately separate from the normal runtime
# identity. It is used only by the registration transaction, never by
# authenticated requests.
registration_engine = _create_engine(
    settings.registration_database_url.get_secret_value()
)

SessionLocal = sessionmaker(
    bind=engine,
    autoflush=False,
    expire_on_commit=False,
)

RegistrationSessionLocal = sessionmaker(
    bind=registration_engine,
    autoflush=False,
    expire_on_commit=False,
)


def get_db() -> Generator[Session, None, None]:
    session = SessionLocal()
    try:
        yield session
    except OperationalError:
        logger.warning(
            "Database connection or operation failed.",
            extra={"event": "database_unavailable"},
        )
        raise DatabaseUnavailableError from None
    finally:
        session.close()


def get_registration_db() -> Generator[Session, None, None]:
    session = RegistrationSessionLocal()
    try:
        yield session
    except OperationalError:
        logger.warning(
            "Registration database connection or operation failed.",
            extra={"event": "registration_database_unavailable"},
        )
        raise DatabaseUnavailableError from None
    finally:
        session.close()


def check_database_ready() -> bool:
    """Return whether the configured runtime database answers a read-only probe.

    Only a lightweight ``SELECT 1`` is issued; no application data is read or
    written. Credentials, connection strings, SQL text, and driver details are
    never returned to the caller or included in the log record.
    """

    try:
        with engine.connect() as connection:
            connection.execute(text("SELECT 1"))
    except SQLAlchemyError:
        logger.warning(
            "Database readiness check failed.",
            extra={"event": "database_readiness_failed"},
        )
        return False
    return True
