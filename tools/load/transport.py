"""Injectable HTTP transport adapters for the load harness.

Provides a stdlib-only default and an optional httpx-backed implementation so
tests can stub transport without opening real sockets.
"""

from __future__ import annotations

import json
import logging
import time
import urllib.error
import urllib.request
from dataclasses import dataclass
from typing import Protocol

LOGGER = logging.getLogger(__name__)


@dataclass(frozen=True)
class HttpResponse:
    """Normalized HTTP response metadata."""

    status_code: int
    body: bytes
    headers: dict[str, str]


class HttpTransport(Protocol):
    """Protocol implemented by real and fake HTTP transports."""

    def request(
        self,
        method: str,
        url: str,
        *,
        headers: dict[str, str] | None = None,
        body: bytes | None = None,
        timeout_s: float = 10.0,
    ) -> HttpResponse:
        """Execute one HTTP request.

        Args:
            method: HTTP verb.
            url: Absolute URL.
            headers: Optional request headers.
            body: Optional request body.
            timeout_s: Socket timeout.

        Returns:
            Normalized response object.
        """
        ...


class StdlibTransport:
    """Thread-safe stdlib HTTP transport using ``urllib.request``."""

    def request(
        self,
        method: str,
        url: str,
        *,
        headers: dict[str, str] | None = None,
        body: bytes | None = None,
        timeout_s: float = 10.0,
    ) -> HttpResponse:
        """Perform one HTTP request via urllib.

        Args:
            method: HTTP verb.
            url: Absolute URL.
            headers: Optional request headers.
            body: Optional request body.
            timeout_s: Socket timeout.

        Returns:
            Normalized response, including error statuses from HTTPError.

        Raises:
            urllib.error.URLError: On transport-level failures.
        """
        LOGGER.info(
            "StdlibTransport.request called method=%s url_length=%s body_len=%s timeout_s=%s",
            method,
            len(url),
            len(body or b""),
            timeout_s,
        )
        request = urllib.request.Request(
            url=url,
            data=body,
            headers=headers or {},
            method=method.upper(),
        )
        started = time.perf_counter()
        try:
            with urllib.request.urlopen(request, timeout=timeout_s) as response:
                payload = response.read()
                elapsed_ms = (time.perf_counter() - started) * 1000.0
                LOGGER.info(
                    "StdlibTransport.request completed status=%s elapsed_ms=%.2f body_len=%s",
                    response.status,
                    elapsed_ms,
                    len(payload),
                )
                return HttpResponse(
                    status_code=response.status,
                    body=payload,
                    headers=dict(response.headers.items()),
                )
        except urllib.error.HTTPError as exc:
            payload = exc.read()
            elapsed_ms = (time.perf_counter() - started) * 1000.0
            LOGGER.info(
                "StdlibTransport.request http_error status=%s elapsed_ms=%.2f body_len=%s",
                exc.code,
                elapsed_ms,
                len(payload),
            )
            return HttpResponse(
                status_code=exc.code,
                body=payload,
                headers=dict(exc.headers.items()) if exc.headers else {},
            )


class RecordingTransport:
    """In-memory transport that records requests for dry-run tests."""

    def __init__(self) -> None:
        """Initialize an empty request log."""
        self.calls: list[dict[str, object]] = []

    def _canned_body(self, method: str, url: str) -> bytes:
        """Return endpoint-shaped JSON for recording-mode scenarios.

        Args:
            method: HTTP verb.
            url: Absolute request URL.

        Returns:
            UTF-8 JSON bytes matching the API contract shape.
        """
        import json
        from uuid import uuid4

        if url.endswith("/api/join"):
            return json.dumps({"session_token": f"fixture-token-{len(self.calls)}"}).encode("utf-8")
        if url.endswith("/api/pair/request"):
            return json.dumps({"status": "queued"}).encode("utf-8")
        if url.endswith("/api/state"):
            return json.dumps(
                {
                    "state_version": len(self.calls),
                    "phase": "queued",
                    "player": {
                        "nickname": "fixture",
                        "score": 0,
                        "rounds_played": 0,
                        "rounds_cap": 20,
                    },
                },
            ).encode("utf-8")
        if url.endswith("/api/turn/audio"):
            return json.dumps({"status": "re_record", "reason": "fixture"}).encode("utf-8")
        if url.endswith("/api/turn/confirm-label") or url.endswith("/api/turn/guess"):
            return json.dumps({"status": "ok"}).encode("utf-8")
        if url.endswith("/api/metrics"):
            return json.dumps({"validated_pairs": 0, "language_count": 0}).encode("utf-8")
        if url.endswith("/api/health"):
            return json.dumps(
                {
                    "status": "ok",
                    "database": "connected",
                    "environment": "load-test",
                    "instance_marker": "wave2-load-isolated",
                    "database_name": "dialect_factory_load_test",
                }
            ).encode("utf-8")
        if method.upper() == "GET":
            return b"{}"
        return json.dumps({"status": "ok", "option_id": str(uuid4())}).encode("utf-8")

    def request(
        self,
        method: str,
        url: str,
        *,
        headers: dict[str, str] | None = None,
        body: bytes | None = None,
        timeout_s: float = 10.0,
    ) -> HttpResponse:
        """Record a request and return a canned success response.

        Args:
            method: HTTP verb.
            url: Absolute URL.
            headers: Optional request headers.
            body: Optional request body.
            timeout_s: Ignored; accepted for protocol compatibility.

        Returns:
            Synthetic 200 response with minimal JSON.
        """
        LOGGER.info(
            "RecordingTransport.request called method=%s url=%s body_len=%s",
            method,
            url,
            len(body or b""),
        )
        self.calls.append(
            {
                "method": method,
                "url": url,
                "headers": dict(headers or {}),
                "body_len": len(body or b""),
            },
        )
        payload = self._canned_body(method, url)
        return HttpResponse(
            status_code=200,
            body=payload,
            headers={"content-type": "application/json"},
        )


def build_transport(preferred: str = "stdlib") -> HttpTransport:
    """Construct the configured HTTP transport implementation.

    Args:
        preferred: ``"stdlib"``, ``"httpx"``, or ``"recording"``.

    Returns:
        Transport instance.

    Raises:
        RuntimeError: When httpx is requested but unavailable.
    """
    LOGGER.info("build_transport called preferred=%s", preferred)
    if preferred == "recording":
        return RecordingTransport()
    if preferred == "httpx":
        return HttpxTransport()
    return StdlibTransport()


class HttpxTransport:
    """Optional httpx-backed transport for dev environments."""

    def request(
        self,
        method: str,
        url: str,
        *,
        headers: dict[str, str] | None = None,
        body: bytes | None = None,
        timeout_s: float = 10.0,
    ) -> HttpResponse:
        """Perform one HTTP request via httpx when installed.

        Args:
            method: HTTP verb.
            url: Absolute URL.
            headers: Optional request headers.
            body: Optional request body.
            timeout_s: Request timeout.

        Returns:
            Normalized response object.
        """
        LOGGER.info(
            "HttpxTransport.request called method=%s url_length=%s body_len=%s",
            method,
            len(url),
            len(body or b""),
        )
        try:
            import httpx
        except ImportError as exc:  # pragma: no cover - only when optional dep missing
            raise RuntimeError("httpx transport requested but httpx is not installed") from exc
        with httpx.Client(timeout=timeout_s) as client:
            response = client.request(method.upper(), url, headers=headers, content=body)
        return HttpResponse(
            status_code=response.status_code,
            body=response.content,
            headers=dict(response.headers.items()),
        )


def json_body(response: HttpResponse) -> dict[str, object]:
    """Parse a JSON object response body.

    Args:
        response: HTTP response with JSON payload.

    Returns:
        Parsed JSON object.

    Raises:
        json.JSONDecodeError: When the body is not valid JSON.
    """
    LOGGER.info("json_body called status_code=%s body_len=%s", response.status_code, len(response.body))
    return json.loads(response.body.decode("utf-8"))
