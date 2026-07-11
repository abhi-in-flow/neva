"""Read-only operator CLI that walks one utterance through gauntlet stages.

Shows sanitized transformation evidence for judges without putting participant
audio or nicknames into the browser. Supports ``--fixture`` rehearsal mode so
tests and dry practice never touch live runtime data.
"""

from __future__ import annotations

import argparse
import json
import logging
import sys
from dataclasses import dataclass
from typing import Any

logger = logging.getLogger(__name__)

FIXTURE_STAGES = (
    {
        "stage": "1_capture",
        "status": "ok",
        "detail": "raw webm accepted (duration/silence checks)",
        "artifact": "audio/<turn_id>.webm",
    },
    {
        "stage": "2_normalize",
        "status": "ok",
        "detail": "ffmpeg → 16 kHz mono FLAC",
        "artifact": "audio/<turn_id>.flac",
    },
    {
        "stage": "3_triage",
        "status": "ok",
        "detail": "Gemini speech + contamination gates",
        "gates": {
            "is_speech": True,
            "single_speaker": True,
            "audio_quality_ok": True,
            "contamination_flag": False,
            "duplicate": False,
        },
    },
    {
        "stage": "4_human_validation",
        "status": "ok",
        "detail": "partner guess validated",
        "outcome": "validated",
    },
    {
        "stage": "5_package",
        "status": "ok",
        "detail": "golden record packaged",
        "training_eligible": True,
    },
    {
        "stage": "6_shard",
        "status": "ok",
        "detail": "appended to corpus shard",
        "shard_file": "shard_0001.jsonl",
    },
)


@dataclass(frozen=True, slots=True)
class PipelineSnapshot:
    """Sanitized utterance pipeline view for operator display."""

    turn_id: str
    stages: list[dict[str, Any]]
    training_eligible: bool | None
    source: str


def build_fixture_snapshot(turn_id: str = "fixture-turn") -> PipelineSnapshot:
    """Build a clearly marked rehearsal snapshot.

    Args:
        turn_id: Synthetic turn identifier shown in the fixture output.

    Returns:
        Fixture snapshot that never claims to be live venue data.
    """
    logger.info("build_fixture_snapshot called turn_id=%s", turn_id)
    stages = []
    for item in FIXTURE_STAGES:
        stage = dict(item)
        if "artifact" in stage and isinstance(stage["artifact"], str):
            stage["artifact"] = stage["artifact"].replace("<turn_id>", turn_id)
        stages.append(stage)
    return PipelineSnapshot(
        turn_id=turn_id,
        stages=stages,
        training_eligible=True,
        source="fixture",
    )


def render_snapshot(snapshot: PipelineSnapshot) -> str:
    """Render a judge-readable multiline pipeline report.

    Args:
        snapshot: Sanitized pipeline snapshot.

    Returns:
        Human-readable text with stage markers.
    """
    logger.info(
        "render_snapshot called turn_id=%s stage_count=%s source=%s",
        snapshot.turn_id,
        len(snapshot.stages),
        snapshot.source,
    )
    lines = [
        "=== PIPELINE VIEW ===",
        f"source={snapshot.source}",
        f"turn_id={snapshot.turn_id}",
        f"training_eligible={snapshot.training_eligible}",
        "",
    ]
    for stage in snapshot.stages:
        lines.append(f"[{stage.get('status', '?')}] {stage.get('stage')}: {stage.get('detail')}")
        for key, value in stage.items():
            if key in {"stage", "status", "detail"}:
                continue
            lines.append(f"    {key}={json.dumps(value, ensure_ascii=False)}")
    return "\n".join(lines)


def load_live_snapshot(database_url: str, turn_id: str) -> PipelineSnapshot:
    """Load one turn's sanitized stages from Postgres.

    Args:
        database_url: Postgres DSN (credentials never logged).
        turn_id: Turn UUID to inspect.

    Returns:
        Live snapshot built from turns/jobs/records only.

    Raises:
        RuntimeError: When asyncpg is unavailable or the turn is missing.

    Side effects:
        Opens a short-lived asyncpg connection and closes it.
    """
    logger.info("load_live_snapshot called turn_id=%s", turn_id)
    try:
        import asyncio

        import asyncpg
    except ImportError as exc:  # pragma: no cover - environment dependent
        raise RuntimeError("asyncpg is required for live pipeline view") from exc

    async def _load() -> PipelineSnapshot:
        conn = await asyncpg.connect(database_url)
        try:
            turn = await conn.fetchrow(
                """
                SELECT id::text AS turn_id, status, outcome, audio_path, audio_flac_path,
                       quality, duration_s
                FROM turns
                WHERE id = $1::uuid
                """,
                turn_id,
            )
            if turn is None:
                raise RuntimeError(f"turn not found: {turn_id}")
            jobs = await conn.fetch(
                """
                SELECT kind, status, tries, last_error, created_at
                FROM jobs
                WHERE payload->>'turn_id' = $1
                ORDER BY created_at
                """,
                turn_id,
            )
            record = await conn.fetchrow(
                """
                SELECT training_eligible, shard_file
                FROM records
                WHERE turn_id = $1::uuid
                """,
                turn_id,
            )
        finally:
            await conn.close()

        quality = turn["quality"] or {}
        if isinstance(quality, str):
            quality = json.loads(quality)
        stages: list[dict[str, Any]] = [
            {
                "stage": "1_capture",
                "status": "ok" if turn["audio_path"] else "missing",
                "detail": "raw audio path present" if turn["audio_path"] else "no raw audio",
                "artifact": turn["audio_path"],
                "duration_s": turn["duration_s"],
            },
            {
                "stage": "2_normalize",
                "status": "ok" if turn["audio_flac_path"] else "pending",
                "detail": "flac present" if turn["audio_flac_path"] else "awaiting triage transcode",
                "artifact": turn["audio_flac_path"],
            },
            {
                "stage": "3_triage",
                "status": "ok" if quality else "pending",
                "detail": "quality metadata on turn" if quality else "no quality yet",
                "gates": {
                    "is_speech": bool(quality.get("is_speech")) if quality else None,
                    "single_speaker": bool(quality.get("single_speaker")) if quality else None,
                    "audio_quality_ok": bool(quality.get("audio_quality_ok")) if quality else None,
                    "contamination_flag": bool(quality.get("contamination_flag"))
                    if quality
                    else None,
                    "duplicate": bool(quality.get("duplicate")) if quality else None,
                },
            },
            {
                "stage": "4_human_validation",
                "status": "ok" if turn["outcome"] == "validated" else "pending",
                "detail": f"turn status={turn['status']} outcome={turn['outcome']}",
                "outcome": turn["outcome"],
            },
        ]
        for job in jobs:
            stages.append(
                {
                    "stage": f"job_{job['kind']}",
                    "status": job["status"],
                    "detail": f"tries={job['tries']}",
                    "last_error": job["last_error"],
                }
            )
        if record is None:
            stages.append(
                {
                    "stage": "5_package",
                    "status": "pending",
                    "detail": "no records row yet",
                    "training_eligible": None,
                }
            )
        else:
            stages.append(
                {
                    "stage": "5_package",
                    "status": "ok",
                    "detail": "records row present",
                    "training_eligible": bool(record["training_eligible"]),
                }
            )
            stages.append(
                {
                    "stage": "6_shard",
                    "status": "ok" if record["shard_file"] else "pending",
                    "detail": "shard linked" if record["shard_file"] else "eligible but unexported",
                    "shard_file": record["shard_file"],
                }
            )
        eligible = bool(record["training_eligible"]) if record else None
        return PipelineSnapshot(
            turn_id=turn["turn_id"],
            stages=stages,
            training_eligible=eligible,
            source="live",
        )

    return asyncio.run(_load())


def build_parser() -> argparse.ArgumentParser:
    """Create the pipeline-view CLI parser."""
    logger.info("build_parser called")
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--fixture", action="store_true", help="Render rehearsal fixture only")
    parser.add_argument("--turn-id", help="Live turn UUID to inspect")
    parser.add_argument(
        "--database-url",
        default="",
        help="Postgres DSN for live mode (or set DATABASE_URL)",
    )
    parser.add_argument(
        "--watch-seconds",
        type=float,
        default=0,
        help="Optional polling interval; 0 = one-shot",
    )
    return parser


def main(argv: list[str] | None = None) -> int:
    """Run one-shot or watch-mode sanitized pipeline rendering.

    Args:
        argv: Optional CLI argv override.

    Returns:
        Process exit code.
    """
    logging.basicConfig(level=logging.INFO, format="%(levelname)s %(name)s %(message)s")
    args = build_parser().parse_args(argv)
    logger.info(
        "main called fixture=%s turn_id_present=%s watch_seconds=%s",
        args.fixture,
        bool(args.turn_id),
        args.watch_seconds,
    )

    def once() -> PipelineSnapshot:
        if args.fixture or not args.turn_id:
            if not args.fixture and not args.turn_id:
                print("No --turn-id supplied; rendering fixture rehearsal snapshot.", file=sys.stderr)
            return build_fixture_snapshot(args.turn_id or "fixture-turn")
        database_url = args.database_url or __import__("os").environ.get("DATABASE_URL", "")
        if not database_url:
            raise SystemExit("live mode requires --database-url or DATABASE_URL")
        return load_live_snapshot(database_url, args.turn_id)

    if args.watch_seconds and args.watch_seconds > 0:
        import time

        while True:
            print(render_snapshot(once()), flush=True)
            print("---", flush=True)
            time.sleep(args.watch_seconds)
    print(render_snapshot(once()))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
