"""Dry-run smoke tests proving heavyweight model and GPU work is not required.

The tests generate and prepare isolated temporary fixtures, invoke training and
comparison validation, and compute dependency-light private metrics. They do
not import optional model libraries through harness code or mutate real data.
"""

from __future__ import annotations

import json
import logging
import sys
from pathlib import Path

from tune.compare import main as compare_main, select_samples
from tune.config import load_config
from tune.make_dummy import generate_dummy, main as make_dummy_main
from tune.metrics import compute_metrics, main as metrics_main
from tune.prepare import main as prepare_main
from tune.train import main as train_main

LOGGER = logging.getLogger(__name__)
HEAVY_MODULES = {"torch", "unsloth", "transformers", "trl", "peft", "datasets"}


def test_prepare_train_and_compare_dry_runs_avoid_heavy_imports(
    tmp_path: Path,
    capsys: object,
) -> None:
    """Validate the complete fixture path without model downloads or GPU use."""
    LOGGER.info(
        "test_prepare_train_and_compare_dry_runs_avoid_heavy_imports called temp_name=%s",
        tmp_path.name,
    )
    initially_loaded = HEAVY_MODULES.intersection(sys.modules)
    dummy_dir = tmp_path / "dummy"
    prepared_dir = tmp_path / "prepared"
    generate_dummy(dummy_dir, 100)

    assert prepare_main(
        [
            "--corpus",
            str(dummy_dir / "corpus"),
            "--data-dir",
            str(dummy_dir),
            "--output",
            str(prepared_dir),
        ]
    ) == 0
    assert train_main(["--train", str(prepared_dir / "train.jsonl"), "--dry-run"]) == 0
    assert compare_main(["--holdout", str(prepared_dir / "holdout.jsonl"), "--dry-run"]) == 0

    newly_loaded = HEAVY_MODULES.intersection(sys.modules) - initially_loaded
    assert not newly_loaded
    captured = capsys.readouterr()
    assert "DRY RUN OK" in captured.out


def test_private_metrics_score_exact_and_fuzzy_outputs(tmp_path: Path) -> None:
    """Compute private exact and fuzzy aggregates from deterministic predictions."""
    LOGGER.info(
        "test_private_metrics_score_exact_and_fuzzy_outputs called temp_name=%s",
        tmp_path.name,
    )
    rows = [
        {
            "utterance_id": "fixture-1",
            "target": "Water pot",
            "base": "pot",
            "tuned": "water pot!",
        },
        {
            "utterance_id": "fixture-2",
            "target": "fish trap",
            "base": "fish",
            "tuned": "fish trap",
        },
    ]
    metrics = compute_metrics(rows, load_config())
    serialized = json.dumps(metrics)

    assert metrics["private"] is True
    assert metrics["sample_count"] == 2
    assert metrics["aggregates"]["tuned"]["exact_matches"] == 2
    assert "headline claims" in serialized


def test_comparison_selects_exactly_five_deterministic_rows() -> None:
    """Limit stage inference to the configured deterministic qualitative set."""
    LOGGER.info("test_comparison_selects_exactly_five_deterministic_rows called")
    rows = [{"utterance_id": f"row-{index}"} for index in range(20)]

    selected = select_samples(rows, 5)

    assert selected == rows[:5]
    assert len(selected) == 5


def test_dummy_and_metrics_dry_runs_do_not_write_outputs(tmp_path: Path) -> None:
    """Validate generator and metrics inputs without creating requested outputs."""
    LOGGER.info(
        "test_dummy_and_metrics_dry_runs_do_not_write_outputs called temp_name=%s",
        tmp_path.name,
    )
    dummy_output = tmp_path / "would-be-dummy"
    predictions = tmp_path / "predictions.jsonl"
    metrics_output = tmp_path / "private" / "metrics.json"
    predictions.write_text(
        json.dumps(
            {
                "utterance_id": "fixture",
                "target": "water pot",
                "base": "pot",
                "tuned": "water pot",
            }
        )
        + "\n",
        encoding="utf-8",
    )

    assert make_dummy_main(["--output", str(dummy_output), "--dry-run"]) == 0
    assert metrics_main(
        [
            "--predictions",
            str(predictions),
            "--output",
            str(metrics_output),
            "--dry-run",
        ]
    ) == 0
    assert not dummy_output.exists()
    assert not metrics_output.exists()

