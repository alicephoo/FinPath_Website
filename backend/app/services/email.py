"""Email-delivery abstraction.

No real provider is configured for the initial backend. The development
adapter writes messages to an owner-only local outbox so the application never
claims that an email was delivered when it was not. Production deployments must
configure a real sender before enabling registration.
"""

from __future__ import annotations

import json
import logging
import os
import urllib.error
import urllib.request
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Protocol
from uuid import uuid4

from app.core.config import settings

logger = logging.getLogger(__name__)

RESEND_EMAILS_URL = "https://api.resend.com/emails"
RESEND_TIMEOUT_SECONDS = 10.0


class EmailDeliveryError(Exception):
    pass


@dataclass(frozen=True)
class EmailMessage:
    to: str
    subject: str
    text: str
    token: str
    purpose: str


class EmailSender(Protocol):
    @property
    def is_available(self) -> bool:
        """Whether this sender can attempt delivery in this environment."""
        ...

    def send(self, message: EmailMessage) -> None: ...


class ResendEmailSender:
    """Production sender using the fixed Resend HTTPS API endpoint."""

    @property
    def is_available(self) -> bool:
        return True

    def __init__(
        self,
        *,
        api_key: str,
        from_address: str,
        timeout_seconds: float = RESEND_TIMEOUT_SECONDS,
    ) -> None:
        if not api_key.strip():
            raise EmailDeliveryError("Resend API key is not configured.")
        if not from_address.strip():
            raise EmailDeliveryError("Resend sender address is not configured.")
        self._api_key = api_key
        self._from_address = from_address
        self._timeout_seconds = timeout_seconds

    def send(self, message: EmailMessage) -> None:
        payload = {
            "from": self._from_address,
            "to": [message.to],
            "subject": message.subject,
            "text": message.text,
        }
        request = urllib.request.Request(
            RESEND_EMAILS_URL,
            data=json.dumps(payload).encode("utf-8"),
            headers={
                "Authorization": f"Bearer {self._api_key}",
                "Content-Type": "application/json",
                "Accept": "application/json",
            },
            method="POST",
        )
        try:
            with urllib.request.urlopen(
                request,
                timeout=self._timeout_seconds,
            ) as response:
                if not 200 <= response.status < 300:
                    raise EmailDeliveryError("Resend rejected the email.")
        except urllib.error.HTTPError:
            raise EmailDeliveryError("Resend rejected the email.") from None
        except (urllib.error.URLError, TimeoutError, OSError):
            raise EmailDeliveryError("Resend email delivery failed.") from None


class OutboxEmailSender:
    """Development-only sender that persists messages to a private directory."""

    def __init__(self, directory: Path) -> None:
        self._directory = directory

    @property
    def is_available(self) -> bool:
        return True

    def send(self, message: EmailMessage) -> None:
        try:
            self._directory.mkdir(mode=0o700, parents=True, exist_ok=True)
            os.chmod(self._directory, 0o700)
            path = self._directory / f"{uuid4().hex}.json"
            descriptor = os.open(
                path,
                os.O_WRONLY | os.O_CREAT | os.O_EXCL,
                0o600,
            )
            with os.fdopen(descriptor, "w", encoding="utf-8") as handle:
                json.dump(asdict(message), handle)
        except OSError as exc:
            raise EmailDeliveryError("Development outbox write failed.") from exc
        # Never log the recipient address or the raw token.
        logger.info(
            "Development email written to the local outbox (purpose=%s).",
            message.purpose,
            extra={
                "event": "development_email_written",
                "purpose": message.purpose,
            },
        )


class UnconfiguredEmailSender:
    @property
    def is_available(self) -> bool:
        return False

    def send(self, message: EmailMessage) -> None:
        raise EmailDeliveryError(
            "No email provider is configured for this environment."
        )


def _outbox_directory() -> Path:
    configured = Path(settings.email_outbox_dir)
    if configured.is_absolute():
        return configured
    return Path(__file__).resolve().parents[2] / configured


def get_email_sender() -> EmailSender:
    backend = settings.email_backend.strip().lower()
    if backend == "resend":
        return ResendEmailSender(
            api_key=settings.resend_api_key.get_secret_value(),
            from_address=settings.email_from,
        )
    if settings.environment == "development" and backend == "outbox":
        return OutboxEmailSender(_outbox_directory())
    return UnconfiguredEmailSender()
