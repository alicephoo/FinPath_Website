"""Static checks for the backend Docker build context and runtime permissions."""

from __future__ import annotations

from pathlib import Path

BACKEND_DIR = Path(__file__).resolve().parents[1]


def _dockerignore_lines() -> set[str]:
    return {
        line.strip()
        for line in (BACKEND_DIR / ".dockerignore").read_text(
            encoding="utf-8"
        ).splitlines()
        if line.strip() and not line.lstrip().startswith("#")
    }


def _dockerfile_lines() -> list[str]:
    return [
        line.strip()
        for line in (BACKEND_DIR / "Dockerfile").read_text(
            encoding="utf-8"
        ).splitlines()
    ]


def test_dockerignore_excludes_nested_caches_and_sensitive_files() -> None:
    patterns = _dockerignore_lines()

    required_patterns = {
        "**/__pycache__/",
        "**/*.py[cod]",
        "**/.pytest_cache/",
        "**/.mypy_cache/",
        "**/.ruff_cache/",
        "**/.coverage",
        "**/htmlcov/",
        ".env",
        "**/.env",
        "**/.env.*",
        ".venv",
        "**/.venv/",
        "tests",
        "var",
        ".git",
        "**/*.pem",
        "**/*.key",
        "**/*.crt",
        "**/*.sqlite",
        "**/*.db",
        "**/*.dump",
        "**/*.bak",
        "**/*.log",
    }

    assert required_patterns <= patterns
    assert "**/*.py" not in patterns
    assert "app" not in patterns
    assert "migrations" not in patterns
    assert "docker" not in patterns


def test_required_backend_files_remain_in_the_build_context() -> None:
    for relative_path in (
        "pyproject.toml",
        "alembic.ini",
        "app/main.py",
        "app/core/config.py",
        "migrations/env.py",
        "migrations/script.py.mako",
        "migrations/versions/7270e464aca9_initial_schema.py",
        "docker/entrypoint.sh",
    ):
        assert (BACKEND_DIR / relative_path).is_file()


def test_dockerfile_copies_only_explicit_paths_and_runs_non_root() -> None:
    lines = _dockerfile_lines()
    content = "\n".join(lines)

    assert "COPY pyproject.toml alembic.ini ./" in lines
    assert "COPY app ./app" in lines
    assert "COPY migrations ./migrations" in lines
    assert "COPY docker/entrypoint.sh /usr/local/bin/finpath-entrypoint.sh" in lines
    assert not any(line.startswith("COPY . ") for line in lines)
    assert not any(line.startswith("ADD ") for line in lines)
    assert "USER finpath" in lines
    assert "PYTHONDONTWRITEBYTECODE=1" in content
    assert "EXPOSE 8000" in lines
    assert "ENTRYPOINT [\"/usr/local/bin/finpath-entrypoint.sh\"]" in lines
    assert not any(line.startswith("ARG ") for line in lines)
    assert "FINPATH_" not in content


def test_runtime_user_can_write_only_the_email_outbox() -> None:
    lines = _dockerfile_lines()
    content = "\n".join(lines)

    assert "mkdir -p /app/var/email_outbox" in content
    assert "chown -R finpath:finpath /app/var/email_outbox" in content
    assert "chmod 0700 /app/var/email_outbox" in content
    assert "chown -R finpath:finpath /app" not in lines
