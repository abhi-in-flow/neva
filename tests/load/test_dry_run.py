"""Dry-run and config-check tests proving zero external I/O."""

from __future__ import annotations

import json
import logging
from pathlib import Path

import pytest

from tools.load.config import REQUIRED_MARKER_VALUE, load_config
from tools.load.runner import main, run_config_check, run_dry_run, run_with_hooks
from tools.load.transport import RecordingTransport

LOGGER = logging.getLogger(__name__)


def _safe_argv(repo_root: Path, data_dir: Path, *, extra: list[str] | None = None) -> list[str]:
    """Build argv with safe isolated defaults.

    Args:
        repo_root: Repository root for guard comparison.
        data_dir: Isolated data directory.
        extra: Optional extra CLI flags.

    Returns:
        argv list suitable for ``load_config`` / ``main``.
    """
    base = [
        "--target-url",
        "http://127.0.0.1:8000",
        "--marker",
        REQUIRED_MARKER_VALUE,
        "--database-name",
        "dialect_factory_load_test",
        "--data-dir",
        str(data_dir),
        "--repo-root",
        str(repo_root),
    ]
    if extra:
        base.extend(extra)
    return base


def test_dry_run_performs_zero_network_calls(
    repo_root: Path,
    isolated_data_dir: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Dry-run validates guards and never opens sockets."""
    LOGGER.info("test_dry_run_performs_zero_network_calls called")

    def _forbidden(*_args, **_kwargs):
        raise AssertionError("external I/O must not occur in dry-run")

    monkeypatch.setattr("urllib.request.urlopen", _forbidden)
    monkeypatch.setattr("asyncpg.connect", _forbidden)
    monkeypatch.setattr("pathlib.Path.read_text", _forbidden)
    config = load_config(_safe_argv(repo_root, isolated_data_dir, extra=["--dry-run"]))
    transport = RecordingTransport()
    report = run_dry_run(config, transport=transport)
    assert report["mode"] == "dry-run"
    assert report["http_calls"] == 0
    assert len(transport.calls) == 0


def test_config_check_performs_zero_io(
    repo_root: Path,
    isolated_data_dir: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Config-check exits before HTTP/DB/filesystem traffic."""
    LOGGER.info("test_config_check_performs_zero_io called")

    def _forbidden_open(*_args, **_kwargs):
        raise AssertionError("filesystem I/O must not occur in config-check")

    monkeypatch.setattr("builtins.open", _forbidden_open)
    config = load_config(_safe_argv(repo_root, isolated_data_dir, extra=["--config-check"]))
    report = run_config_check(config)
    assert report["mode"] == "config-check"
    assert report["status"] == "ok"


def test_unsafe_target_exits_nonzero_via_cli(
    repo_root: Path,
    isolated_data_dir: Path,
    capsys: pytest.CaptureFixture[str],
) -> None:
    """CLI returns exit code 2 for public targets."""
    LOGGER.info("test_unsafe_target_exits_nonzero_via_cli called")
    argv = _safe_argv(
        repo_root,
        isolated_data_dir,
        extra=["--dry-run", "--target-url", "https://example.com"],
    )
    code = main(argv)
    captured = capsys.readouterr()
    payload = json.loads(captured.out)
    assert code == 2
    assert payload["code"] == "target_not_private"


def test_main_dry_run_ok_prints_plan(
    repo_root: Path,
    isolated_data_dir: Path,
    capsys: pytest.CaptureFixture[str],
) -> None:
    """CLI dry-run prints JSON plan on success."""
    LOGGER.info("test_main_dry_run_ok_prints_plan called")
    code = main(_safe_argv(repo_root, isolated_data_dir, extra=["--dry-run"]))
    captured = capsys.readouterr()
    payload = json.loads(captured.out)
    assert code == 0
    assert payload["plan"]["expected_poll_requests"] == 200 * 15


def test_run_with_hooks_uses_recording_transport(
    safe_config,
) -> None:
    """Injectable runner uses recording transport during dry-run."""
    LOGGER.info("test_run_with_hooks_uses_recording_transport called")
    report = run_with_hooks(
        safe_config,
        transport_builder=RecordingTransport,
    )
    assert report["mode"] == "dry-run"
    worker_plan = report["plan"]["worker_plan"]
    assert worker_plan["attestation_required"] is True
    assert worker_plan["injected_by_harness"] is False
