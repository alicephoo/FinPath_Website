"""Startup-script tests that never touch PostgreSQL or Docker."""

from __future__ import annotations

import subprocess
from pathlib import Path

import pytest

ENTRYPOINT = Path(__file__).resolve().parents[1] / "docker" / "entrypoint.sh"


def _write_stub(bin_dir: Path, name: str) -> None:
    stub = bin_dir / name
    stub.write_text(
        "#!/bin/sh\n"
        f"printf '%s %s\\n' '{name}' \"$*\" >> \"$FINPATH_TEST_LOG\"\n",
        encoding="utf-8",
    )
    stub.chmod(0o755)


def _run_entrypoint(
    tmp_path: Path,
    **overrides: str,
) -> tuple[subprocess.CompletedProcess[str], list[str]]:
    bin_dir = tmp_path / "bin"
    bin_dir.mkdir()
    for name in ("alembic", "python", "uvicorn"):
        _write_stub(bin_dir, name)

    command_log = tmp_path / "commands.log"
    environment = {
        "PATH": f"{bin_dir}:/usr/bin:/bin",
        "FINPATH_TEST_LOG": str(command_log),
    }
    environment.update(overrides)

    result = subprocess.run(
        ["sh", str(ENTRYPOINT)],
        env=environment,
        capture_output=True,
        text=True,
        check=False,
    )
    commands = (
        command_log.read_text(encoding="utf-8").splitlines()
        if command_log.exists()
        else []
    )
    return result, commands


def test_default_startup_runs_migrations_and_seed(tmp_path: Path) -> None:
    result, commands = _run_entrypoint(
        tmp_path,
        FINPATH_ENVIRONMENT="development",
        PORT="10000",
    )

    assert result.returncode == 0
    assert commands == [
        "alembic upgrade head",
        "python -m app.db.seed",
        "uvicorn app.main:app --host 0.0.0.0 --port 10000 --proxy-headers",
    ]
    assert "Applying FinPath database migrations" in result.stdout
    assert "Seeding FinPath reference data" in result.stdout


@pytest.mark.parametrize("value", ["false", "FALSE", "0", "no", "off"])
def test_render_mode_skips_migrations_and_seed(
    tmp_path: Path,
    value: str,
) -> None:
    result, commands = _run_entrypoint(
        tmp_path,
        FINPATH_ENVIRONMENT="development",
        FINPATH_RUN_STARTUP_MIGRATIONS=value,
        PORT="10000",
    )

    assert result.returncode == 0
    assert commands == [
        "uvicorn app.main:app --host 0.0.0.0 --port 10000 --proxy-headers",
    ]
    assert "Skipping startup migrations" in result.stdout


def test_startup_uses_default_port_when_port_is_unset(tmp_path: Path) -> None:
    result, commands = _run_entrypoint(
        tmp_path,
        FINPATH_ENVIRONMENT="development",
        FINPATH_RUN_STARTUP_MIGRATIONS="false",
    )

    assert result.returncode == 0
    assert commands[-1].endswith("--port 8000 --proxy-headers")


def test_invalid_startup_flag_fails_without_starting_uvicorn(
    tmp_path: Path,
) -> None:
    result, commands = _run_entrypoint(
        tmp_path,
        FINPATH_ENVIRONMENT="development",
        FINPATH_RUN_STARTUP_MIGRATIONS="sometimes",
    )

    assert result.returncode != 0
    assert commands == []
    assert "Invalid FINPATH_RUN_STARTUP_MIGRATIONS" in result.stderr


@pytest.mark.parametrize("environment", ["production", "PRODUCTION", "prod"])
def test_production_requires_access_token_secret_before_any_command(
    tmp_path: Path,
    environment: str,
) -> None:
    result, commands = _run_entrypoint(
        tmp_path,
        FINPATH_ENVIRONMENT=environment,
        FINPATH_RUN_STARTUP_MIGRATIONS="false",
    )

    assert result.returncode != 0
    assert commands == []
    assert "FINPATH_ACCESS_TOKEN_SECRET is required" in result.stderr


def test_production_rejects_blank_access_token_secret(tmp_path: Path) -> None:
    result, commands = _run_entrypoint(
        tmp_path,
        FINPATH_ENVIRONMENT="production",
        FINPATH_ACCESS_TOKEN_SECRET="   ",
        FINPATH_RUN_STARTUP_MIGRATIONS="false",
    )

    assert result.returncode != 0
    assert commands == []
    assert "FINPATH_ACCESS_TOKEN_SECRET is required" in result.stderr


def test_production_with_secret_starts_without_migrations(tmp_path: Path) -> None:
    result, commands = _run_entrypoint(
        tmp_path,
        FINPATH_ENVIRONMENT="production",
        FINPATH_ACCESS_TOKEN_SECRET="test-only-secret",
        FINPATH_RUN_STARTUP_MIGRATIONS="false",
        PORT="10000",
    )

    assert result.returncode == 0
    assert commands == [
        "uvicorn app.main:app --host 0.0.0.0 --port 10000 --proxy-headers",
    ]


def test_development_does_not_require_access_token_secret(tmp_path: Path) -> None:
    result, commands = _run_entrypoint(
        tmp_path,
        FINPATH_ENVIRONMENT="development",
        FINPATH_RUN_STARTUP_MIGRATIONS="false",
        PORT="19000",
    )

    assert result.returncode == 0
    assert commands[-1] == (
        "uvicorn app.main:app --host 0.0.0.0 --port 19000 --proxy-headers"
    )
