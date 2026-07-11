"""Application service for authenticated deck-control workflows.

The service coordinates repository transactions, background deck generation,
contract-model mapping, and safe media URLs. FastAPI concerns stay in the
router/dependency layer, while all collaborators are injectable for tests.
"""

from __future__ import annotations

import json
import logging
from pathlib import Path, PurePosixPath
from typing import Any, Mapping
from urllib.parse import quote
from uuid import UUID

from contracts.api_types import (
    AdminConceptInput,
    AdminDeckCardReview,
    AdminDeckDetail,
    AdminDeckGenerateRequest,
    AdminDeckListResponse,
    AdminDeckOperationResponse,
    AdminDeckPromptGenerateRequest,
    AdminDeckSummary,
    DeckStatus,
)

from app.deck_admin.config import FAILURE_REASON_MAX_CHARS, MEDIA_URL_PREFIX
from app.deck_admin.generation import DeckGenerationGateway
from app.deck_admin.repository import DeckAdminRepository

logger = logging.getLogger(__name__)


class DeckAdminError(Exception):
    """Expected service error carrying an HTTP-compatible status and detail."""

    def __init__(self, status_code: int, detail: str) -> None:
        """Create a safe domain error.

        Args:
            status_code: HTTP status for the route adapter.
            detail: Credential-free client-facing message.
        """
        logger.info(
            "DeckAdminError.__init__ called status_code=%s detail_chars=%s",
            status_code,
            len(detail),
        )
        super().__init__(detail)
        self.status_code = status_code
        self.detail = detail


def _json_mapping(value: Any) -> dict[str, Any]:
    """Normalize an asyncpg JSON/JSONB value to a dictionary.

    Args:
        value: Mapping, JSON string, or null from the repository.

    Returns:
        Parsed dictionary, or an empty dictionary for null.
    """
    logger.info("_json_mapping called value_type=%s", type(value).__name__)
    if value is None:
        return {}
    if isinstance(value, str):
        decoded = json.loads(value)
        return dict(decoded)
    return dict(value)


def _generation_metrics_for_api(
    value: Any,
) -> dict[str, int | float | str | bool] | None:
    """Coerce stored generation metrics into contract-safe scalar values.

    Demo decks may mix numeric throughput counters with string mode markers
    such as ``generation_mode``. Nested objects and unsupported types are
    dropped so list/review never 500 on validation.

    Args:
        value: Raw ``decks.generation_metrics`` JSONB payload.

    Returns:
        Scalar-only metrics mapping, or ``None`` when empty/absent.
    """
    logger.info(
        "_generation_metrics_for_api called value_present=%s value_type=%s",
        value is not None,
        type(value).__name__,
    )
    if value is None:
        return None
    raw = _json_mapping(value)
    cleaned: dict[str, int | float | str | bool] = {}
    for key, item in raw.items():
        if isinstance(item, bool):
            cleaned[str(key)] = item
        elif isinstance(item, int) and not isinstance(item, bool):
            cleaned[str(key)] = item
        elif isinstance(item, float):
            cleaned[str(key)] = item
        elif isinstance(item, str):
            cleaned[str(key)] = item
        else:
            logger.info(
                "_generation_metrics_for_api dropped key=%s value_type=%s",
                key,
                type(item).__name__,
            )
    return cleaned or None


def _summary(row: Mapping[str, Any]) -> AdminDeckSummary:
    """Map one repository row to the frozen summary contract.

    Args:
        row: Deck row with optional card count.

    Returns:
        Validated ``AdminDeckSummary``.
    """
    logger.info("_summary called deck_id=%s status=%s", row["id"], row["status"])
    return AdminDeckSummary(
        deck_id=row["id"],
        region_tag=row["region_tag"],
        status=DeckStatus(row["status"]),
        card_count=int(row.get("card_count", 0)),
        generation_metrics=_generation_metrics_for_api(row.get("generation_metrics")),
        failure_reason=row.get("failure_reason"),
        activated_at=row.get("activated_at"),
        created_at=row["created_at"],
    )


def _safe_image_url(image_path: str, data_dir: Path) -> str:
    """Convert a stored local image path to a same-origin media URL.

    Args:
        image_path: Relative path stored in ``cards.image_path`` or an absolute
            path beneath ``data_dir``.
        data_dir: Configured runtime data root.

    Returns:
        Percent-encoded ``/media/...`` URL with no inline data.

    Raises:
        DeckAdminError: If the path is outside the media root or unsafe.
    """
    logger.info(
        "_safe_image_url called path_chars=%s is_absolute=%s",
        len(image_path),
        Path(image_path).is_absolute(),
    )
    candidate = Path(image_path)
    if candidate.is_absolute():
        try:
            candidate = candidate.resolve().relative_to(data_dir.resolve())
        except ValueError as exc:
            raise DeckAdminError(500, "Deck contains an invalid image path") from exc
    normalized = PurePosixPath(str(candidate).replace("\\", "/"))
    if (
        not normalized.parts
        or any(part in {"", ".", ".."} for part in normalized.parts)
        or normalized.is_absolute()
        or ":" in normalized.parts[0]
    ):
        raise DeckAdminError(500, "Deck contains an invalid image path")
    encoded = "/".join(quote(part, safe="") for part in normalized.parts)
    return f"{MEDIA_URL_PREFIX}/{encoded}"


class DeckAdminService:
    """Coordinate deck-control use cases over injectable collaborators."""

    def __init__(
        self,
        repository: DeckAdminRepository,
        generator: DeckGenerationGateway,
        *,
        data_dir: Path,
    ) -> None:
        """Configure the service.

        Args:
            repository: Deck persistence boundary.
            generator: Late-bound background generation adapter.
            data_dir: Runtime media root used to validate review URLs.
        """
        logger.info("DeckAdminService.__init__ called data_dir=%s", data_dir)
        self._repository = repository
        self._generator = generator
        self._data_dir = data_dir

    async def start_generation(
        self, request: AdminDeckGenerateRequest
    ) -> AdminDeckOperationResponse:
        """Persist a generating deck and return immediately.

        Args:
            request: Validated region and operator concepts.

        Returns:
            Operation response with ``generating`` status.

        Side effects:
            Inserts one generating deck. Generation itself is scheduled by the
            route only after this method returns.
        """
        logger.info(
            "start_generation called region_tag=%s concept_count=%s",
            request.region_tag,
            len(request.concepts),
        )
        payload = request.model_dump(mode="json")
        row = await self._repository.create_generating(
            region_tag=request.region_tag,
            generation_input=payload,
        )
        response = AdminDeckOperationResponse(
            deck_id=row["id"], status=DeckStatus.GENERATING
        )
        logger.info("start_generation completed deck_id=%s", response.deck_id)
        return response

    async def start_prompt_generation(
        self, request: AdminDeckPromptGenerateRequest
    ) -> AdminDeckOperationResponse:
        """Persist a generating deck for the primary prompt-to-deck path.

        Args:
            request: Validated region, one-line theme, and card count.

        Returns:
            Operation response with ``generating`` status.

        Side effects:
            Inserts one generating deck whose ``generation_input`` stores the
            prompt payload (concepts are filled in during background invent).
        """
        logger.info(
            "start_prompt_generation called region_tag=%s prompt_chars=%s "
            "card_count=%s",
            request.region_tag,
            len(request.prompt),
            request.card_count,
        )
        payload = {
            "region_tag": request.region_tag,
            "prompt": request.prompt,
            "card_count": request.card_count,
            "source": "prompt",
            "concepts": [],
        }
        row = await self._repository.create_generating(
            region_tag=request.region_tag,
            generation_input=payload,
        )
        response = AdminDeckOperationResponse(
            deck_id=row["id"], status=DeckStatus.GENERATING
        )
        logger.info(
            "start_prompt_generation completed deck_id=%s",
            response.deck_id,
        )
        return response

    async def run_generation(
        self, deck_id: UUID, request: AdminDeckGenerateRequest
    ) -> None:
        """Run generation in the background and safely record failures.

        Args:
            deck_id: Existing generating deck.
            request: Validated operator request captured by the route.

        Side effects:
            Invokes GenAI/filesystem/DB deck generation through the gateway. On
            any failure, marks the deck failed with a bounded generic reason.
        """
        logger.info(
            "run_generation called deck_id=%s region_tag=%s concept_count=%s",
            deck_id,
            request.region_tag,
            len(request.concepts),
        )
        concepts = [concept.model_dump(mode="json") for concept in request.concepts]
        try:
            await self._generator.generate(
                region_tag=request.region_tag,
                concepts=concepts,
                deck_id=deck_id,
            )
        except Exception as exc:
            reason = f"Generation failed ({type(exc).__name__})"
            reason = reason[:FAILURE_REASON_MAX_CHARS]
            logger.error(
                "run_generation failed deck_id=%s exception_type=%s",
                deck_id,
                type(exc).__name__,
            )
            await self._repository.mark_failed(deck_id, reason)
            return
        logger.info("run_generation completed deck_id=%s", deck_id)

    async def run_prompt_generation(
        self, deck_id: UUID, request: AdminDeckPromptGenerateRequest
    ) -> None:
        """Run prompt invent + progressive image generation in the background.

        Args:
            deck_id: Existing generating deck.
            request: Validated prompt request captured by the route.

        Side effects:
            Invokes Gemini concept invent and progressive NB2 generation.
            Failures mark the deck failed without leaking exception details;
            partial cards may remain for review diagnostics.
        """
        logger.info(
            "run_prompt_generation called deck_id=%s region_tag=%s "
            "prompt_chars=%s card_count=%s",
            deck_id,
            request.region_tag,
            len(request.prompt),
            request.card_count,
        )
        try:
            await self._generator.generate_from_prompt(
                region_tag=request.region_tag,
                prompt=request.prompt,
                card_count=request.card_count,
                deck_id=deck_id,
            )
        except Exception as exc:
            reason = f"Generation failed ({type(exc).__name__})"
            reason = reason[:FAILURE_REASON_MAX_CHARS]
            logger.error(
                "run_prompt_generation failed deck_id=%s exception_type=%s",
                deck_id,
                type(exc).__name__,
            )
            await self._repository.mark_failed(deck_id, reason)
            return
        logger.info("run_prompt_generation completed deck_id=%s", deck_id)

    async def list_decks(self) -> AdminDeckListResponse:
        """Return newest deck summaries.

        Returns:
            Frozen list response contract.
        """
        logger.info("DeckAdminService.list_decks called")
        rows = await self._repository.list_decks()
        response = AdminDeckListResponse(decks=[_summary(row) for row in rows])
        logger.info(
            "DeckAdminService.list_decks completed deck_count=%s",
            len(response.decks),
        )
        return response

    async def review_deck(self, deck_id: UUID) -> AdminDeckDetail:
        """Return operator inputs and safe card review metadata.

        Args:
            deck_id: Deck to review.

        Returns:
            Frozen detail contract with same-origin image URLs.

        Raises:
            DeckAdminError: 404 when the deck does not exist.
        """
        logger.info("review_deck called deck_id=%s", deck_id)
        row = await self._repository.get_deck(deck_id)
        if row is None:
            raise DeckAdminError(404, "Deck not found")
        generation_input = _json_mapping(row.get("generation_input"))
        # Hide invented concept JSON while generating so the admin UI never
        # flashes raw Gemini concept payloads during progressive image work.
        status = DeckStatus(row["status"])
        if status is DeckStatus.GENERATING:
            concepts: list[AdminConceptInput] = []
        else:
            concepts = [
                AdminConceptInput.model_validate(concept)
                for concept in generation_input.get("concepts", [])
            ]
        cards: list[AdminDeckCardReview] = []
        for card in row.get("cards", []):
            labels = {
                str(key): str(value)
                for key, value in _json_mapping(card["label_common"]).items()
            }
            cards.append(
                AdminDeckCardReview(
                    card_id=card["id"],
                    concept_id=card.get("concept_id"),
                    image_url=_safe_image_url(card["image_path"], self._data_dir),
                    label_en=labels.get("en", ""),
                    labels=labels,
                    verified=bool(card["verified"]),
                )
            )
        summary = _summary(row)
        detail = AdminDeckDetail(
            **summary.model_dump(),
            concepts=concepts,
            cards=cards,
        )
        logger.info(
            "review_deck completed deck_id=%s card_count=%s concept_count=%s",
            deck_id,
            len(cards),
            len(concepts),
        )
        return detail

    async def activate(self, deck_id: UUID) -> AdminDeckOperationResponse:
        """Activate a ready deck or idempotently retain a live deck.

        Args:
            deck_id: Ready/live target deck.

        Returns:
            Operation response with live status.

        Raises:
            DeckAdminError: 404 for absence or 409 for a non-ready state.
        """
        logger.info("DeckAdminService.activate called deck_id=%s", deck_id)
        result = await self._repository.activate(deck_id)
        if result is None:
            raise DeckAdminError(404, "Deck not found")
        if result["deck"] is None:
            raise DeckAdminError(
                409,
                f"Deck must be ready before activation (current: {result['original_status']})",
            )
        response = AdminDeckOperationResponse(
            deck_id=result["deck"]["id"],
            status=DeckStatus.LIVE,
        )
        logger.info("DeckAdminService.activate completed deck_id=%s", deck_id)
        return response
