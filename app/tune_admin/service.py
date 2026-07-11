"""Service layer for safe tune overview, queueing, polling, and audio access.

This module translates untrusted runtime JSON into frozen ``AdminTune*``
contracts, computes supervisor and full-adapter readiness, and creates the only
two allowed GPU requests. It never imports ``tune``, launches subprocesses, or
returns local paths, commands, raw logs, private metrics, or audio metadata.
"""

from __future__ import annotations

import logging
import re
from datetime import UTC, datetime
from pathlib import Path
from typing import Any, Callable, Protocol
from uuid import UUID, uuid4

from pydantic import ValidationError

from app.tune_admin.config import TuneAdminConfig
from app.tune_admin.repository import (
    TuneAdminBusyError,
    TuneAdminRepositoryError,
)
from contracts.api_types import (
    AdminTuneArtifactMetadata,
    AdminTuneArtifactProfile,
    AdminTuneCorpusMetadata,
    AdminTuneHeldoutComparison,
    AdminTuneHeldoutSample,
    AdminTuneJobDetail,
    AdminTuneJobEvent,
    AdminTuneJobKind,
    AdminTuneJobOperationResponse,
    AdminTuneJobResult,
    AdminTuneJobStatus,
    AdminTuneJobSummary,
    AdminTuneOverview,
    AdminTuneSampleCounts,
    AdminTuneSupervisorStatus,
)

logger = logging.getLogger(__name__)

_SENSITIVE_TEXT = re.compile(
    r"(?i)(/(?:home|root|etc|var|data|tmp)/|[A-Z]:\\|traceback|"
    r"\b(?:api[_-]?key|token|secret|stderr|stdout|command)\b)"
)
_MODEL_ID = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._/-]{0,159}$")
_HASH = re.compile(r"^[a-f0-9]{64}$")
_LANGUAGE = re.compile(r"^[\w .()'-]+$", re.UNICODE)


class TuneAdminServiceError(RuntimeError):
    """Expected API-safe service failure with an HTTP status."""

    def __init__(self, status_code: int, detail: str) -> None:
        """Store a safe status and bounded public detail.

        Args:
            status_code: HTTP response status selected by the service.
            detail: Non-sensitive operator-facing explanation.
        """
        super().__init__(detail)
        self.status_code = status_code
        self.detail = detail


class TuneAdminStore(Protocol):
    """Minimal filesystem repository interface used by the service."""

    def read_overview(self) -> dict[str, Any] | None:
        """Return the published overview object when present."""

    def read_job(self, job_id: UUID) -> dict[str, Any] | None:
        """Return one job object when present."""

    def read_active_job(self) -> dict[str, Any] | None:
        """Return the current queued/running job when present."""

    def enqueue(
        self,
        job_id: UUID,
        request_payload: dict[str, Any],
        job_payload: dict[str, Any],
    ) -> None:
        """Atomically publish one job and supervisor request."""

    def stage_upload(self, upload_name: str, payload: bytes) -> Path:
        """Stage bounded private audio and return its rollback path."""

    def remove_upload(self, upload_name: str) -> None:
        """Remove a staged upload after queue failure."""

    def resolve_published_audio(self, sample_id: str) -> Path | None:
        """Resolve one approved held-out audio file without exposing its path."""


def _safe_text(value: Any, *, maximum: int, fallback: str) -> str:
    """Normalize bounded browser text and redact path/log/secret indicators.

    Args:
        value: Runtime value that may not be a string.
        maximum: Maximum returned character count.
        fallback: Safe replacement for absent or sensitive content.

    Returns:
        One whitespace-normalized, bounded, non-sensitive string.
    """
    logger.info(
        "_safe_text called value_type=%s maximum=%s",
        type(value).__name__,
        maximum,
    )
    if not isinstance(value, str):
        return fallback
    normalized = " ".join(value.split())
    if not normalized or _SENSITIVE_TEXT.search(normalized):
        return fallback
    return normalized[:maximum]


def _safe_optional_text(value: Any, *, maximum: int) -> str | None:
    """Return safe bounded text or ``None`` when absent.

    Args:
        value: Runtime value.
        maximum: Maximum returned character count.

    Returns:
        Sanitized text, ``"[redacted]"`` for sensitive text, or ``None``.
    """
    logger.info(
        "_safe_optional_text called value_type=%s maximum=%s",
        type(value).__name__,
        maximum,
    )
    if value is None:
        return None
    return _safe_text(value, maximum=maximum, fallback="[redacted]")


def _safe_model_id(value: Any) -> str | None:
    """Accept an exact published manifest model identifier, never a path.

    Args:
        value: Published model identifier.

    Returns:
        A bounded relative identifier or ``None`` when invalid.
    """
    logger.info("_safe_model_id called value_type=%s", type(value).__name__)
    if not isinstance(value, str) or not _MODEL_ID.fullmatch(value):
        return None
    if value.startswith("/") or ".." in value or value.count("/") > 2:
        return None
    return value


def _safe_hash(value: Any) -> str | None:
    """Return a lowercase SHA-256 digest when structurally valid.

    Args:
        value: Runtime digest candidate.

    Returns:
        Valid digest or ``None``.
    """
    logger.info("_safe_hash called value_type=%s", type(value).__name__)
    return value if isinstance(value, str) and _HASH.fullmatch(value) else None


def _as_dict(value: Any) -> dict[str, Any]:
    """Return a plain dictionary or an empty dictionary.

    Args:
        value: Runtime JSON value.

    Returns:
        ``value`` when it is a dictionary, otherwise ``{}``.
    """
    logger.info("_as_dict called value_type=%s", type(value).__name__)
    return value if isinstance(value, dict) else {}


def _as_nonnegative_int(value: Any) -> int | None:
    """Coerce a non-boolean nonnegative integer when possible.

    Args:
        value: Runtime numeric candidate.

    Returns:
        Nonnegative integer or ``None``.
    """
    logger.info("_as_nonnegative_int called value_type=%s", type(value).__name__)
    if isinstance(value, bool):
        return None
    try:
        number = int(value)
    except (TypeError, ValueError):
        return None
    return number if number >= 0 else None


def _as_nonnegative_float(value: Any) -> float | None:
    """Coerce a finite nonnegative float when possible.

    Args:
        value: Runtime numeric candidate.

    Returns:
        Nonnegative float or ``None``.
    """
    logger.info("_as_nonnegative_float called value_type=%s", type(value).__name__)
    if isinstance(value, bool):
        return None
    try:
        number = float(value)
    except (TypeError, ValueError):
        return None
    return number if number >= 0 and number != float("inf") else None


def _sample_counts(value: Any) -> AdminTuneSampleCounts:
    """Shape aggregate sample counts without participant-level metadata.

    Args:
        value: Published ``sample_counts`` object.

    Returns:
        Safe nonnegative corpus counts.
    """
    logger.info("_sample_counts called")
    raw = _as_dict(value)
    return AdminTuneSampleCounts(
        total=_as_nonnegative_int(raw.get("total")) or 0,
        train=_as_nonnegative_int(raw.get("train")) or 0,
        holdout=_as_nonnegative_int(raw.get("holdout")) or 0,
    )


def _language_counts(value: Any) -> dict[str, int]:
    """Shape bounded aggregate language counts.

    Args:
        value: Published language-count mapping.

    Returns:
        Up to fifty safe labels and nonnegative counts.
    """
    logger.info("_language_counts called value_type=%s", type(value).__name__)
    if not isinstance(value, dict):
        return {}
    shaped: dict[str, int] = {}
    for key, raw_count in list(value.items())[:50]:
        label = _safe_text(key, maximum=80, fallback="")
        count = _as_nonnegative_int(raw_count)
        if label and count is not None:
            shaped[label] = count
    return shaped


def _corpus(value: Any) -> AdminTuneCorpusMetadata:
    """Shape published frozen-corpus metadata.

    Args:
        value: Raw overview corpus object.

    Returns:
        Safe corpus contract.
    """
    logger.info("_corpus called")
    raw = _as_dict(value)
    status = _safe_text(raw.get("status"), maximum=40, fallback="unavailable")
    return AdminTuneCorpusMetadata(
        ready=bool(raw.get("ready", status == "frozen")) and status in {"frozen", "ready"},
        status=status,
        input_mode=_safe_optional_text(raw.get("input_mode"), maximum=20),
        model_id=_safe_model_id(raw.get("model_id")),
        sample_counts=_sample_counts(raw.get("sample_counts")),
        language_counts=_language_counts(raw.get("language_counts")),
        source_corpus_sha256=_safe_hash(raw.get("source_corpus_sha256")),
        dataset_manifest_sha256=_safe_hash(raw.get("dataset_manifest_sha256")),
    )


def _artifact(value: Any) -> AdminTuneArtifactMetadata | None:
    """Shape one published adapter artifact and omit local paths/private config.

    Args:
        value: Raw smoke or full artifact object.

    Returns:
        Safe artifact contract, or ``None`` when no object is published.
    """
    logger.info("_artifact called value_type=%s", type(value).__name__)
    if not isinstance(value, dict):
        return None
    training = _as_dict(value.get("training"))
    lora = _as_dict(value.get("lora"))
    profile_raw = value.get("profile", training.get("profile"))
    try:
        profile = AdminTuneArtifactProfile(profile_raw) if profile_raw else None
    except ValueError:
        profile = None
    status = _safe_text(value.get("status"), maximum=40, fallback="unavailable")
    available = bool(value.get("available", status == "completed"))
    return AdminTuneArtifactMetadata(
        available=available,
        compatible=bool(value.get("compatible")),
        status=status,
        profile=profile,
        model_id=_safe_model_id(value.get("model_id")),
        input_mode=_safe_optional_text(value.get("input_mode"), maximum=20),
        sample_counts=_sample_counts(value.get("sample_counts")),
        language_counts=_language_counts(value.get("language_counts")),
        lora_rank=_as_nonnegative_int(value.get("lora_rank", lora.get("rank"))),
        completed_steps=_as_nonnegative_int(
            value.get(
                "completed_steps",
                value.get(
                    "max_steps",
                    training.get("completed_steps", training.get("max_steps")),
                ),
            )
        ),
        final_loss=_as_nonnegative_float(value.get("final_loss", training.get("final_loss"))),
        duration_seconds=_as_nonnegative_float(
            value.get("duration_seconds", training.get("duration_seconds"))
        ),
        peak_vram_gib=_as_nonnegative_float(
            value.get("peak_vram_gib", training.get("peak_vram_gib"))
        ),
        source_corpus_sha256=_safe_hash(value.get("source_corpus_sha256")),
        adapter_sha256=_safe_hash(value.get("adapter_sha256")),
        created_at=value.get("created_at"),
    )


def _supervisor(
    value: Any,
    *,
    now: datetime,
    stale_after_seconds: float,
) -> AdminTuneSupervisorStatus:
    """Compute host supervisor liveness from its published heartbeat.

    Args:
        value: Raw supervisor overview object.
        now: Current aware UTC time.
        stale_after_seconds: Maximum accepted heartbeat age.

    Returns:
        Safe computed supervisor status. A publication cannot force ``healthy``
        without a recent parseable heartbeat.
    """
    logger.info(
        "_supervisor called stale_after_seconds=%s now=%s",
        stale_after_seconds,
        now.isoformat(),
    )
    raw = _as_dict(value)
    status = _safe_text(raw.get("status"), maximum=40, fallback="unavailable")
    try:
        heartbeat = datetime.fromisoformat(str(raw.get("heartbeat_at")).replace("Z", "+00:00"))
        if heartbeat.tzinfo is None:
            heartbeat = heartbeat.replace(tzinfo=UTC)
        heartbeat = heartbeat.astimezone(UTC)
    except (TypeError, ValueError):
        heartbeat = None
    age = (now - heartbeat).total_seconds() if heartbeat is not None else None
    stale = age is None or age < 0 or age > stale_after_seconds
    healthy = not stale and status in {"ready", "idle", "running", "healthy"}
    return AdminTuneSupervisorStatus(
        healthy=healthy,
        stale=stale,
        status=status,
        heartbeat_at=heartbeat,
        message=_safe_optional_text(raw.get("message"), maximum=200),
    )


def _job_summary(value: Any) -> AdminTuneJobSummary:
    """Shape the public summary fields of one supervisor job.

    Args:
        value: Raw job status object.

    Returns:
        Validated safe job summary.

    Raises:
        ValueError: If required identifiers, states, or timestamps are invalid.
    """
    logger.info("_job_summary called")
    raw = _as_dict(value)
    return AdminTuneJobSummary(
        job_id=raw.get("job_id"),
        kind=raw.get("kind"),
        status=raw.get("status"),
        stage=_safe_text(raw.get("stage"), maximum=80, fallback="unknown"),
        progress=_as_nonnegative_float(raw.get("progress")) or 0,
        created_at=raw.get("created_at"),
        started_at=raw.get("started_at"),
        completed_at=raw.get("completed_at"),
    )


def _event(value: Any) -> AdminTuneJobEvent | None:
    """Shape one structured event and reject malformed timestamps.

    Args:
        value: Raw event object.

    Returns:
        Safe event or ``None`` when validation fails.
    """
    logger.info("_event called value_type=%s", type(value).__name__)
    if not isinstance(value, dict):
        return None
    try:
        return AdminTuneJobEvent(
            timestamp=value.get("timestamp", value.get("created_at")),
            stage=_safe_text(value.get("stage"), maximum=80, fallback="update"),
            message=_safe_text(
                value.get("message"),
                maximum=240,
                fallback="Progress update",
            ),
            progress=_as_nonnegative_float(value.get("progress")),
            step=_as_nonnegative_int(value.get("step")),
            loss=_as_nonnegative_float(value.get("loss")),
            elapsed_seconds=_as_nonnegative_float(value.get("elapsed_seconds")),
            peak_vram_gib=_as_nonnegative_float(value.get("peak_vram_gib")),
        )
    except ValidationError:
        return None


def _job_result(value: Any, kind: AdminTuneJobKind) -> AdminTuneJobResult | None:
    """Shape the operation-specific safe job result.

    Args:
        value: Raw result object.
        kind: Validated operation kind.

    Returns:
        Training-proof metadata or bounded inference outputs, never paths.
    """
    logger.info("_job_result called kind=%s", kind)
    if not isinstance(value, dict):
        return None
    if kind is AdminTuneJobKind.TRAIN_SMOKE:
        proof_raw = value.get("training_proof", value.get("artifact", value))
        proof = _artifact(proof_raw)
        if proof is not None and proof.profile is not AdminTuneArtifactProfile.SMOKE:
            proof = proof.model_copy(update={"profile": AdminTuneArtifactProfile.SMOKE})
        return AdminTuneJobResult(training_proof=proof) if proof is not None else None
    prediction = value
    predictions = value.get("predictions")
    if isinstance(predictions, list) and predictions and isinstance(predictions[0], dict):
        prediction = predictions[0]
    base = _safe_optional_text(
        prediction.get("base_output", prediction.get("base")),
        maximum=2000,
    )
    tuned = _safe_optional_text(
        prediction.get("tuned_output", prediction.get("tuned")),
        maximum=2000,
    )
    return AdminTuneJobResult(base_output=base, tuned_output=tuned)


class TuneAdminService:
    """Compose safe tune-demo reads and fixed supervisor requests."""

    def __init__(
        self,
        store: TuneAdminStore,
        config: TuneAdminConfig,
        *,
        now: Callable[[], datetime] | None = None,
        new_uuid: Callable[[], UUID] | None = None,
    ) -> None:
        """Bind the repository, centralized limits, and injectable clocks.

        Args:
            store: Filesystem repository or isolated test fake.
            config: Tune-admin runtime paths and safety limits.
            now: Optional aware-UTC clock for deterministic tests.
            new_uuid: Optional UUID factory for deterministic tests.
        """
        self._store = store
        self._config = config
        self._now = now or (lambda: datetime.now(UTC))
        self._new_uuid = new_uuid or uuid4
        logger.info("TuneAdminService initialized")

    def overview(self) -> AdminTuneOverview:
        """Return safe publication state and current GPU job.

        Returns:
            Complete overview. A missing publication yields an honest
            unavailable payload so the admin UI can render readiness guidance.

        Raises:
            TuneAdminServiceError: With 503 when a present runtime publication
            or job file is malformed.
        """
        logger.info("TuneAdminService.overview called")
        try:
            raw = self._store.read_overview()
            active_raw = self._store.read_active_job()
            if raw is None:
                return AdminTuneOverview(
                    readiness_reason="Tune supervisor has not published readiness",
                    current_job=_job_summary(active_raw) if active_raw is not None else None,
                )
            supervisor_raw = raw.get("supervisor")
            if not isinstance(supervisor_raw, dict) and raw.get("updated_at") is not None:
                supervisor_raw = {
                    "status": "ready",
                    "heartbeat_at": raw.get("updated_at"),
                }
            supervisor = _supervisor(
                supervisor_raw,
                now=self._now(),
                stale_after_seconds=self._config.heartbeat_stale_seconds,
            )
            corpus = _corpus(raw.get("corpus", raw.get("dataset")))
            smoke = _artifact(raw.get("smoke_artifact"))
            full_raw = raw.get("full_artifact")
            if not isinstance(full_raw, dict) and isinstance(raw.get("artifact"), dict):
                full_raw = {
                    **raw["artifact"],
                    "training": raw.get("training"),
                    "profile": _as_dict(raw.get("training")).get("profile"),
                    "input_mode": _as_dict(raw.get("training")).get("input_mode"),
                    "duration_seconds": _as_dict(raw.get("training")).get(
                        "duration_seconds"
                    ),
                    "peak_vram_gib": _as_dict(raw.get("training")).get("peak_vram_gib"),
                    "available": raw["artifact"].get("status") == "completed",
                    "compatible": raw["artifact"].get("compatible", False),
                }
            full = _artifact(full_raw)
            artifact_ready = self._full_artifact_ready(corpus, full)
            current_raw = active_raw or raw.get("current_job")
            current = _job_summary(current_raw) if current_raw is not None else None
            samples = self._heldout_samples(raw.get("heldout_samples"))
            comparisons = self._heldout_comparisons(
                raw.get("heldout_comparisons"),
                {sample.sample_id: sample.target for sample in samples},
            )
            full_ready = artifact_ready and bool(comparisons)
            reason = None
            if not supervisor.healthy:
                reason = "Local GPU supervisor is unavailable"
            elif not corpus.ready:
                reason = "Frozen tuning corpus is unavailable"
            elif not artifact_ready:
                reason = "Compatible full adapter is unavailable"
            elif not comparisons:
                reason = "No approved qualitative comparison is available"
            response = AdminTuneOverview(
                supervisor=supervisor,
                corpus=corpus,
                smoke_artifact=smoke,
                full_artifact=full,
                full_adapter_ready=full_ready,
                readiness_reason=reason,
                current_job=current,
                heldout_samples=samples,
                heldout_comparisons=comparisons if full_ready else [],
            )
        except (TuneAdminRepositoryError, ValidationError, TypeError, ValueError) as exc:
            logger.info("TuneAdminService.overview malformed type=%s", type(exc).__name__)
            raise TuneAdminServiceError(503, "Tune runtime metadata is unavailable") from exc
        logger.info(
            "TuneAdminService.overview completed supervisor_healthy=%s full_ready=%s "
            "sample_count=%s",
            response.supervisor.healthy,
            response.full_adapter_ready,
            len(response.heldout_samples),
        )
        return response

    def _full_artifact_ready(
        self,
        corpus: AdminTuneCorpusMetadata,
        artifact: AdminTuneArtifactMetadata | None,
    ) -> bool:
        """Enforce the technical full-versus-smoke artifact gate.

        Args:
            corpus: Safe frozen-corpus publication.
            artifact: Safe full-artifact publication.

        Returns:
            ``True`` only for a completed, compatible, explicitly full artifact
            whose published model and corpus digest match the frozen corpus.
            Browser inference additionally requires an approved comparison.
        """
        logger.info("_full_artifact_ready called artifact_present=%s", artifact is not None)
        if (
            not corpus.ready
            or artifact is None
            or not artifact.available
            or not artifact.compatible
            or artifact.status != "completed"
            or artifact.profile is not AdminTuneArtifactProfile.FULL
        ):
            return False
        if not artifact.model_id or artifact.model_id != corpus.model_id:
            return False
        if (
            not artifact.source_corpus_sha256
            or artifact.source_corpus_sha256 != corpus.source_corpus_sha256
        ):
            return False
        return bool(artifact.adapter_sha256)

    def _heldout_samples(self, value: Any) -> list[AdminTuneHeldoutSample]:
        """Shape bounded approved held-out sample metadata.

        Args:
            value: Published held-out sample list.

        Returns:
            At most the configured number of safe unique samples.
        """
        logger.info("_heldout_samples called value_type=%s", type(value).__name__)
        if not isinstance(value, list):
            return []
        samples: list[AdminTuneHeldoutSample] = []
        seen: set[str] = set()
        for raw in value:
            if not isinstance(raw, dict) or raw.get("approved") is not True:
                continue
            sample_id = raw.get("sample_id")
            if not isinstance(sample_id, str) or sample_id in seen:
                continue
            try:
                sample = AdminTuneHeldoutSample(
                    sample_id=sample_id,
                    native_language=_safe_text(
                        raw.get("native_language", raw.get("native_lang_tag")),
                        maximum=80,
                        fallback="Unknown",
                    ),
                    target=_safe_text(raw.get("target"), maximum=500, fallback="[redacted]"),
                    audio_available=bool(raw.get("audio_name")),
                )
            except ValidationError:
                continue
            samples.append(sample)
            seen.add(sample.sample_id)
            if len(samples) >= self._config.max_samples:
                break
        return samples

    def _heldout_comparisons(
        self,
        value: Any,
        approved_samples: dict[str, str],
    ) -> list[AdminTuneHeldoutComparison]:
        """Shape qualitative outputs only for approved published samples.

        Args:
            value: Published comparison list.
            approved_samples: Sample identifiers and targets accepted by sample
                shaping.

        Returns:
            Bounded safe comparisons with no aggregate score or audio path.
        """
        logger.info(
            "_heldout_comparisons called value_type=%s approved_count=%s",
            type(value).__name__,
            len(approved_samples),
        )
        if not isinstance(value, list):
            return []
        comparisons: list[AdminTuneHeldoutComparison] = []
        for raw in value:
            if not isinstance(raw, dict) or raw.get("approved") is not True:
                continue
            sample_id = raw.get("sample_id", raw.get("utterance_id"))
            if sample_id not in approved_samples:
                continue
            target = _safe_text(raw.get("target"), maximum=500, fallback="[redacted]")
            base_output = _safe_text(
                raw.get("base_output", raw.get("base")),
                maximum=2000,
                fallback="[redacted]",
            )
            tuned_output = _safe_text(
                raw.get("tuned_output", raw.get("tuned")),
                maximum=2000,
                fallback="[redacted]",
            )
            if (
                target != approved_samples[sample_id]
                or "[redacted]" in {target, base_output, tuned_output}
            ):
                continue
            try:
                comparison = AdminTuneHeldoutComparison(
                    sample_id=sample_id,
                    target=target,
                    base_output=base_output,
                    tuned_output=tuned_output,
                )
            except ValidationError:
                continue
            comparisons.append(comparison)
            if len(comparisons) >= self._config.max_samples:
                break
        return comparisons

    def start_smoke_training(self) -> AdminTuneJobOperationResponse:
        """Queue exactly one one-step training proof request.

        Returns:
            Accepted operation identifier in queued state.

        Raises:
            TuneAdminServiceError: With 503 when the local supervisor is not
            healthy, or 409 when another GPU operation is active.
        """
        logger.info("start_smoke_training called")
        overview = self.overview()
        if not overview.supervisor.healthy:
            raise TuneAdminServiceError(503, "Local GPU supervisor is unavailable")
        if not overview.corpus.ready:
            raise TuneAdminServiceError(503, "Frozen tuning corpus is unavailable")
        return self._enqueue(AdminTuneJobKind.TRAIN_SMOKE)

    def start_live_inference(
        self,
        *,
        audio: bytes,
        content_type: str,
        native_language: str,
    ) -> AdminTuneJobOperationResponse:
        """Stage temporary audio and queue full-adapter live comparison.

        Args:
            audio: Bounded upload bytes read by the route.
            content_type: Caller MIME type normalized by this method.
            native_language: Operator-declared language used in the fixed task.

        Returns:
            Accepted queued operation identifier.

        Raises:
            TuneAdminServiceError: For invalid input, unavailable supervisor or
            full adapter, busy GPU, or safe filesystem failure.

        Side effects:
            Atomically stages a server-named upload. Queue failure removes it.
        """
        normalized_type = content_type.partition(";")[0].strip().lower()
        language = " ".join(native_language.split())
        logger.info(
            "start_live_inference called byte_length=%s content_type=%s "
            "language_chars=%s",
            len(audio),
            normalized_type,
            len(language),
        )
        if normalized_type not in self._config.allowed_audio_types:
            raise TuneAdminServiceError(415, "Unsupported audio content type")
        if not audio:
            raise TuneAdminServiceError(400, "Audio upload is empty")
        if len(audio) > self._config.max_upload_bytes:
            raise TuneAdminServiceError(413, "Audio upload exceeds configured limit")
        if (
            not language
            or len(language) > self._config.max_language_chars
            or not _LANGUAGE.fullmatch(language)
        ):
            raise TuneAdminServiceError(422, "native_language is invalid")
        overview = self.overview()
        if not overview.supervisor.healthy:
            raise TuneAdminServiceError(503, "Local GPU supervisor is unavailable")
        if not overview.full_adapter_ready:
            raise TuneAdminServiceError(
                503,
                overview.readiness_reason or "Tuned inference is unavailable",
            )
        job_id = self._new_uuid()
        extension = self._config.extension_for_content_type(normalized_type)
        upload_name = f"{job_id}{extension}"
        try:
            self._store.stage_upload(upload_name, audio)
            return self._enqueue(
                AdminTuneJobKind.INFER_LIVE,
                job_id=job_id,
                extra_request={
                    "upload_name": upload_name,
                    "native_language": language,
                },
            )
        except TuneAdminServiceError:
            self._store.remove_upload(upload_name)
            raise
        except TuneAdminRepositoryError as exc:
            self._store.remove_upload(upload_name)
            raise TuneAdminServiceError(503, "Tune runtime storage is unavailable") from exc

    def _enqueue(
        self,
        kind: AdminTuneJobKind,
        *,
        job_id: UUID | None = None,
        extra_request: dict[str, Any] | None = None,
    ) -> AdminTuneJobOperationResponse:
        """Build and atomically publish one fixed supervisor request.

        Args:
            kind: One of the two contract-approved job kinds.
            job_id: Optional preallocated UUID used by staged live audio.
            extra_request: Server-constructed infer-live fields only.

        Returns:
            Queued operation response.

        Raises:
            TuneAdminServiceError: With 409 for busy state or 503 for runtime
            storage failure.
        """
        selected_id = job_id or self._new_uuid()
        created_at = self._now().astimezone(UTC).isoformat()
        logger.info("_enqueue called job_id=%s kind=%s", selected_id, kind)
        request_payload: dict[str, Any] = {
            "job_id": str(selected_id),
            "kind": kind.value,
            "created_at": created_at,
        }
        if extra_request:
            request_payload.update(extra_request)
        job_payload: dict[str, Any] = {
            "job_id": str(selected_id),
            "kind": kind.value,
            "status": AdminTuneJobStatus.QUEUED.value,
            "stage": "queued",
            "progress": 0.0,
            "events": [],
            "result": None,
            "failure_reason": None,
            "created_at": created_at,
            "started_at": None,
            "completed_at": None,
        }
        try:
            self._store.enqueue(selected_id, request_payload, job_payload)
        except TuneAdminBusyError as exc:
            raise TuneAdminServiceError(409, "A tune GPU job is already active") from exc
        except TuneAdminRepositoryError as exc:
            raise TuneAdminServiceError(503, "Tune runtime storage is unavailable") from exc
        return AdminTuneJobOperationResponse(
            job_id=selected_id,
            kind=kind,
            status=AdminTuneJobStatus.QUEUED,
        )

    def get_job(self, job_id: UUID) -> AdminTuneJobDetail:
        """Return one safely parsed job status.

        Args:
            job_id: Server-validated job UUID.

        Returns:
            Safe detail with structured events and operation-specific result.

        Raises:
            TuneAdminServiceError: With 404 for unknown jobs or 503 for malformed
            status files.
        """
        logger.info("get_job called job_id=%s", job_id)
        try:
            raw = self._store.read_job(job_id)
            if raw is None:
                raise TuneAdminServiceError(404, "Tune job not found")
            summary = _job_summary(raw)
            if summary.job_id != job_id:
                raise ValueError("job identifier mismatch")
            raw_events = raw.get("events")
            events = (
                [event for item in raw_events if (event := _event(item)) is not None]
                if isinstance(raw_events, list)
                else []
            )
            events = events[-self._config.max_events :]
            failure = None
            if summary.status is AdminTuneJobStatus.FAILED:
                failure = _safe_text(
                    raw.get("failure_reason"),
                    maximum=240,
                    fallback="Tune job failed",
                )
            response = AdminTuneJobDetail(
                **summary.model_dump(),
                events=events,
                result=_job_result(raw.get("result"), summary.kind),
                failure_reason=failure,
            )
        except TuneAdminServiceError:
            raise
        except (TuneAdminRepositoryError, ValidationError, TypeError, ValueError) as exc:
            logger.info("get_job malformed job_id=%s type=%s", job_id, type(exc).__name__)
            raise TuneAdminServiceError(503, "Tune job status is unavailable") from exc
        logger.info(
            "get_job completed job_id=%s status=%s event_count=%s",
            job_id,
            response.status,
            len(response.events),
        )
        return response

    def get_sample_audio(self, sample_id: str) -> Path:
        """Resolve one approved held-out recording for authenticated transfer.

        Args:
            sample_id: Public sample identifier from the overview contract.

        Returns:
            Existing confined file path for ``FileResponse``.

        Raises:
            TuneAdminServiceError: With 404 when unknown/unavailable or 503 when
            publication metadata violates the path boundary.
        """
        logger.info("get_sample_audio called sample_id=%s", sample_id)
        try:
            path = self._store.resolve_published_audio(sample_id)
        except TuneAdminRepositoryError as exc:
            raise TuneAdminServiceError(503, "Held-out audio is unavailable") from exc
        if path is None:
            raise TuneAdminServiceError(404, "Held-out sample audio not found")
        return path
