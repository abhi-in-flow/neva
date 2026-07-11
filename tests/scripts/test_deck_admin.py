"""Test the deck operator CLI without opening network connections.

The suite injects a recording transport to verify methods, URLs, JSON bodies,
and protected headers. It also exercises contract validation, redacted dry-run
output, and safe nonzero error handling through the public CLI entrypoint.
"""

from __future__ import annotations

import json
import logging
from pathlib import Path
from urllib.request import Request
from uuid import UUID

import pytest

from scripts.deck_admin import HttpResponse, main

LOGGER = logging.getLogger(__name__)
DECK_ID = UUID("6c78aa60-9d93-460f-a85c-55cf9b34f7a2")
SECRET_KEY = "operator-secret-that-must-not-leak"


def valid_payload() -> dict[str, object]:
    """Return a minimal generation payload accepted by the frozen contract."""
    LOGGER.info("valid_payload called")
    concepts = [
        {
            "concept_id": f"concept-{index}",
            "label_en": f"Concept {index}",
            "locale": "Assam",
            "cultural_hint": f"Everyday Assamese object {index}",
        }
        for index in range(6)
    ]
    return {"region_tag": "assam", "concepts": concepts}


def write_payload(tmp_path: Path, payload: object | None = None) -> Path:
    """Write an isolated generation fixture and return its path.

    Args:
        tmp_path: Pytest-provided isolated directory.
        payload: Optional JSON value; defaults to a valid request.

    Returns:
        Path to the generated JSON fixture.
    """
    LOGGER.info("write_payload called temp_name=%s", tmp_path.name)
    path = tmp_path / "concepts.json"
    path.write_text(
        json.dumps(valid_payload() if payload is None else payload),
        encoding="utf-8",
    )
    return path


class RecordingTransport:
    """Capture prepared urllib requests and return deterministic JSON."""

    def __init__(self, response: object | None = None, *, status: int = 200) -> None:
        """Configure a transport response without retaining any credentials.

        Args:
            response: JSON value returned as the response body.
            status: Synthetic HTTP status.
        """
        LOGGER.info("RecordingTransport.__init__ called status=%s", status)
        self.response = {} if response is None else response
        self.status = status
        self.calls: list[tuple[Request, float]] = []

    def __call__(self, request: Request, timeout: float) -> HttpResponse:
        """Record one request and return the configured response.

        Args:
            request: Prepared request under test.
            timeout: Configured request timeout.

        Returns:
            Synthetic JSON HTTP response.
        """
        LOGGER.info(
            "RecordingTransport.__call__ called method=%s url=%s timeout=%s",
            request.get_method(),
            request.full_url,
            timeout,
        )
        self.calls.append((request, timeout))
        return HttpResponse(self.status, json.dumps(self.response).encode("utf-8"))


def request_headers(request: Request) -> dict[str, str]:
    """Return request headers with lowercase names for stable assertions.

    Args:
        request: Prepared urllib request captured by the fake transport.

    Returns:
        Lowercase header mapping.
    """
    LOGGER.info("request_headers called header_count=%s", len(request.header_items()))
    return {name.lower(): value for name, value in request.header_items()}


def test_generate_validates_payload_and_sends_protected_header(
    tmp_path: Path,
    capsys: pytest.CaptureFixture[str],
    caplog: pytest.LogCaptureFixture,
) -> None:
    """Validate generation input and send its exact contract payload."""
    LOGGER.info("test_generate_validates_payload_and_sends_protected_header called")
    fixture = write_payload(tmp_path)
    transport = RecordingTransport({"deck_id": str(DECK_ID), "status": "generating"})

    with caplog.at_level(logging.INFO):
        exit_code = main(
            [
                "--base-url",
                "http://deck-host:9000/",
                "--api-key",
                SECRET_KEY,
                "generate",
                str(fixture),
            ],
            transport=transport,
        )

    assert exit_code == 0
    assert len(transport.calls) == 1
    request, _ = transport.calls[0]
    assert request.get_method() == "POST"
    assert request.full_url == "http://deck-host:9000/api/admin/decks"
    assert json.loads(request.data or b"{}") == valid_payload()
    assert request_headers(request)["x-deck-admin-key"] == SECRET_KEY
    assert request_headers(request)["content-type"] == "application/json"
    captured = capsys.readouterr()
    assert SECRET_KEY not in captured.out
    assert SECRET_KEY not in captured.err
    assert SECRET_KEY not in caplog.text


@pytest.mark.parametrize(
    ("arguments", "expected_method", "expected_url"),
    [
        (["list"], "GET", "http://localhost:8080/api/admin/decks"),
        (
            ["show", str(DECK_ID)],
            "GET",
            f"http://localhost:8080/api/admin/decks/{DECK_ID}",
        ),
        (
            ["activate", str(DECK_ID)],
            "POST",
            f"http://localhost:8080/api/admin/decks/{DECK_ID}/activate",
        ),
    ],
)
def test_commands_use_contract_methods_and_urls(
    arguments: list[str],
    expected_method: str,
    expected_url: str,
) -> None:
    """Route list, show, and activate to their frozen API endpoints."""
    LOGGER.info("test_commands_use_contract_methods_and_urls called arguments=%s", arguments)
    transport = RecordingTransport()

    exit_code = main(
        ["--base-url", "http://localhost:8080", "--api-key", SECRET_KEY, *arguments],
        transport=transport,
    )

    assert exit_code == 0
    request, _ = transport.calls[0]
    assert request.get_method() == expected_method
    assert request.full_url == expected_url
    assert request_headers(request)["x-deck-admin-key"] == SECRET_KEY


@pytest.mark.parametrize(
    "arguments",
    [
        ["generate", "INPUT", "--dry-run"],
        ["activate", str(DECK_ID), "--dry-run"],
    ],
)
def test_mutating_dry_runs_are_redacted_and_never_use_transport(
    arguments: list[str],
    tmp_path: Path,
    capsys: pytest.CaptureFixture[str],
) -> None:
    """Print redacted mutation details without requiring a key or HTTP."""
    LOGGER.info("test_mutating_dry_runs_are_redacted_and_never_use_transport called")
    fixture = write_payload(tmp_path)
    resolved_arguments = [str(fixture) if value == "INPUT" else value for value in arguments]

    def forbidden_transport(_request: Request, _timeout: float) -> HttpResponse:
        """Fail if dry-run dispatch attempts any network transport."""
        raise AssertionError("dry-run must not invoke transport")

    exit_code = main(resolved_arguments, transport=forbidden_transport)

    assert exit_code == 0
    output = json.loads(capsys.readouterr().out)
    assert output["dry_run"] is True
    assert output["headers"]["X-Deck-Admin-Key"] == "[REDACTED]"
    assert SECRET_KEY not in json.dumps(output)
    if arguments[0] == "generate":
        assert output["payload"] == valid_payload()
    else:
        assert output["payload"] is None


@pytest.mark.parametrize(
    ("payload", "message"),
    [
        ({"region_tag": "assam", "concepts": []}, "validation"),
        (
            {
                **valid_payload(),
                "concepts": [
                    valid_payload()["concepts"][0],
                    valid_payload()["concepts"][0],
                    *valid_payload()["concepts"][2:],
                ],
            },
            "unique",
        ),
    ],
)
def test_generate_validation_fails_before_transport(
    payload: object,
    message: str,
    tmp_path: Path,
    capsys: pytest.CaptureFixture[str],
) -> None:
    """Return a usage error for invalid input before any HTTP call."""
    LOGGER.info("test_generate_validation_fails_before_transport called message=%s", message)
    fixture = write_payload(tmp_path, payload)

    def forbidden_transport(_request: Request, _timeout: float) -> HttpResponse:
        """Fail if invalid generation data reaches the transport."""
        raise AssertionError("invalid input must not invoke transport")

    exit_code = main(
        ["--api-key", SECRET_KEY, "generate", str(fixture)],
        transport=forbidden_transport,
    )

    assert exit_code == 2
    assert message in capsys.readouterr().err.lower()


def test_missing_key_and_http_failure_return_safe_nonzero_errors(
    capsys: pytest.CaptureFixture[str],
) -> None:
    """Distinguish missing configuration from a server request failure."""
    LOGGER.info("test_missing_key_and_http_failure_return_safe_nonzero_errors called")
    no_key_transport = RecordingTransport()

    assert main(["list"], transport=no_key_transport) == 2
    assert not no_key_transport.calls
    missing_key_error = capsys.readouterr().err
    assert "DECK_ADMIN_API_KEY" in missing_key_error

    failing_transport = RecordingTransport({"detail": "deck is not ready"}, status=409)
    assert main(["--api-key", SECRET_KEY, "activate", str(DECK_ID)], transport=failing_transport) == 1
    failure_error = capsys.readouterr().err
    assert "HTTP 409" in failure_error
    assert "deck is not ready" in failure_error
    assert SECRET_KEY not in failure_error
