"""Throughput and cost metrics for Track 3 deck-generation demos.

Tracks images generated, verification rejects, elapsed wall time, and
estimated USD cost. Emits explicit images/minute, cost/image, reject rate,
and total deck cost — the numbers intended for stage readout.
"""

from __future__ import annotations

import logging
import time
from dataclasses import dataclass, field

from deckgen.config import COST_PER_FLASH_CALL_USD, COST_PER_IMAGE_USD

logger = logging.getLogger(__name__)


@dataclass
class DeckMetrics:
    """Mutable counters for one deck-generation run.

    Attributes:
        images_attempted: Total NB2 image generations (including rejects).
        images_accepted: Images that passed verification.
        images_rejected: Verification failures that triggered regeneration.
        flash_calls: Gemini Flash JSON calls (verify/translate/decoy).
        started_at: Monotonic start timestamp.
        finished_at: Monotonic end timestamp, or None while running.
        cost_per_image_usd: Pricing assumption for NB2 images.
        cost_per_flash_call_usd: Pricing assumption for Flash calls.
    """

    images_attempted: int = 0
    images_accepted: int = 0
    images_rejected: int = 0
    flash_calls: int = 0
    started_at: float = field(default_factory=time.monotonic)
    finished_at: float | None = None
    cost_per_image_usd: float = COST_PER_IMAGE_USD
    cost_per_flash_call_usd: float = COST_PER_FLASH_CALL_USD

    def record_image_attempt(self) -> None:
        """Increment the image-attempt counter.

        Side effects:
            Mutates ``images_attempted``.
        """
        self.images_attempted += 1
        logger.info(
            "DeckMetrics.record_image_attempt images_attempted=%s",
            self.images_attempted,
        )

    def record_accept(self) -> None:
        """Record a verification pass.

        Side effects:
            Mutates ``images_accepted``.
        """
        self.images_accepted += 1
        logger.info(
            "DeckMetrics.record_accept images_accepted=%s",
            self.images_accepted,
        )

    def record_reject(self) -> None:
        """Record a verification reject / regeneration.

        Side effects:
            Mutates ``images_rejected``.
        """
        self.images_rejected += 1
        logger.info(
            "DeckMetrics.record_reject images_rejected=%s",
            self.images_rejected,
        )

    def record_flash_call(self) -> None:
        """Record one Gemini Flash JSON call for cost accounting.

        Side effects:
            Mutates ``flash_calls``.
        """
        self.flash_calls += 1
        logger.info(
            "DeckMetrics.record_flash_call flash_calls=%s",
            self.flash_calls,
        )

    def finish(self) -> None:
        """Mark the run complete and freeze elapsed time.

        Side effects:
            Sets ``finished_at`` to the current monotonic clock.
        """
        self.finished_at = time.monotonic()
        logger.info("DeckMetrics.finish elapsed_s=%.3f", self.elapsed_seconds)

    @property
    def elapsed_seconds(self) -> float:
        """Wall time since start (or until finish if completed).

        Floors at 1 ms so dry-run / instantaneous fakes do not explode
        images-per-minute into astronomical values.
        """
        end = self.finished_at if self.finished_at is not None else time.monotonic()
        return max(end - self.started_at, 1e-3)

    @property
    def images_per_minute(self) -> float:
        """Accepted images per minute of wall-clock time."""
        return self.images_accepted / self.elapsed_seconds * 60.0

    @property
    def reject_rate(self) -> float:
        """Fraction of image attempts that were rejected (0..1)."""
        if self.images_attempted == 0:
            return 0.0
        return self.images_rejected / self.images_attempted

    @property
    def image_cost_usd(self) -> float:
        """Estimated NB2 image generation cost in USD."""
        return self.images_attempted * self.cost_per_image_usd

    @property
    def flash_cost_usd(self) -> float:
        """Estimated Flash JSON call cost in USD."""
        return self.flash_calls * self.cost_per_flash_call_usd

    @property
    def total_cost_usd(self) -> float:
        """Total estimated deck cost (images + Flash calls) in USD."""
        return self.image_cost_usd + self.flash_cost_usd

    @property
    def cost_per_image_usd_effective(self) -> float:
        """Blended cost per accepted image including rejects and Flash."""
        if self.images_accepted == 0:
            return 0.0
        return self.total_cost_usd / self.images_accepted

    def as_dict(self) -> dict[str, float | int]:
        """Serialize demo metrics for logging and CLI output.

        Returns:
            Dict with images/minute, cost/image, reject rate, and total cost.
        """
        payload = {
            "images_attempted": self.images_attempted,
            "images_accepted": self.images_accepted,
            "images_rejected": self.images_rejected,
            "flash_calls": self.flash_calls,
            "elapsed_seconds": round(self.elapsed_seconds, 3),
            "images_per_minute": round(self.images_per_minute, 3),
            "cost_per_image_usd": round(self.cost_per_image_usd_effective, 6),
            "reject_rate": round(self.reject_rate, 4),
            "total_cost_usd": round(self.total_cost_usd, 6),
            "nb2_unit_cost_usd": self.cost_per_image_usd,
        }
        logger.info("DeckMetrics.as_dict %s", payload)
        return payload
