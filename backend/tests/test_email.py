"""Unit tests for the development outbox and production Resend sender."""

from __future__ import annotations

import io
import json
import urllib.error

import pytest
from pydantic import SecretStr, ValidationError

from app.core.config import Settings, settings
from app.services.email import (
    EmailDeliveryError,
    EmailMessage,
    OutboxEmailSender,
    ResendEmailSender,
    UnconfiguredEmailSender,
    get_email_sender,
)

MESSAGE = EmailMessage(
    to="user@example.test",
    subject="Verify your FinPath email address",
    text="Use this single-use token to verify your FinPath email: token-123",
    token="token-123",
    purpose="email_verification",
)


class FakeResponse:
    def __init__(self, status: int = 200, body: bytes = b'{"id":"email-1"}') -> None:
        self.status = status
        self._body = body

    def __enter__(self) -> "FakeResponse":
        return self

    def __exit__(self, *args: object) -> bool:
        return False

    def read(self) -> bytes:
        return self._body


def _headers(request: object) -> dict[str, str]:
    return {
        key.lower(): value
        for key, value in request.header_items()  # type: ignore[attr-defined]
    }


def test_resend_sender_posts_expected_request(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    captured: dict[str, object] = {}

    def fake_urlopen(request, timeout):
        captured["request"] = request
        captured["timeout"] = timeout
        return FakeResponse()

    monkeypatch.setattr(
        "app.services.email.urllib.request.urlopen",
        fake_urlopen,
    )
    sender = ResendEmailSender(
        api_key="re_test_key",
        from_address="FinPath <no-reply@example.test>",
    )

    sender.send(MESSAGE)

    request = captured["request"]
    assert request.full_url == "https://api.resend.com/emails"  # type: ignore[attr-defined]
    assert request.get_method() == "POST"  # type: ignore[attr-defined]
    headers = _headers(request)
    assert headers["authorization"] == "Bearer re_test_key"
    assert headers["content-type"] == "application/json"
    assert headers["accept"] == "application/json"
    payload = json.loads(request.data.decode("utf-8"))  # type: ignore[attr-defined]
    assert payload == {
        "from": "FinPath <no-reply@example.test>",
        "to": ["user@example.test"],
        "subject": "Verify your FinPath email address",
        "text": (
            "Use this single-use token to verify your FinPath email: "
            "token-123"
        ),
    }
    assert captured["timeout"] == 10.0


def test_resend_sender_reports_provider_rejection(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    def fake_urlopen(request, timeout):
        raise urllib.error.HTTPError(
            request.full_url,
            422,
            "Unprocessable Entity",
            hdrs=None,
            fp=io.BytesIO(b'{"message":"invalid"}'),
        )

    monkeypatch.setattr(
        "app.services.email.urllib.request.urlopen",
        fake_urlopen,
    )
    sender = ResendEmailSender(
        api_key="re_test_key",
        from_address="no-reply@example.test",
    )

    with pytest.raises(EmailDeliveryError, match="rejected"):
        sender.send(MESSAGE)


def test_resend_sender_reports_network_and_timeout_failures(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    sender = ResendEmailSender(
        api_key="re_test_key",
        from_address="no-reply@example.test",
    )

    def network_failure(request, timeout):
        raise urllib.error.URLError("network unavailable")

    monkeypatch.setattr(
        "app.services.email.urllib.request.urlopen",
        network_failure,
    )
    with pytest.raises(EmailDeliveryError, match="failed"):
        sender.send(MESSAGE)

    def timeout_failure(request, timeout):
        raise TimeoutError("timed out")

    monkeypatch.setattr(
        "app.services.email.urllib.request.urlopen",
        timeout_failure,
    )
    with pytest.raises(EmailDeliveryError, match="failed"):
        sender.send(MESSAGE)


def test_resend_sender_rejects_missing_constructor_configuration() -> None:
    with pytest.raises(EmailDeliveryError, match="API key"):
        ResendEmailSender(api_key="", from_address="no-reply@example.test")
    with pytest.raises(EmailDeliveryError, match="sender address"):
        ResendEmailSender(api_key="re_test_key", from_address="")


def test_resend_settings_require_key_and_sender() -> None:
    with pytest.raises(ValidationError, match="FINPATH_RESEND_API_KEY"):
        Settings(
            _env_file=None,
            environment="production",
            email_backend="resend",
            resend_api_key=SecretStr(""),
            email_from="no-reply@example.test",
        )
    with pytest.raises(ValidationError, match="FINPATH_EMAIL_FROM"):
        Settings(
            _env_file=None,
            environment="production",
            email_backend="resend",
            resend_api_key=SecretStr("re_test_key"),
            email_from="",
        )


def test_email_backend_must_be_supported() -> None:
    with pytest.raises(ValidationError, match="must be 'outbox' or 'resend'"):
        Settings(_env_file=None, environment="development", email_backend="smtp")


def test_production_verification_requires_resend() -> None:
    with pytest.raises(
        ValidationError,
        match="Production email verification requires",
    ):
        Settings(
            _env_file=None,
            environment="production",
            require_email_verification=True,
            email_backend="outbox",
        )


def test_valid_resend_configuration_is_accepted_and_secret_is_hidden() -> None:
    configured = Settings(
        _env_file=None,
        environment="production",
        require_email_verification=True,
        email_backend="resend",
        resend_api_key=SecretStr("re_secret_value"),
        email_from="no-reply@example.test",
        frontend_origins="https://finpath.example.test",
    )

    assert configured.email_backend == "resend"
    assert configured.email_from == "no-reply@example.test"
    assert "re_secret_value" not in repr(configured)


def test_get_email_sender_selects_backend(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(settings, "environment", "development")
    monkeypatch.setattr(settings, "email_backend", "outbox")
    assert isinstance(get_email_sender(), OutboxEmailSender)
    assert get_email_sender().is_available is True

    monkeypatch.setattr(settings, "environment", "production")
    monkeypatch.setattr(settings, "email_backend", "resend")
    monkeypatch.setattr(settings, "resend_api_key", SecretStr("re_test_key"))
    monkeypatch.setattr(settings, "email_from", "no-reply@example.test")
    assert isinstance(get_email_sender(), ResendEmailSender)
    assert get_email_sender().is_available is True


def test_production_outbox_is_unconfigured_and_not_available(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(settings, "environment", "production")
    monkeypatch.setattr(settings, "email_backend", "outbox")

    sender = get_email_sender()

    assert isinstance(sender, UnconfiguredEmailSender)
    assert sender.is_available is False
    with pytest.raises(EmailDeliveryError, match="No email provider"):
        sender.send(MESSAGE)


def test_email_outbox_is_not_exposed_by_a_public_api_route() -> None:
    from app.main import app

    paths = {getattr(route, "path", "") for route in app.routes}

    assert not any("email_outbox" in path for path in paths)
    assert not any(path.startswith("/var/") for path in paths)


def test_outbox_sender_behavior_is_unchanged(tmp_path) -> None:
    sender = OutboxEmailSender(tmp_path)
    sender.send(MESSAGE)

    files = list(tmp_path.glob("*.json"))
    assert len(files) == 1
    payload = json.loads(files[0].read_text(encoding="utf-8"))
    assert payload["to"] == "user@example.test"
    assert payload["subject"] == "Verify your FinPath email address"
    assert payload["token"] == "token-123"
    assert payload["purpose"] == "email_verification"
