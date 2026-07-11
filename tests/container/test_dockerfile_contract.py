"""Static contract tests for the shared production container artifact.

Validates multi-stage build requirements, runtime layout, security exclusions,
and alternate entry commands without assuming a Docker daemon is installed.
"""

from __future__ import annotations

import logging
import re
import shutil
import subprocess
from pathlib import Path

import pytest

logger = logging.getLogger(__name__)

REPO_ROOT = Path(__file__).resolve().parents[2]
DOCKERFILE = REPO_ROOT / "Dockerfile"
DOCKERIGNORE = REPO_ROOT / ".dockerignore"

FRONTEND_DIST = Path("frontend/web/dist")
RUNTIME_PYTHON_PATHS = (
    Path("app"),
    Path("worker"),
    Path("contracts"),
    Path("scripts"),
    Path("deckgen"),
    Path("pyproject.toml"),
    Path("uv.lock"),
)
EXCLUDED_CONTEXT_PATHS = (
    Path(".env"),
    Path("data/audio/sample.webm"),
    Path("tests/container/test_dockerfile_contract.py"),
    Path(".venv/bin/python"),
    Path("frontend/web/node_modules/react/package.json"),
    Path("tune/pyproject.toml"),
)
ALTERNATE_COMMANDS = (
    "python -m worker",
    "python -m worker --dry-run",
    "python -m scripts.apply_migrations --dry-run",
    "python -m scripts.apply_schema --dry-run",
    "python -m scripts.ops.cli restart-plan --dry-run",
)


def _read_repo_file(path: Path) -> str:
    """Return UTF-8 text for a repository file used by container contract tests.

    Args:
        path: Absolute or repo-root-relative file path.

    Returns:
        The file contents as a string.

    Raises:
        FileNotFoundError: If ``path`` does not exist.
    """
    logger.info("_read_repo_file called path=%s", path)
    return path.read_text(encoding="utf-8")


def _parse_dockerignore_patterns(text: str) -> list[str]:
    """Parse non-comment ``.dockerignore`` lines into normalized patterns.

    Args:
        text: Raw ``.dockerignore`` file contents.

    Returns:
        Ignore patterns with trailing slashes preserved and blank lines removed.
    """
    logger.info("_parse_dockerignore_patterns called line_count=%s", len(text.splitlines()))
    patterns: list[str] = []
    for raw_line in text.splitlines():
        line = raw_line.strip()
        if not line or line.startswith("#"):
            continue
        patterns.append(line)
    return patterns


def _dockerignore_excludes(patterns: list[str], relative_path: Path) -> bool:
    """Return whether a repo-relative path would be excluded from build context.

    Args:
        patterns: Parsed ``.dockerignore`` patterns in file order.
        relative_path: Path relative to the repository root.

    Returns:
        True when the final applicable pattern excludes ``relative_path``.
    """
    logger.info(
        "_dockerignore_excludes called relative_path=%s pattern_count=%s",
        relative_path,
        len(patterns),
    )
    posix = relative_path.as_posix()
    excluded = False
    for pattern in patterns:
        if pattern.startswith("!"):
            negated = pattern[1:]
            if _pattern_matches(negated, posix):
                excluded = False
            continue
        if _pattern_matches(pattern, posix):
            excluded = True
    return excluded


def _pattern_matches(pattern: str, posix_path: str) -> bool:
    """Match a single ``.dockerignore`` pattern against a POSIX path.

    Args:
        pattern: One ignore pattern from ``.dockerignore``.
        posix_path: Candidate path using forward slashes.

    Returns:
        True when ``pattern`` excludes ``posix_path``.
    """
    normalized = pattern.rstrip("/")
    if pattern.endswith("/"):
        return posix_path == normalized or posix_path.startswith(f"{normalized}/")
    if "*" in pattern or "?" in pattern or "[" in pattern:
        return Path(posix_path).match(normalized) is not False
    return posix_path == normalized or posix_path.startswith(f"{normalized}/")


def test_dockerfile_is_multistage_with_frontend_and_python() -> None:
    """Require Node and Python stages with locked frontend and dependency sync."""
    logger.info("test_dockerfile_is_multistage_with_frontend_and_python called")
    content = _read_repo_file(DOCKERFILE).lower()
    assert "as frontend-builder" in content
    assert "node:22" in content or "node:${node_version}" in content
    assert "npm ci" in content
    assert "test:contract" in content
    assert "npm run build" in content
    assert "python:3.12" in content or "python:${python_version}" in content
    assert "uv sync" in content
    assert "--frozen" in content
    assert "--no-dev" in content
    logger.info("test_dockerfile_is_multistage_with_frontend_and_python completed")


def test_dockerfile_installs_runtime_os_tools() -> None:
    """Install ffmpeg and PostgreSQL client tools for media and ops compatibility."""
    logger.info("test_dockerfile_installs_runtime_os_tools called")
    content = _read_repo_file(DOCKERFILE).lower()
    assert "ffmpeg" in content
    assert "postgresql-client" in content
    assert "ghcr.io/astral-sh/uv" in content
    logger.info("test_dockerfile_installs_runtime_os_tools completed")


def test_dockerfile_copies_runtime_modules_and_frontend_dist() -> None:
    """Copy Python runtime packages and the built Vite dist to the contract path."""
    logger.info("test_dockerfile_copies_runtime_modules_and_frontend_dist called")
    content = _read_repo_file(DOCKERFILE)
    for segment in ("app/", "worker/", "contracts/", "scripts/", "deckgen/"):
        assert segment in content
    assert "frontend/web/dist" in content
    assert "pyproject.toml" in content
    assert "uv.lock" in content
    logger.info("test_dockerfile_copies_runtime_modules_and_frontend_dist completed")


def test_dockerfile_runtime_hardening_and_default_cmd() -> None:
    """Use non-root runtime, writable data root, labels, and API default command."""
    logger.info("test_dockerfile_runtime_hardening_and_default_cmd called")
    content = _read_repo_file(DOCKERFILE)
    assert "PYTHONUNBUFFERED=1" in content
    assert "PYTHONDONTWRITEBYTECODE=1" in content
    assert re.search(r"^USER\s+neva\b", content, flags=re.MULTILINE)
    assert "/app/data" in content
    assert "org.opencontainers.image.title" in content
    assert "uvicorn" in content
    assert "0.0.0.0" in content
    assert "8000" in content
    assert "ENTRYPOINT" not in content
    logger.info("test_dockerfile_runtime_hardening_and_default_cmd completed")


def test_dockerfile_documents_alternate_commands() -> None:
    """Keep CMD-only entry so worker, migrations, and ops CLIs can override command."""
    logger.info("test_dockerfile_documents_alternate_commands called")
    content = _read_repo_file(DOCKERFILE)
    for command in ALTERNATE_COMMANDS:
        module = command.split()[2]
        root = module.split(".")[0]
        assert root in content
    assert "CMD [" in content
    logger.info("test_dockerfile_documents_alternate_commands completed")


def test_dockerignore_excludes_secrets_and_dev_artifacts() -> None:
    """Exclude secrets, runtime data, tests, caches, and node_modules from context."""
    logger.info("test_dockerignore_excludes_secrets_and_dev_artifacts called")
    patterns = _parse_dockerignore_patterns(_read_repo_file(DOCKERIGNORE))
    for relative_path in EXCLUDED_CONTEXT_PATHS:
        assert _dockerignore_excludes(patterns, relative_path), relative_path
    logger.info("test_dockerignore_excludes_secrets_and_dev_artifacts completed")


def test_dockerignore_keeps_runtime_build_inputs() -> None:
    """Keep Python runtime modules and frontend sources available to the build."""
    logger.info("test_dockerignore_keeps_runtime_build_inputs called")
    patterns = _parse_dockerignore_patterns(_read_repo_file(DOCKERIGNORE))
    for relative_path in RUNTIME_PYTHON_PATHS:
        assert not _dockerignore_excludes(patterns, relative_path), relative_path
    assert not _dockerignore_excludes(patterns, Path("frontend/web/package.json"))
    assert not _dockerignore_excludes(patterns, Path("frontend/web/src/App.jsx"))
    logger.info("test_dockerignore_keeps_runtime_build_inputs completed")


def test_frontend_dist_matches_app_config() -> None:
    """Ensure the image copies Vite output to the backend default dist directory."""
    logger.info("test_frontend_dist_matches_app_config called")
    from app.config import Settings

    settings = Settings()
    assert settings.frontend_dist_dir.as_posix() == FRONTEND_DIST.as_posix()
    dockerfile = _read_repo_file(DOCKERFILE)
    assert FRONTEND_DIST.as_posix() in dockerfile
    logger.info("test_frontend_dist_matches_app_config completed")


@pytest.mark.skipif(shutil.which("docker") is None, reason="docker CLI not available")
def test_docker_build_succeeds_when_daemon_available() -> None:
    """Build the production image when Docker is installed and reachable."""
    logger.info("test_docker_build_succeeds_when_daemon_available called")
    probe = subprocess.run(
        ["docker", "info"],
        capture_output=True,
        text=True,
        check=False,
        timeout=30,
    )
    if probe.returncode != 0:
        pytest.skip("docker daemon not reachable")

    result = subprocess.run(
        [
            "docker",
            "build",
            "-t",
            "dialect-data-factory:test",
            "--build-arg",
            "BUILD_DATE=1970-01-01T00:00:00Z",
            "--build-arg",
            "VCS_REF=local-test",
            str(REPO_ROOT),
        ],
        capture_output=True,
        text=True,
        check=False,
        timeout=900,
    )
    assert result.returncode == 0, result.stderr[-4000:]
    logger.info("test_docker_build_succeeds_when_daemon_available completed")
