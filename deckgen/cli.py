"""CLI entrypoints for the regional picture-deck engine.

Exposes curated generation plus ``--concepts-file`` operator payloads,
optional pre-created deck IDs, and ready/live finalization. ``--dry-run``
exercises the full pipeline using fake images/responses and performs no API,
database, or runtime-data mutation.
"""

from __future__ import annotations

import argparse
import json
import logging
import sys
import uuid
from pathlib import Path
from typing import Any

from deckgen.client import SHARED_CLIENT_EXPECTATIONS
from deckgen.concepts import Concept, concepts_from_operator_mappings
from deckgen.config import (
    DECK_FINAL_STATUSES,
    DECK_STATUS_LIVE,
    DEFAULT_CARD_COUNT,
    DEFAULT_REGION,
    MAX_CARD_COUNT,
    MIN_CARD_COUNT,
    REGION_CONTEXTS,
)
from deckgen.pipeline import build_deck_sync

logger = logging.getLogger(__name__)


def build_parser() -> argparse.ArgumentParser:
    """Construct the deckgen argument parser.

    Returns:
        Configured ``ArgumentParser`` for the deck CLI.
    """
    logger.info("build_parser called")
    parser = argparse.ArgumentParser(
        prog="python -m deckgen",
        description=(
            "Generate a culturally grounded regional picture deck with "
            "NB2 Lite, Gemini verification, decoys, and atomic publication."
        ),
    )
    parser.add_argument(
        "--region",
        default=None,
        help=(
            "Region tag. Defaults to region_tag from --concepts-file, then "
            f"{DEFAULT_REGION}. Known: {', '.join(sorted(REGION_CONTEXTS))}"
        ),
    )
    parser.add_argument(
        "--cards",
        type=int,
        default=None,
        help=(
            f"Number of cards ({MIN_CARD_COUNT}–{MAX_CARD_COUNT}); defaults to "
            f"{DEFAULT_CARD_COUNT}, or derives from --concepts-file."
        ),
    )
    parser.add_argument(
        "--concepts-file",
        type=Path,
        default=None,
        help=("UTF-8 JSON operator concepts: a list, or an object with a 'concepts' list."),
    )
    parser.add_argument(
        "--deck-id",
        type=uuid.UUID,
        default=None,
        help="Optional UUID of an existing deck in generating status.",
    )
    parser.add_argument(
        "--final-status",
        choices=DECK_FINAL_STATUSES,
        default=DECK_STATUS_LIVE,
        help="Successful deck status (default live).",
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Use fake GenAI/publisher; no API, DB, or data-dir mutation.",
    )
    parser.add_argument(
        "--seed",
        type=int,
        default=None,
        help="Optional RNG seed for reproducible concept selection.",
    )
    parser.add_argument(
        "--log-level",
        default="INFO",
        choices=["DEBUG", "INFO", "WARNING", "ERROR"],
        help="Logging level (default INFO).",
    )
    return parser


def load_operator_deck_file(path: Path) -> tuple[list[Concept], str | None]:
    """Load operator concepts and an optional region from UTF-8 JSON.

    Args:
        path: JSON path containing either a top-level list or an object whose
            ``concepts`` member is a list and whose optional ``region_tag``
            matches the admin API request contract.

    Returns:
        Validated concepts and the optional normalized region tag.

    Raises:
        OSError: If the file cannot be read.
        json.JSONDecodeError: If the file is not valid JSON.
        TypeError: If the root/container shape is invalid.
        ValueError: If concept validation fails or the list is empty.
    """
    logger.info("load_operator_deck_file called path=%s", path)
    payload: Any = json.loads(path.read_text(encoding="utf-8"))
    region_tag: str | None = None
    if isinstance(payload, dict):
        unknown = set(payload) - {"concepts", "region_tag"}
        if unknown:
            raise ValueError(f"concepts file object has unknown fields: {sorted(unknown)}")
        if "concepts" not in payload:
            raise ValueError("concepts file object must contain 'concepts'")
        raw_region = payload.get("region_tag")
        if raw_region is not None:
            if not isinstance(raw_region, str) or not raw_region.strip():
                raise ValueError("concepts file region_tag must be a non-blank string")
            region_tag = raw_region.strip().lower()
        payload = payload["concepts"]
    if not isinstance(payload, list):
        raise TypeError("concepts file must contain a list of concept objects")
    concepts = concepts_from_operator_mappings(payload)
    if not concepts:
        raise ValueError("concepts file must contain at least one concept")
    logger.info(
        "load_operator_deck_file completed path=%s concept_count=%s region_tag=%s",
        path,
        len(concepts),
        region_tag,
    )
    return concepts, region_tag


def load_operator_concepts_file(path: Path) -> list[Concept]:
    """Load only operator concepts while preserving the original helper API.

    Args:
        path: Admin-compatible operator concept JSON file.

    Returns:
        Validated concepts in file order.
    """
    logger.info("load_operator_concepts_file called path=%s", path)
    concepts, _ = load_operator_deck_file(path)
    return concepts


def main(argv: list[str] | None = None) -> int:
    """Run the deckgen CLI.

    Args:
        argv: Optional argument vector (defaults to ``sys.argv[1:]``).

    Returns:
        Process exit code (0 on success, non-zero on failure).

    Side effects:
        Configures logging, runs the pipeline, prints metrics JSON to stdout.
    """
    parser = build_parser()
    args = parser.parse_args(argv)
    logging.basicConfig(
        level=getattr(logging, args.log_level),
        format="%(asctime)s %(levelname)s %(name)s %(message)s",
    )
    logger.info(
        "main called region=%s cards=%s concepts_file=%s deck_id=%s "
        "final_status=%s dry_run=%s seed=%s",
        args.region,
        args.cards,
        args.concepts_file,
        args.deck_id,
        args.final_status,
        args.dry_run,
        args.seed,
    )
    try:
        concepts, file_region = (
            load_operator_deck_file(args.concepts_file)
            if args.concepts_file is not None
            else (None, None)
        )
    except Exception as exc:
        logger.error("invalid concepts file: %s", exc)
        print(f"invalid --concepts-file: {exc}", file=sys.stderr)
        return 2

    card_count = (
        args.cards
        if args.cards is not None
        else len(concepts)
        if concepts is not None
        else DEFAULT_CARD_COUNT
    )
    if concepts is not None and args.cards is not None and args.cards != len(concepts):
        print(
            f"--cards ({args.cards}) must match --concepts-file count ({len(concepts)})",
            file=sys.stderr,
        )
        return 2
    if not (MIN_CARD_COUNT <= card_count <= MAX_CARD_COUNT):
        logger.error(
            "cards out of range cards=%s min=%s max=%s",
            card_count,
            MIN_CARD_COUNT,
            MAX_CARD_COUNT,
        )
        print(
            f"--cards must be between {MIN_CARD_COUNT} and {MAX_CARD_COUNT}",
            file=sys.stderr,
        )
        return 2

    region = args.region or file_region or DEFAULT_REGION
    try:
        result = build_deck_sync(
            region=region,
            cards=card_count,
            concepts=concepts,
            dry_run=args.dry_run,
            seed=args.seed,
            deck_id=args.deck_id,
            final_status=args.final_status,
        )
    except Exception as exc:
        logger.exception("deckgen failed: %s", exc)
        print(f"deckgen failed: {exc}", file=sys.stderr)
        if not args.dry_run:
            print(
                "\nShared client expectations:\n" + SHARED_CLIENT_EXPECTATIONS,
                file=sys.stderr,
            )
        return 1

    metrics = result.metrics.as_dict()
    payload = {
        "region": result.region,
        "dry_run": result.dry_run,
        "deck_id": str(result.publish.deck_id) if result.publish else None,
        "status": result.publish.status if result.publish else None,
        "card_count": len(result.cards),
        "metrics": metrics,
    }
    print(json.dumps(payload, indent=2, ensure_ascii=False))
    logger.info("main completed deck_id=%s", payload["deck_id"])
    return 0
