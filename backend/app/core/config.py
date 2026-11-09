import ipaddress
from urllib.parse import urlparse

from pydantic import SecretStr, model_validator
from pydantic_settings import BaseSettings, SettingsConfigDict

_PRODUCTION_ENVIRONMENTS = {"production", "prod"}
_LOCAL_HOSTNAMES = {"localhost", "127.0.0.1", "0.0.0.0", "::1"}


def _is_local_hostname(hostname: str) -> bool:
    if hostname in _LOCAL_HOSTNAMES or hostname.endswith(".localhost"):
        return True
    try:
        address = ipaddress.ip_address(hostname)
    except ValueError:
        return False
    return address.is_loopback or address.is_unspecified


def _is_production_frontend_origin(origin: str) -> bool:
    """Return whether ``origin`` is a deployable HTTPS frontend origin."""

    try:
        parsed = urlparse(origin)
        hostname = parsed.hostname
    except ValueError:
        return False
    if parsed.scheme != "https":
        return False
    if parsed.username or parsed.password:
        return False
    if parsed.params or parsed.query or parsed.fragment:
        return False
    if parsed.path not in {"", "/"}:
        return False
    if hostname is None:
        return False
    return not _is_local_hostname(hostname.rstrip(".").lower())


class Settings(BaseSettings):
    app_name: str = "FinPath API"
    environment: str = "development"
    # Optional observability controls. Defaults keep local development quiet
    # while still emitting one structured record per request.
    log_level: str = "INFO"
    slow_request_threshold_ms: int = 1000
    database_url: SecretStr = SecretStr(
        "postgresql+psycopg://finpath_app@127.0.0.1:5432/finpath"
    )
    # Registration writes use a dedicated least-privilege login identity. The
    # runtime ``finpath_app`` identity remains read-only on ``user_roles``.
    registration_database_url: SecretStr = SecretStr(
        "postgresql+psycopg://finpath_registration@127.0.0.1:5432/finpath"
    )

    # Access tokens are signed JWTs. The secret must be supplied through the
    # environment; an empty value makes token issuance fail closed.
    access_token_secret: SecretStr = SecretStr("")
    access_token_issuer: str = "finpath-api"
    access_token_audience: str = "finpath-web"
    access_token_ttl_seconds: int = 900
    csrf_token_audience: str = "finpath-csrf"
    csrf_token_ttl_seconds: int = 900

    refresh_token_ttl_seconds: int = 30 * 24 * 60 * 60
    refresh_inactivity_ttl_seconds: int = 7 * 24 * 60 * 60
    # Email verification is required by default. Local development may disable
    # it when FINPATH_ENVIRONMENT is not production. Production fails closed if
    # verification is disabled unless the deployment explicitly opts in to an
    # MVP with no email provider.
    require_email_verification: bool = True
    allow_unverified_registration: bool = False
    email_verification_ttl_seconds: int = 24 * 60 * 60
    password_reset_ttl_seconds: int = 30 * 60
    password_min_length: int = 12

    # Maximum amount for one income/expense entry. This is an entry limit, not
    # a database maximum or a cap on aggregates. It must remain a positive
    # whole-Kyat integer and within the JSON safe-integer range.
    max_financial_entry_kyat: int = 1_000_000_000

    refresh_cookie_name: str = "finpath_refresh"
    refresh_cookie_path: str = "/api/v1/auth"
    # ``None`` selects an environment-aware default: Secure outside local
    # development, and not Secure in development so an HTTP frontend/API can
    # persist the refresh cookie in browsers that do not treat localhost as a
    # secure context. Production can never disable the Secure attribute.
    refresh_cookie_secure: bool | None = None

    frontend_origins: str = (
        "http://localhost:5173,http://127.0.0.1:5173"
    )

    # ``outbox`` writes development email to a local, owner-only directory so
    # no real delivery is claimed. ``resend`` uses the Resend HTTPS API and
    # requires FINPATH_RESEND_API_KEY plus FINPATH_EMAIL_FROM.
    email_backend: str = "outbox"
    email_outbox_dir: str = "var/email_outbox"
    resend_api_key: SecretStr = SecretStr("")
    email_from: str = ""

    model_config = SettingsConfigDict(
        env_prefix="FINPATH_",
        env_file=".env",
        extra="ignore",
    )

    @property
    def frontend_origin_list(self) -> tuple[str, ...]:
        return tuple(
            origin.strip()
            for origin in self.frontend_origins.split(",")
            if origin.strip()
        )

    @model_validator(mode="after")
    def prevent_insecure_verification_bypass(self) -> "Settings":
        environment = self.environment.strip().lower()
        if (
            environment in {"production", "prod"}
            and not self.require_email_verification
            and not self.allow_unverified_registration
        ):
            raise ValueError(
                "Email verification cannot be disabled in production unless "
                "FINPATH_ALLOW_UNVERIFIED_REGISTRATION=true is explicitly set."
            )

        if self.refresh_cookie_secure is None:
            self.refresh_cookie_secure = environment not in {
                "development",
                "local",
                "test",
                "testing",
            }
        if (
            environment in {"production", "prod"}
            and not self.refresh_cookie_secure
        ):
            raise ValueError(
                "The refresh cookie cannot be made insecure in production."
            )
        return self

    @model_validator(mode="after")
    def validate_email_delivery(self) -> "Settings":
        environment = self.environment.strip().lower()
        backend = self.email_backend.strip().lower()

        if backend not in {"outbox", "resend"}:
            raise ValueError(
                "FINPATH_EMAIL_BACKEND must be 'outbox' or 'resend'."
            )
        if (
            environment in {"production", "prod"}
            and self.require_email_verification
            and backend != "resend"
        ):
            raise ValueError(
                "Production email verification requires "
                "FINPATH_EMAIL_BACKEND=resend."
            )
        if backend == "resend":
            api_key = self.resend_api_key.get_secret_value().strip()
            sender = self.email_from.strip()
            if not api_key:
                raise ValueError(
                    "FINPATH_RESEND_API_KEY is required when "
                    "FINPATH_EMAIL_BACKEND=resend."
                )
            if (
                not sender
                or "@" not in sender
                or any(character in sender for character in "\r\n")
            ):
                raise ValueError(
                    "FINPATH_EMAIL_FROM must be a valid verified sender "
                    "address when FINPATH_EMAIL_BACKEND=resend."
                )
        return self

    @model_validator(mode="after")
    def validate_production_frontend_origins(self) -> "Settings":
        environment = self.environment.strip().lower()
        if environment not in _PRODUCTION_ENVIRONMENTS:
            return self

        origins = self.frontend_origin_list
        if not origins:
            raise ValueError(
                "FINPATH_FRONTEND_ORIGINS must list at least one HTTPS "
                "frontend origin in production."
            )
        if not all(
            _is_production_frontend_origin(origin) for origin in origins
        ):
            raise ValueError(
                "FINPATH_FRONTEND_ORIGINS must contain only HTTPS, "
                "non-localhost origins in production. Configure the deployed "
                "frontend origin, for example "
                "FINPATH_FRONTEND_ORIGINS=https://<project>.pages.dev."
            )
        return self


class MigrationSettings(BaseSettings):
    database_url: SecretStr

    model_config = SettingsConfigDict(
        env_prefix="FINPATH_MIGRATION_",
        env_file=".env",
        extra="ignore",
    )


class PostgresTestSettings(BaseSettings):
    """Connection settings for the dedicated PostgreSQL test database.

    Tests never fall back to the runtime, registration, or migration URLs.
    When these are unset, PostgreSQL tests skip with setup instructions
    instead of touching the development database.
    """

    database_url: SecretStr | None = None
    migration_database_url: SecretStr | None = None
    registration_database_url: SecretStr | None = None

    model_config = SettingsConfigDict(
        env_prefix="FINPATH_TEST_",
        env_file=".env",
        extra="ignore",
    )


settings = Settings()
