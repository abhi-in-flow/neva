"""Operate the protected deck-administration API from a local command line.

The module provides generate, list, show, and activate commands using only
Python's standard-library HTTP client. Generation input is validated against
the frozen Pydantic API contract before any request is sent. Mutating commands
also support dry-run output that redacts the admin key and performs no network
activity.
"""

from __future__ import annotations

import argparse
import json
import logging
import os
import sys
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path
from typing import Any
from urllib.error import HTTPError, URLError
from urllib.parse import urlsplit
from urllib.request import Request, urlopen
from uuid import UUID

from pydantic import ValidationError

from contracts.api_types import AdminDeckGenerateRequest

LOGGER = logging.getLogger(__name__)

DEFAULT_BASE_URL = "http://127.0.0.1:8000"
BASE_URL_ENV = "DECK_ADMIN_BASE_URL"
API_KEY_ENV = "DECK_ADMIN_API_KEY"
ADMIN_KEY_HEADER = "X-Deck-Admin-Key"
DEFAULT_TIMEOUT_SECONDS = 30.0
MAX_ERROR_BODY_CHARS = 500
EXIT_OK = 0
EXIT_REQUEST_ERROR = 1
EXIT_USAGE_ERROR = 2
JSON_INDENT = 2


class DeckAdminError(Exception):
    """Represent a safe operator-facing failure with a process exit code."""

    def __init__(self, message: str, *, exit_code: int) -> None:
        """Create an error without retaining credentials or request payloads.

        Args:
            message: Safe text suitable for printing to standard error.
            exit_code: Nonzero process status returned by the CLI.
        """
        super().__init__(message)
        self.exit_code = exit_code


@dataclass(frozen=True)
class HttpResponse:
    """Store the status and body returned by an HTTP transport."""

    status: int
    body: bytes


Transport = Callable[[Request, float], HttpResponse]


def default_transport(request: Request, timeout: float) -> HttpResponse:
    """Send one request with urllib and fully read its response.

    Args:
        request: Prepared urllib request, including the protected admin header.
        timeout: Maximum request duration in seconds.

    Returns:
        The response status and raw response body.

    Side effects:
        Performs one HTTP request. The admin key is never logged.
    """
    LOGGER.info(
        "default_transport called method=%s url=%s timeout_seconds=%s",
        request.get_method(),
        request.full_url,
        timeout,
    )
    with urlopen(request, timeout=timeout) as response:  # noqa: S310 - operator-selected URL
        return HttpResponse(status=response.status, body=response.read())


def normalize_base_url(raw_url: str) -> str:
    """Validate and normalize the configured API base URL.

    Args:
        raw_url: CLI or environment URL supplied by the operator.

    Returns:
        An HTTP(S) base URL without a trailing slash.

    Raises:
        DeckAdminError: If the URL is not an absolute HTTP(S) URL.
    """
    LOGGER.info("normalize_base_url called url_length=%s", len(raw_url))
    candidate = raw_url.strip().rstrip("/")
    parsed = urlsplit(candidate)
    if parsed.scheme not in {"http", "https"} or not parsed.netloc:
        raise DeckAdminError(
            "base URL must be an absolute http:// or https:// URL",
            exit_code=EXIT_USAGE_ERROR,
        )
    if parsed.query or parsed.fragment:
        raise DeckAdminError(
            "base URL must not contain a query string or fragment",
            exit_code=EXIT_USAGE_ERROR,
        )
    return candidate


def load_generate_request(input_path: Path) -> AdminDeckGenerateRequest:
    """Read and validate a generation request from a JSON file.

    Args:
        input_path: Path to the operator-authored JSON request.

    Returns:
        A validated frozen-contract request model.

    Raises:
        DeckAdminError: If the file cannot be read, decoded, or validated.
    """
    LOGGER.info("load_generate_request called input_path=%s", input_path)
    try:
        raw_text = input_path.read_text(encoding="utf-8")
    except OSError as exc:
        raise DeckAdminError(
            f"cannot read input file '{input_path}': {exc.strerror or exc}",
            exit_code=EXIT_USAGE_ERROR,
        ) from exc
    try:
        payload = json.loads(raw_text)
    except json.JSONDecodeError as exc:
        raise DeckAdminError(
            f"invalid JSON in '{input_path}' at line {exc.lineno}, column {exc.colno}",
            exit_code=EXIT_USAGE_ERROR,
        ) from exc
    try:
        request_model = AdminDeckGenerateRequest.model_validate(payload)
    except ValidationError as exc:
        raise DeckAdminError(
            f"generation input failed contract validation:\n{exc}",
            exit_code=EXIT_USAGE_ERROR,
        ) from exc
    LOGGER.info(
        "load_generate_request completed region_tag=%s concept_count=%s",
        request_model.region_tag,
        len(request_model.concepts),
    )
    return request_model


def parse_uuid(value: str) -> UUID:
    """Parse a canonical deck UUID for an argparse command argument.

    Args:
        value: Text supplied for a deck identifier.

    Returns:
        Parsed UUID value.

    Raises:
        argparse.ArgumentTypeError: If the value is not a UUID.
    """
    LOGGER.info("parse_uuid called value_length=%s", len(value))
    try:
        return UUID(value)
    except ValueError as exc:
        raise argparse.ArgumentTypeError("deck_id must be a valid UUID") from exc


def print_json(value: object) -> None:
    """Print deterministic, readable JSON to standard output.

    Args:
        value: JSON-serializable result or dry-run description.

    Side effects:
        Writes one JSON document to standard output.
    """
    LOGGER.info("print_json called value_type=%s", type(value).__name__)
    print(json.dumps(value, indent=JSON_INDENT, sort_keys=True, ensure_ascii=False))


class DeckAdminClient:
    """Build authenticated admin requests and execute them through a transport."""

    def __init__(
        self,
        *,
        base_url: str,
        api_key: str | None,
        transport: Transport = default_transport,
        timeout: float = DEFAULT_TIMEOUT_SECONDS,
    ) -> None:
        """Configure an API client without logging or exposing its key.

        Args:
            base_url: Absolute HTTP(S) server base URL.
            api_key: Admin credential, required for actual HTTP calls.
            transport: Injectable request transport used by tests and production.
            timeout: HTTP timeout in seconds.
        """
        LOGGER.info(
            "DeckAdminClient.__init__ called has_api_key=%s timeout_seconds=%s",
            bool(api_key),
            timeout,
        )
        self.base_url = normalize_base_url(base_url)
        self._api_key = api_key
        self._transport = transport
        self._timeout = timeout

    def execute(
        self,
        method: str,
        path: str,
        *,
        payload: dict[str, Any] | None = None,
        dry_run: bool = False,
    ) -> object:
        """Dry-run or execute one JSON admin API operation.

        Args:
            method: HTTP method for the endpoint.
            path: API path beginning with a slash.
            payload: Optional JSON object sent as the request body.
            dry_run: Print-safe mode that performs no transport call.

        Returns:
            Decoded JSON response, or a redacted dry-run request description.

        Raises:
            DeckAdminError: For missing credentials, HTTP failures, network
                failures, or malformed response JSON.
        """
        url = f"{self.base_url}{path}"
        LOGGER.info(
            "DeckAdminClient.execute called method=%s url=%s has_payload=%s dry_run=%s",
            method,
            url,
            payload is not None,
            dry_run,
        )
        if dry_run:
            return {
                "dry_run": True,
                "method": method,
                "url": url,
                "headers": {ADMIN_KEY_HEADER: "[REDACTED]"},
                "payload": payload,
            }
        if not self._api_key:
            raise DeckAdminError(
                f"admin API key is required; use --api-key or set {API_KEY_ENV}",
                exit_code=EXIT_USAGE_ERROR,
            )

        body = None if payload is None else json.dumps(payload).encode("utf-8")
        headers = {
            "Accept": "application/json",
            ADMIN_KEY_HEADER: self._api_key,
        }
        if body is not None:
            headers["Content-Type"] = "application/json"
        request = Request(url, data=body, headers=headers, method=method)
        try:
            response = self._transport(request, self._timeout)
        except HTTPError as exc:
            detail = _safe_http_error_detail(exc)
            raise DeckAdminError(
                f"admin API returned HTTP {exc.code}{detail}",
                exit_code=EXIT_REQUEST_ERROR,
            ) from exc
        except (URLError, TimeoutError, OSError) as exc:
            reason = getattr(exc, "reason", exc)
            raise DeckAdminError(
                f"admin API request failed: {reason}",
                exit_code=EXIT_REQUEST_ERROR,
            ) from exc

        if not 200 <= response.status < 300:
            detail = _safe_body_detail(response.body)
            raise DeckAdminError(
                f"admin API returned HTTP {response.status}{detail}",
                exit_code=EXIT_REQUEST_ERROR,
            )
        try:
            return json.loads(response.body.decode("utf-8"))
        except (UnicodeDecodeError, json.JSONDecodeError) as exc:
            raise DeckAdminError(
                "admin API returned an invalid JSON response",
                exit_code=EXIT_REQUEST_ERROR,
            ) from exc


def _safe_http_error_detail(error: HTTPError) -> str:
    """Extract a bounded, operator-safe detail from an urllib HTTP error.

    Args:
        error: HTTP error raised by urllib.

    Returns:
        A prefixed response detail, or an empty string.
    """
    LOGGER.info("_safe_http_error_detail called status=%s", error.code)
    try:
        return _safe_body_detail(error.read())
    except OSError:
        return ""


def _safe_body_detail(body: bytes) -> str:
    """Convert a bounded API error body into concise display text.

    Args:
        body: Raw response bytes.

    Returns:
        A prefixed detail string with control whitespace collapsed.
    """
    LOGGER.info("_safe_body_detail called body_bytes=%s", len(body))
    text = body.decode("utf-8", errors="replace")
    collapsed = " ".join(text.split())[:MAX_ERROR_BODY_CHARS]
    return f": {collapsed}" if collapsed else ""


def build_parser() -> argparse.ArgumentParser:
    """Build the complete deck administration argument parser.

    Returns:
        Parser with shared configuration and all four subcommands.
    """
    LOGGER.info("build_parser called")
    parser = argparse.ArgumentParser(description="Operate the deck administration API.")
    parser.add_argument(
        "--base-url",
        default=os.environ.get(BASE_URL_ENV, DEFAULT_BASE_URL),
        help=f"API base URL (env: {BASE_URL_ENV}; default: {DEFAULT_BASE_URL}).",
    )
    parser.add_argument(
        "--api-key",
        default=os.environ.get(API_KEY_ENV),
        help=f"Admin API key (env: {API_KEY_ENV}). Never printed or logged.",
    )
    subparsers = parser.add_subparsers(dest="command", required=True)

    generate_parser = subparsers.add_parser("generate", help="Submit a deck generation file.")
    generate_parser.add_argument("input", type=Path, help="JSON generation request file.")
    generate_parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Validate and print the redacted request without HTTP.",
    )

    subparsers.add_parser("list", help="List generated decks.")

    show_parser = subparsers.add_parser("show", help="Show one deck and its cards.")
    show_parser.add_argument("deck_id", type=parse_uuid)

    activate_parser = subparsers.add_parser("activate", help="Activate a ready deck.")
    activate_parser.add_argument("deck_id", type=parse_uuid)
    activate_parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Print the redacted activation request without HTTP.",
    )
    return parser


def run_command(args: argparse.Namespace, *, transport: Transport = default_transport) -> object:
    """Dispatch parsed CLI arguments to the matching admin endpoint.

    Args:
        args: Namespace produced by :func:`build_parser`.
        transport: Injectable standard-library-compatible HTTP transport.

    Returns:
        Decoded API response or dry-run request description.

    Side effects:
        Performs at most one HTTP call unless the command is a dry-run.
    """
    LOGGER.info("run_command called command=%s", args.command)
    client = DeckAdminClient(
        base_url=args.base_url,
        api_key=args.api_key,
        transport=transport,
    )
    if args.command == "generate":
        request_model = load_generate_request(args.input)
        payload = request_model.model_dump(mode="json")
        return client.execute("POST", "/api/admin/decks", payload=payload, dry_run=args.dry_run)
    if args.command == "list":
        return client.execute("GET", "/api/admin/decks")
    if args.command == "show":
        return client.execute("GET", f"/api/admin/decks/{args.deck_id}")
    if args.command == "activate":
        return client.execute(
            "POST",
            f"/api/admin/decks/{args.deck_id}/activate",
            dry_run=args.dry_run,
        )
    raise DeckAdminError("unknown command", exit_code=EXIT_USAGE_ERROR)


def main(argv: list[str] | None = None, *, transport: Transport = default_transport) -> int:
    """Run the operator CLI and convert safe failures to nonzero exits.

    Args:
        argv: Optional test argument vector; defaults to process arguments.
        transport: Injectable HTTP transport used to avoid network calls in tests.

    Returns:
        Zero on success, one for request failures, or two for input/config errors.

    Side effects:
        Configures INFO logging, prints JSON on success, and may perform one HTTP
        request. Credentials are never printed or logged.
    """
    logging.basicConfig(level=logging.INFO, format="%(levelname)s %(name)s %(message)s")
    LOGGER.info("main called argv_length=%s", 0 if argv is None else len(argv))
    try:
        args = build_parser().parse_args(argv)
        result = run_command(args, transport=transport)
    except DeckAdminError as exc:
        print(f"error: {exc}", file=sys.stderr)
        return exc.exit_code
    print_json(result)
    return EXIT_OK


if __name__ == "__main__":
    sys.exit(main())
