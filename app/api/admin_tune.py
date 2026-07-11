"""Authenticated FastAPI routes for the local Gemma tune demonstration.

The Docker API only validates inputs, reads safe supervisor publications, and
writes filesystem queue requests. It never imports the isolated ``tune``
package, starts subprocesses, selects models, or accepts client paths. Every
route reuses the demo ``X-Deck-Admin-Key`` dependency.
"""

from __future__ import annotations

import logging
from pathlib import Path as FileSystemPath
from typing import Annotated
from uuid import UUID

from fastapi import (
    APIRouter,
    Depends,
    File,
    Form,
    HTTPException,
    Path,
    UploadFile,
    status,
)
from fastapi.responses import FileResponse

from app.deck_admin.deps import require_deck_admin_key
from app.tune_admin.config import get_tune_admin_config
from app.tune_admin.deps import get_tune_admin_service
from app.tune_admin.service import TuneAdminService, TuneAdminServiceError
from contracts.api_types import (
    AdminTuneJobDetail,
    AdminTuneJobOperationResponse,
    AdminTuneOverview,
)

logger = logging.getLogger(__name__)

router = APIRouter(
    prefix="/api/admin/tune",
    tags=["tune-admin"],
    dependencies=[Depends(require_deck_admin_key)],
)
ServiceDependency = Annotated[TuneAdminService, Depends(get_tune_admin_service)]
SampleId = Annotated[
    str,
    Path(min_length=1, max_length=80, pattern=r"^[A-Za-z0-9][A-Za-z0-9_-]*$"),
]


def _raise_tune_error(exc: TuneAdminServiceError) -> None:
    """Translate one safe service failure to an HTTP exception.

    Args:
        exc: Expected tune-admin domain failure.

    Raises:
        HTTPException: Always, preserving only safe status and detail.
    """
    logger.info(
        "_raise_tune_error called status_code=%s detail_chars=%s",
        exc.status_code,
        len(exc.detail),
    )
    raise HTTPException(status_code=exc.status_code, detail=exc.detail) from exc


async def _read_bounded_upload(upload: UploadFile) -> bytes:
    """Read multipart audio with configured chunk and total byte limits.

    Args:
        upload: FastAPI temporary upload stream.

    Returns:
        Uploaded bytes when their total size is within the configured limit.

    Raises:
        TuneAdminServiceError: With 413 as soon as the limit is exceeded.

    Side effects:
        Advances and closes the framework-managed upload stream.
    """
    config = get_tune_admin_config()
    logger.info(
        "_read_bounded_upload called content_type=%s filename_present=%s",
        upload.content_type,
        bool(upload.filename),
    )
    chunks: list[bytes] = []
    total = 0
    try:
        while True:
            chunk = await upload.read(config.upload_chunk_bytes)
            if not chunk:
                break
            total += len(chunk)
            if total > config.max_upload_bytes:
                raise TuneAdminServiceError(
                    status.HTTP_413_CONTENT_TOO_LARGE,
                    "Audio upload exceeds configured limit",
                )
            chunks.append(chunk)
    finally:
        await upload.close()
    logger.info("_read_bounded_upload completed byte_length=%s", total)
    return b"".join(chunks)


def _audio_media_type(path: FileSystemPath) -> str:
    """Map a confined server file suffix to a safe audio response type.

    Args:
        path: Resolved approved held-out audio file.

    Returns:
        A fixed MIME type; no caller metadata is reflected.
    """
    logger.info("_audio_media_type called suffix=%s", path.suffix.lower())
    return {
        ".flac": "audio/flac",
        ".wav": "audio/wav",
        ".webm": "audio/webm",
        ".ogg": "audio/ogg",
        ".mp3": "audio/mpeg",
        ".m4a": "audio/mp4",
    }.get(path.suffix.lower(), "application/octet-stream")


@router.get("/overview", response_model=AdminTuneOverview)
async def tune_overview(service: ServiceDependency) -> AdminTuneOverview:
    """Return supervisor, corpus, artifact, job, and approved sample state.

    Args:
        service: Injected filesystem tune administration service.

    Returns:
        Safe tune overview with local paths and raw logs omitted.
    """
    logger.info("tune_overview route called")
    try:
        response = service.overview()
    except TuneAdminServiceError as exc:
        _raise_tune_error(exc)
    logger.info(
        "tune_overview route completed supervisor_healthy=%s full_ready=%s",
        response.supervisor.healthy,
        response.full_adapter_ready,
    )
    return response


@router.post(
    "/jobs/train-smoke",
    response_model=AdminTuneJobOperationResponse,
    status_code=status.HTTP_202_ACCEPTED,
)
async def train_smoke(service: ServiceDependency) -> AdminTuneJobOperationResponse:
    """Queue one fixed one-step QLoRA training proof.

    Args:
        service: Injected filesystem tune administration service.

    Returns:
        Accepted queued operation. No model, path, or command is accepted from
        the browser.
    """
    logger.info("train_smoke route called")
    try:
        response = service.start_smoke_training()
    except TuneAdminServiceError as exc:
        _raise_tune_error(exc)
    logger.info("train_smoke route completed job_id=%s", response.job_id)
    return response


@router.post(
    "/jobs/infer-live",
    response_model=AdminTuneJobOperationResponse,
    status_code=status.HTTP_202_ACCEPTED,
)
async def infer_live(
    service: ServiceDependency,
    audio: Annotated[UploadFile, File(description="Temporary demo audio")],
    native_language: Annotated[str, Form(min_length=1, max_length=80)],
) -> AdminTuneJobOperationResponse:
    """Queue temporary live audio inference against base and full adapter.

    Args:
        service: Injected filesystem tune administration service.
        audio: Bounded multipart audio stream; its filename is ignored.
        native_language: Bounded operator-declared source language.

    Returns:
        Accepted queued operation.

    Side effects:
        Reads and closes the upload, then stages it under a server-generated
        UUID name only when all readiness and input checks pass.
    """
    logger.info(
        "infer_live route called content_type=%s language_chars=%s",
        audio.content_type,
        len(native_language),
    )
    try:
        payload = await _read_bounded_upload(audio)
        response = service.start_live_inference(
            audio=payload,
            content_type=audio.content_type or "",
            native_language=native_language,
        )
    except TuneAdminServiceError as exc:
        _raise_tune_error(exc)
    logger.info("infer_live route completed job_id=%s", response.job_id)
    return response


@router.get("/jobs/{job_id}", response_model=AdminTuneJobDetail)
async def tune_job(
    job_id: UUID,
    service: ServiceDependency,
) -> AdminTuneJobDetail:
    """Return sanitized status, events, and result for one tune job.

    Args:
        job_id: UUID returned by an accepted operation.
        service: Injected filesystem tune administration service.

    Returns:
        Safe parsed job detail.
    """
    logger.info("tune_job route called job_id=%s", job_id)
    try:
        response = service.get_job(job_id)
    except TuneAdminServiceError as exc:
        _raise_tune_error(exc)
    logger.info("tune_job route completed job_id=%s status=%s", job_id, response.status)
    return response


@router.get(
    "/samples/{sample_id}/audio",
    response_class=FileResponse,
    response_model=None,
)
async def heldout_audio(
    sample_id: SampleId,
    service: ServiceDependency,
) -> FileResponse:
    """Stream one authenticated approved held-out recording.

    Args:
        sample_id: Public identifier from ``heldout_samples``.
        service: Injected filesystem tune administration service.

    Returns:
        File response for an existing path confined beneath published audio.
    """
    logger.info("heldout_audio route called sample_id=%s", sample_id)
    try:
        path = service.get_sample_audio(sample_id)
    except TuneAdminServiceError as exc:
        _raise_tune_error(exc)
    logger.info("heldout_audio route completed sample_id=%s", sample_id)
    return FileResponse(path, media_type=_audio_media_type(path))
