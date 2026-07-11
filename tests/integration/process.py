"""Child-process orchestration for isolated end-to-end acceptance runs.

Starts uvicorn and worker subprocesses with fresh environment variables so
``app.config.get_settings`` and worker settings never reuse a contaminated
parent-process cache.
"""

from __future__ import annotations

import logging
import os
import signal
import subprocess
import sys
import time

import httpx

from tests.integration.config import (
    REQUIRED_ENVIRONMENT,
    REQUIRED_INSTANCE_MARKER,
    Wave2E2EConfig,
)

LOGGER = logging.getLogger(__name__)

HEALTH_TIMEOUT_S = 45.0
HEALTH_POLL_INTERVAL_S = 0.5
WORKER_ONCE_TIMEOUT_S = 120.0
API_STARTUP_TIMEOUT_S = 60.0


def build_child_env(config: Wave2E2EConfig) -> dict[str, str]:
    """Build a subprocess environment for API, worker, and bootstrap children.

    Args:
        config: Guarded end-to-end configuration.

    Returns:
        Environment mapping with isolated database, data directory, and fake
        triage controls. Paid Gemini credentials are explicitly removed.
    """
    LOGGER.info("build_child_env called data_dir=%s database_name=%s", config.data_dir, config.database_name)
    env = os.environ.copy()
    env.update(
        {
            "DATABASE_URL": config.database_url,
            "DATA_DIR": str(config.data_dir),
            "APP_ENVIRONMENT": REQUIRED_ENVIRONMENT,
            "INSTANCE_MARKER": REQUIRED_INSTANCE_MARKER,
            "FRONTEND_DIST_DIR": str(config.repo_root / "frontend" / "web" / "dist"),
            "FRONTEND_REQUIRED": "true" if config.require_frontend else "false",
            "GEMINI_API_KEY": "",
            "DECK_ADMIN_API_KEY": "",
            "WORKER_FAKE_GEMINI": "true",
            "WORKER_FAKE_GEMINI_DELAY_SECONDS": "0",
            "WORKER_FAKE_GEMINI_FAILURE_RATE": "0",
            "WORKER_ID": config.worker_id,
            "PYTHONUNBUFFERED": "1",
        }
    )
    env.pop("GEMINI_API_KEY", None)
    LOGGER.info("build_child_env completed keys=%s", sorted(env.keys()))
    return env


def ensure_runtime_directories(config: Wave2E2EConfig) -> None:
    """Create contract runtime directories under the isolated data root.

    Args:
        config: Guarded end-to-end configuration.

    Side effects:
        Creates ``audio``, ``decks``, and ``corpus`` subdirectories.
    """
    LOGGER.info("ensure_runtime_directories called data_dir=%s", config.data_dir)
    for name in ("audio", "decks", "corpus"):
        (config.data_dir / name).mkdir(parents=True, exist_ok=True)
    LOGGER.info("ensure_runtime_directories completed")


def start_api_process(config: Wave2E2EConfig) -> subprocess.Popen[str]:
    """Launch uvicorn for the FastAPI application in a child process.

    Args:
        config: Guarded end-to-end configuration.

    Returns:
        Running ``Popen`` handle for the API server.
    """
    LOGGER.info("start_api_process called api_base_url=%s", config.api_base_url)
    env = build_child_env(config)
    command = [
        sys.executable,
        "-m",
        "uvicorn",
        "app.main:app",
        "--host",
        config.api_host,
        "--port",
        str(config.api_port),
        "--log-level",
        "info",
    ]
    process = subprocess.Popen(
        command,
        cwd=config.repo_root,
        env=env,
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
        text=True,
    )
    LOGGER.info("start_api_process completed pid=%s", process.pid)
    return process


def stop_process(process: subprocess.Popen[str] | None) -> None:
    """Terminate a child process gracefully when still running.

    Args:
        process: Child ``Popen`` handle or ``None``.

    Side effects:
        Sends SIGTERM and waits briefly before SIGKILL when needed.
    """
    LOGGER.info("stop_process called pid=%s", None if process is None else process.pid)
    if process is None:
        return
    if process.poll() is not None:
        return
    process.send_signal(signal.SIGTERM)
    try:
        process.wait(timeout=10)
    except subprocess.TimeoutExpired:
        process.kill()
        process.wait(timeout=5)
    LOGGER.info("stop_process completed returncode=%s", process.returncode)


def wait_for_health(config: Wave2E2EConfig, *, process: subprocess.Popen[str] | None = None) -> dict[str, object]:
    """Poll ``/api/health`` until the API reports database connectivity.

    Args:
        config: Guarded end-to-end configuration.
        process: Optional API process used to fail fast on early exit.

    Returns:
        Parsed health JSON payload.

    Raises:
        RuntimeError: When health never becomes ready or the API exits early.
    """
    LOGGER.info("wait_for_health called health_url=%s", config.health_url)
    deadline = time.monotonic() + HEALTH_TIMEOUT_S
    last_error = "unknown"
    with httpx.Client(timeout=5.0) as client:
        while time.monotonic() < deadline:
            if process is not None and process.poll() is not None:
                output = process.stdout.read() if process.stdout else ""
                raise RuntimeError(
                    f"API process exited before health ready code={process.returncode} "
                    f"output_tail={output[-800:]}"
                )
            try:
                response = client.get(config.health_url)
                if response.status_code == 200:
                    payload = response.json()
                    if payload.get("status") == "ok":
                        LOGGER.info("wait_for_health completed database=%s", payload.get("database"))
                        return payload
                last_error = f"status={response.status_code}"
            except httpx.HTTPError as error:
                last_error = type(error).__name__
            time.sleep(HEALTH_POLL_INTERVAL_S)
    raise RuntimeError(f"API health not ready within timeout; last_error={last_error}")


def run_worker_once(config: Wave2E2EConfig) -> int:
    """Process at most one gauntlet job in an isolated worker subprocess.

    Args:
        config: Guarded end-to-end configuration.

    Returns:
        Child process exit code.

    Raises:
        RuntimeError: When the worker exceeds the configured timeout.
    """
    LOGGER.info("run_worker_once called worker_id=%s", config.worker_id)
    env = build_child_env(config)
    completed = subprocess.run(
        [sys.executable, "-m", "worker", "--once"],
        cwd=config.repo_root,
        env=env,
        capture_output=True,
        text=True,
        timeout=WORKER_ONCE_TIMEOUT_S,
        check=False,
    )
    if completed.returncode != 0:
        LOGGER.warning(
            "run_worker_once non_zero exit_code=%s stderr_tail=%s",
            completed.returncode,
            completed.stderr[-500:],
        )
    else:
        LOGGER.info("run_worker_once completed exit_code=0")
    return completed.returncode


def tail_process_output(process: subprocess.Popen[str], *, max_chars: int = 1200) -> str:
    """Read trailing stdout from a child process without blocking indefinitely.

    Args:
        process: Child process whose stdout is piped.
        max_chars: Maximum characters to retain.

    Returns:
        Trailing stdout text for failure diagnostics.
    """
    LOGGER.info("tail_process_output called pid=%s max_chars=%s", process.pid, max_chars)
    if process.stdout is None:
        return ""
    try:
        remaining = process.stdout.read()
    except Exception:
        remaining = ""
    LOGGER.info("tail_process_output completed chars=%s", len(remaining))
    return remaining[-max_chars:]
