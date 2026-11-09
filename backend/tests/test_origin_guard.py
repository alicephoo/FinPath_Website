"""Regression tests for the trusted browser-origin guard."""

from __future__ import annotations

import pytest

from app.api.dependencies import require_trusted_origin
from app.api.errors import ApiError
from app.core.config import settings

TRUSTED_ORIGIN = "https://finpath.example.test"


@pytest.fixture(autouse=True)
def trusted_origin(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(settings, "frontend_origins", TRUSTED_ORIGIN)


def _assert_rejected(**kwargs: object) -> None:
    with pytest.raises(ApiError) as raised:
        require_trusted_origin(**kwargs)
    assert raised.value.status_code == 403
    assert raised.value.code == "origin_not_allowed"


def test_trusted_origin_supplied_is_accepted() -> None:
    require_trusted_origin(
        origin=TRUSTED_ORIGIN,
        sec_fetch_site=None,
    )
    require_trusted_origin(
        origin=TRUSTED_ORIGIN,
        sec_fetch_site="same-origin",
    )
    require_trusted_origin(
        origin=TRUSTED_ORIGIN,
        sec_fetch_site="same-site",
    )


def test_untrusted_origin_supplied_is_rejected() -> None:
    _assert_rejected(
        origin="https://attacker.example.test",
        sec_fetch_site=None,
    )
    _assert_rejected(
        origin="https://attacker.example.test",
        sec_fetch_site="same-origin",
    )


def test_no_origin_with_same_origin_fetch_metadata_is_accepted() -> None:
    require_trusted_origin(
        origin=None,
        sec_fetch_site="same-origin",
    )


def test_no_origin_with_cross_site_fetch_metadata_is_rejected() -> None:
    _assert_rejected(
        origin=None,
        sec_fetch_site="cross-site",
    )


def test_no_origin_with_none_fetch_metadata_preserves_existing_rejection() -> None:
    _assert_rejected(
        origin=None,
        sec_fetch_site="none",
    )


def test_no_origin_and_missing_fetch_metadata_preserves_existing_rejection() -> None:
    _assert_rejected(
        origin=None,
        sec_fetch_site=None,
    )


def test_trusted_origin_with_cross_site_fetch_metadata_is_rejected() -> None:
    _assert_rejected(
        origin=TRUSTED_ORIGIN,
        sec_fetch_site="cross-site",
    )


def test_empty_origin_is_not_treated_as_same_origin() -> None:
    _assert_rejected(
        origin="",
        sec_fetch_site="same-origin",
    )
