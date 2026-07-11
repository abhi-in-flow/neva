"""Compute private exact and fuzzy holdout metrics from comparison predictions.

Normalization is Unicode-aware, case-folded, punctuation-insensitive, and
whitespace-collapsed. Fuzzy similarity uses the Python standard library's
deterministic ``SequenceMatcher`` so this command stays dependency-light.
Outputs are explicitly marked private and are not suitable for headline claims
without human review.
"""

from __future__ import annotations

import argparse
import json
import logging
import os
import re
import unicodedata
from difflib import SequenceMatcher
from pathlib import Path
from typing import Any

from tune.config import TuneConfig, load_config

LOGGER = logging.getLogger(__name__)
WHITESPACE = re.compile(r"\s+")


def normalize_text(value: str) -> str:
    """Normalize text for conservative exact and fuzzy string comparison."""
    LOGGER.info("normalize_text called text_length=%d", len(value))
    normalized = unicodedata.normalize("NFKC", value).casefold()
    without_punctuation = "".join(
        character
        for character in normalized
        if not unicodedata.category(character).startswith(("P", "S"))
    )
    return WHITESPACE.sub(" ", without_punctuation).strip()


def read_predictions(path: Path) -> list[dict[str, str]]:
    """Read prediction JSONL requiring target, base, and tuned strings."""
    LOGGER.info("read_predictions called input_name=%s", path.name)
    if not path.is_file():
        raise ValueError(f"predictions file does not exist: {path}")
    rows: list[dict[str, str]] = []
    with path.open("r", encoding="utf-8") as handle:
        for line_number, line in enumerate(handle, start=1):
            if not line.strip():
                continue
            row = json.loads(line)
            required = ("utterance_id", "target", "base", "tuned")
            if not isinstance(row, dict) or not all(isinstance(row.get(key), str) for key in required):
                raise ValueError(f"invalid prediction row at {path.name}:{line_number}")
            rows.append({key: row[key] for key in required})
    if not rows:
        raise ValueError("predictions file contains no rows")
    return rows


def score_output(prediction: str, target: str, threshold: float) -> dict[str, float | bool]:
    """Score one output using normalized exact match and fuzzy similarity."""
    LOGGER.info(
        "score_output called prediction_length=%d target_length=%d threshold=%s",
        len(prediction),
        len(target),
        threshold,
    )
    normalized_prediction = normalize_text(prediction)
    normalized_target = normalize_text(target)
    similarity = SequenceMatcher(None, normalized_prediction, normalized_target).ratio()
    return {
        "exact": normalized_prediction == normalized_target,
        "similarity": round(similarity, 6),
        "fuzzy_match": similarity >= threshold,
    }


def compute_metrics(rows: list[dict[str, str]], config: TuneConfig) -> dict[str, Any]:
    """Compute per-model aggregates and per-sample private diagnostics."""
    LOGGER.info(
        "compute_metrics called row_count=%d fuzzy_threshold=%s",
        len(rows),
        config.fuzzy_threshold,
    )
    samples: list[dict[str, Any]] = []
    for row in rows:
        samples.append(
            {
                "utterance_id": row["utterance_id"],
                "base": score_output(row["base"], row["target"], config.fuzzy_threshold),
                "tuned": score_output(row["tuned"], row["target"], config.fuzzy_threshold),
            }
        )
    aggregates: dict[str, dict[str, float | int]] = {}
    for model_name in ("base", "tuned"):
        aggregates[model_name] = {
            "exact_matches": sum(int(sample[model_name]["exact"]) for sample in samples),
            "fuzzy_matches": sum(int(sample[model_name]["fuzzy_match"]) for sample in samples),
            "mean_similarity": round(
                sum(float(sample[model_name]["similarity"]) for sample in samples) / len(samples),
                6,
            ),
        }
    return {
        "private": True,
        "policy": "Do not publish or make headline claims without orchestrator approval.",
        "sample_count": len(samples),
        "fuzzy_threshold": config.fuzzy_threshold,
        "normalization": "NFKC + casefold + remove punctuation/symbols + collapse whitespace",
        "aggregates": aggregates,
        "samples": samples,
    }


def write_private_metrics(path: Path, metrics: dict[str, Any]) -> None:
    """Write metrics JSON and request owner-only permissions where supported."""
    LOGGER.info("write_private_metrics called output_name=%s", path.name)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        json.dumps(metrics, ensure_ascii=False, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )
    try:
        os.chmod(path, 0o600)
    except OSError:
        LOGGER.warning("owner-only metrics permissions could not be applied on this filesystem")


def build_parser() -> argparse.ArgumentParser:
    """Create the private metrics command-line parser."""
    LOGGER.info("build_parser called")
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--predictions", type=Path, required=True)
    parser.add_argument("--output", type=Path)
    parser.add_argument("--dry-run", action="store_true")
    return parser


def main(argv: list[str] | None = None) -> int:
    """Validate predictions or compute and write private evaluation metrics."""
    LOGGER.info("main called argv_provided=%s", argv is not None)
    logging.basicConfig(level=logging.INFO, format="%(levelname)s %(name)s %(message)s")
    args = build_parser().parse_args(argv)
    if not args.dry_run and args.output is None:
        raise SystemExit("--output is required unless --dry-run is used")
    config = load_config()
    rows = read_predictions(args.predictions)
    if args.dry_run:
        print(
            f"DRY RUN OK: predictions={len(rows)} fuzzy_threshold={config.fuzzy_threshold}; "
            "no metrics file written"
        )
        return 0
    metrics = compute_metrics(rows, config)
    write_private_metrics(args.output, metrics)
    print(f"PRIVATE METRICS WRITTEN: samples={len(rows)} output={args.output}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

