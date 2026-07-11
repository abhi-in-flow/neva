"""Authenticated API client helpers for load scenarios.

Wraps join, pair, state polling, and bounded turn actions using injectable HTTP
transport. Never uploads real browser audio; fixture bytes are explicit stubs.
"""

from __future__ import annotations

import json
import logging
import time
import uuid
from dataclasses import dataclass, field
from typing import Callable
from urllib.parse import urljoin

from tools.load.config import LoadConfig
from tools.load.fixtures import fake_audio_bytes
from tools.load.metrics import MetricsCollector, RequestSample
from tools.load.transport import HttpResponse, HttpTransport, json_body

LOGGER = logging.getLogger(__name__)


@dataclass
class LoadClient:
    """One simulated authenticated player."""

    client_id: int
    nickname: str
    native_lang: str
    common_langs: list[str]
    token: str | None = None
    actions_taken: int = 0
    uploads_taken: int = 0
    state_version: int = -1


@dataclass
class ApiSession:
    """HTTP session bound to one target and metrics collector."""

    config: LoadConfig
    transport: HttpTransport
    metrics: MetricsCollector
    clients: list[LoadClient] = field(default_factory=list)

    def _url(self, path: str) -> str:
        """Join a relative API path to the configured target URL.

        Args:
            path: API path beginning with ``/``.

        Returns:
            Absolute URL string.
        """
        return urljoin(self.config.target_url.rstrip("/") + "/", path.lstrip("/"))

    def _record(
        self,
        *,
        endpoint: str,
        started: float,
        response: HttpResponse | None,
        error: str | None,
        client_id: int | None = None,
    ) -> HttpResponse | None:
        """Record one HTTP attempt into the metrics collector.

        Args:
            endpoint: Logical endpoint label.
            started: ``time.perf_counter()`` start timestamp.
            response: Response when present.
            error: Transport or parse error string.
            client_id: Optional simulated client identifier.

        Returns:
            The original response object when provided.
        """
        latency_ms = (time.perf_counter() - started) * 1000.0
        self.metrics.record(
            RequestSample(
                endpoint=endpoint,
                status_code=None if response is None else response.status_code,
                latency_ms=latency_ms,
                error=error,
                client_id=client_id,
            ),
        )
        return response

    def join_client(self, client: LoadClient) -> str:
        """Register one player via ``POST /api/join``.

        Args:
            client: Client metadata to register.

        Returns:
            Issued session token.

        Raises:
            RuntimeError: When join fails or returns no token.
        """
        LOGGER.info(
            "ApiSession.join_client called client_id=%s nickname=%s",
            client.client_id,
            client.nickname,
        )
        payload = json.dumps(
            {
                "nickname": client.nickname,
                "native_lang": client.native_lang,
                "common_langs": client.common_langs,
            },
        ).encode("utf-8")
        started = time.perf_counter()
        try:
            response = self.transport.request(
                "POST",
                self._url("/api/join"),
                headers={"Content-Type": "application/json"},
                body=payload,
                timeout_s=self.config.request_timeout_s,
            )
        except Exception as exc:  # noqa: BLE001 - surfaced as metrics + runtime error
            self._record(endpoint="join", started=started, response=None, error=str(exc), client_id=client.client_id)
            raise RuntimeError(f"join failed for client {client.client_id}") from exc
        self._record(endpoint="join", started=started, response=response, error=None, client_id=client.client_id)
        if response.status_code != 200:
            raise RuntimeError(f"join failed status={response.status_code}")
        token = json_body(response).get("session_token")
        if not isinstance(token, str) or not token:
            raise RuntimeError("join response missing session_token")
        client.token = token
        self.clients.append(client)
        return token

    def request_pair(self, client: LoadClient) -> dict[str, object]:
        """Enqueue matchmaking via ``POST /api/pair/request``.

        Args:
            client: Authenticated client.

        Returns:
            Parsed JSON response body.

        Raises:
            RuntimeError: When the client has no token or the request fails.
        """
        LOGGER.info("ApiSession.request_pair called client_id=%s", client.client_id)
        if not client.token:
            raise RuntimeError("pair request requires session token")
        started = time.perf_counter()
        try:
            response = self.transport.request(
                "POST",
                self._url("/api/pair/request"),
                headers={"Authorization": f"Bearer {client.token}"},
                timeout_s=self.config.request_timeout_s,
            )
        except Exception as exc:  # noqa: BLE001
            self._record(endpoint="pair", started=started, response=None, error=str(exc), client_id=client.client_id)
            raise RuntimeError(f"pair failed for client {client.client_id}") from exc
        self._record(endpoint="pair", started=started, response=response, error=None, client_id=client.client_id)
        if response.status_code != 200:
            raise RuntimeError(f"pair failed status={response.status_code}")
        return json_body(response)

    def poll_state(self, client: LoadClient) -> dict[str, object]:
        """Poll ``GET /api/state`` for one client.

        Args:
            client: Authenticated client.

        Returns:
            Parsed state JSON.

        Raises:
            RuntimeError: When polling fails.
        """
        LOGGER.info("ApiSession.poll_state called client_id=%s", client.client_id)
        if not client.token:
            raise RuntimeError("state poll requires session token")
        started = time.perf_counter()
        try:
            response = self.transport.request(
                "GET",
                self._url("/api/state"),
                headers={"Authorization": f"Bearer {client.token}"},
                timeout_s=self.config.request_timeout_s,
            )
        except Exception as exc:  # noqa: BLE001
            self._record(endpoint="state", started=started, response=None, error=str(exc), client_id=client.client_id)
            raise RuntimeError(f"state poll failed for client {client.client_id}") from exc
        self._record(endpoint="state", started=started, response=response, error=None, client_id=client.client_id)
        if response.status_code != 200:
            raise RuntimeError(f"state poll failed status={response.status_code}")
        payload = json_body(response)
        version = payload.get("state_version")
        if isinstance(version, int):
            client.state_version = version
        return payload

    def upload_fixture_audio(self, client: LoadClient) -> dict[str, object]:
        """Upload bounded fake audio bytes via multipart ``POST /api/turn/audio``.

        Args:
            client: Authenticated speaker client.

        Returns:
            Parsed upload response JSON.

        Raises:
            RuntimeError: When upload bounds or transport fail.
        """
        LOGGER.info(
            "ApiSession.upload_fixture_audio called client_id=%s uploads_taken=%s",
            client.client_id,
            client.uploads_taken,
        )
        if client.uploads_taken >= self.config.max_uploads_per_client:
            raise RuntimeError("upload bound exceeded")
        if not client.token:
            raise RuntimeError("upload requires session token")
        body, content_type = fake_audio_bytes(client.client_id)
        boundary = f"load-{uuid.uuid4().hex}"
        multipart = (
            f"--{boundary}\r\n"
            f'Content-Disposition: form-data; name="file"; filename="fixture-{client.client_id}.wav"\r\n'
            f"Content-Type: {content_type}\r\n\r\n"
        ).encode("utf-8") + body + f"\r\n--{boundary}--\r\n".encode("utf-8")
        started = time.perf_counter()
        try:
            response = self.transport.request(
                "POST",
                self._url("/api/turn/audio"),
                headers={
                    "Authorization": f"Bearer {client.token}",
                    "Content-Type": f"multipart/form-data; boundary={boundary}",
                },
                body=multipart,
                timeout_s=self.config.request_timeout_s,
            )
        except Exception as exc:  # noqa: BLE001
            self._record(endpoint="audio", started=started, response=None, error=str(exc), client_id=client.client_id)
            raise RuntimeError(f"audio upload failed for client {client.client_id}") from exc
        self._record(endpoint="audio", started=started, response=response, error=None, client_id=client.client_id)
        client.uploads_taken += 1
        if response.status_code != 200:
            raise RuntimeError(f"audio upload failed status={response.status_code}")
        return json_body(response)

    def confirm_label(self, client: LoadClient) -> dict[str, object]:
        """Confirm label via ``POST /api/turn/confirm-label``.

        Args:
            client: Authenticated speaker client.

        Returns:
            Parsed JSON body.
        """
        LOGGER.info("ApiSession.confirm_label called client_id=%s", client.client_id)
        return self._authorized_post(client, endpoint="confirm-label", path="/api/turn/confirm-label")

    def submit_guess(self, client: LoadClient, option_id: str) -> dict[str, object]:
        """Submit a guess via ``POST /api/turn/guess``.

        Args:
            client: Authenticated guesser client.
            option_id: Selected option UUID string.

        Returns:
            Parsed JSON body.
        """
        LOGGER.info(
            "ApiSession.submit_guess called client_id=%s option_id=%s",
            client.client_id,
            option_id,
        )
        payload = json.dumps({"option_id": option_id}).encode("utf-8")
        return self._authorized_post(
            client,
            endpoint="guess",
            path="/api/turn/guess",
            body=payload,
            extra_headers={"Content-Type": "application/json"},
        )

    def _authorized_post(
        self,
        client: LoadClient,
        *,
        endpoint: str,
        path: str,
        body: bytes | None = None,
        extra_headers: dict[str, str] | None = None,
    ) -> dict[str, object]:
        """Perform one authorized POST and enforce action bounds.

        Args:
            client: Authenticated client.
            endpoint: Metrics endpoint label.
            path: API path.
            body: Optional JSON body.
            extra_headers: Additional headers.

        Returns:
            Parsed JSON response.

        Raises:
            RuntimeError: When action bounds are exceeded or the call fails.
        """
        if client.actions_taken >= self.config.max_actions_per_client:
            raise RuntimeError("action bound exceeded")
        if not client.token:
            raise RuntimeError("authorized POST requires session token")
        headers = {"Authorization": f"Bearer {client.token}"}
        if extra_headers:
            headers.update(extra_headers)
        started = time.perf_counter()
        try:
            response = self.transport.request(
                "POST",
                self._url(path),
                headers=headers,
                body=body,
                timeout_s=self.config.request_timeout_s,
            )
        except Exception as exc:  # noqa: BLE001
            self._record(endpoint=endpoint, started=started, response=None, error=str(exc), client_id=client.client_id)
            raise RuntimeError(f"{endpoint} failed for client {client.client_id}") from exc
        self._record(endpoint=endpoint, started=started, response=response, error=None, client_id=client.client_id)
        client.actions_taken += 1
        if response.status_code != 200:
            raise RuntimeError(f"{endpoint} failed status={response.status_code}")
        return json_body(response)

    def fetch_metrics(self) -> dict[str, object]:
        """Fetch unauthenticated ``GET /api/metrics``.

        Returns:
            Parsed metrics JSON.
        """
        LOGGER.info("ApiSession.fetch_metrics called")
        started = time.perf_counter()
        try:
            response = self.transport.request(
                "GET",
                self._url("/api/metrics"),
                timeout_s=self.config.request_timeout_s,
            )
        except Exception as exc:  # noqa: BLE001
            self._record(endpoint="metrics", started=started, response=None, error=str(exc))
            raise RuntimeError("metrics fetch failed") from exc
        self._record(endpoint="metrics", started=started, response=response, error=None)
        return json_body(response)

    def fetch_health(self) -> dict[str, object]:
        """Fetch ``GET /api/health`` for recovery checks.

        Returns:
            Parsed health JSON.
        """
        LOGGER.info("ApiSession.fetch_health called")
        started = time.perf_counter()
        try:
            response = self.transport.request(
                "GET",
                self._url("/api/health"),
                timeout_s=self.config.request_timeout_s,
            )
        except Exception as exc:  # noqa: BLE001
            self._record(endpoint="health", started=started, response=None, error=str(exc))
            raise RuntimeError("health fetch failed") from exc
        self._record(endpoint="health", started=started, response=response, error=None)
        return json_body(response)


def seed_clients(
    session: ApiSession,
    *,
    count: int,
    nickname_prefix: str = "load",
    languages: list[tuple[str, list[str]]] | None = None,
) -> list[LoadClient]:
    """Join and optionally pair a bounded set of simulated clients.

    Args:
        session: API session with transport and metrics.
        count: Number of clients to create.
        nickname_prefix: Prefix for generated nicknames.
        languages: Optional list of ``(native_lang, common_langs)`` pairs.

    Returns:
        Created client records with session tokens.

    Side effects:
        Issues join (and optional pair) HTTP calls unless transport is recording.
    """
    LOGGER.info("seed_clients called count=%s", count)
    if count < 0:
        raise ValueError("count must be >= 0")
    language_pairs = languages or [
        ("as", ["hi", "en"]),
        ("bn", ["hi", "en"]),
        ("hi", ["en", "as"]),
        ("en", ["hi", "bn"]),
    ]
    created: list[LoadClient] = []
    for index in range(count):
        native, common = language_pairs[index % len(language_pairs)]
        client = LoadClient(
            client_id=index,
            nickname=f"{nickname_prefix}-{index:03d}",
            native_lang=native,
            common_langs=common,
        )
        session.join_client(client)
        created.append(client)
    return created


def make_burst_schedule(
    config: LoadConfig,
    client_count: int,
    *,
    rng: Callable[[], float] | None = None,
) -> list[float]:
    """Build per-client burst offsets for jittered or synchronized actions.

    Args:
        config: Load configuration with burst mode and timing knobs.
        client_count: Number of scheduled offsets to produce.
        rng: Optional RNG callable returning floats in ``[0, 1)``.

    Returns:
        Monotonic offset list capped by ``action_burst_size``.
    """
    LOGGER.info(
        "make_burst_schedule called client_count=%s burst_mode=%s",
        client_count,
        config.burst_mode,
    )
    import random

    draw = rng or random.random
    size = min(client_count, config.action_burst_size)
    if config.burst_mode == "sync":
        base = config.sync_offset_s
        return [base for _ in range(size)]
    return [draw() * config.jitter_max_s for _ in range(size)]
