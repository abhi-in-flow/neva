"""Define the frozen server/client API contract for every player-facing view.

The backend owns game rules. The frontend renders StateResponse and submits
requests defined here; it must not infer, cache, or manufacture hidden state.
These models also freeze the unauthenticated venue-TV leaderboard and
throughput-metrics payloads so backend and frontend work can proceed in
parallel without inventing field names.
"""

from __future__ import annotations

from datetime import datetime
from enum import StrEnum
from uuid import UUID

from pydantic import BaseModel, Field, model_validator


class Phase(StrEnum):
    ONBOARDING = "onboarding"
    QUEUED = "queued"
    SPEAKING_VIEW_IMAGE = "speaking_view_image"
    SPEAKING_CONFIRM_LABEL = "speaking_confirm_label"
    WAITING_PARTNER = "waiting_partner"
    GUESSING = "guessing"
    ROUND_RESULT = "round_result"
    SESSION_DONE = "session_done"


class DeckStatus(StrEnum):
    DRAFT = "draft"
    GENERATING = "generating"
    READY = "ready"
    LIVE = "live"
    FAILED = "failed"


class AdminConceptInput(BaseModel):
    concept_id: str = Field(min_length=1, max_length=80, pattern=r"^[a-z0-9][a-z0-9_-]*$")
    label_en: str = Field(min_length=1, max_length=120)
    locale: str = Field(min_length=1, max_length=120)
    cultural_hint: str = Field(min_length=1, max_length=500)


class AdminDeckGenerateRequest(BaseModel):
    region_tag: str = Field(min_length=1, max_length=80, pattern=r"^[a-z0-9][a-z0-9_-]*$")
    concepts: list[AdminConceptInput] = Field(min_length=6, max_length=60)

    @model_validator(mode="after")
    def require_unique_concept_ids(self) -> "AdminDeckGenerateRequest":
        """Reject duplicate concept identifiers before generation starts."""
        concept_ids = [concept.concept_id for concept in self.concepts]
        if len(concept_ids) != len(set(concept_ids)):
            raise ValueError("concept_id values must be unique within a deck")
        return self


class AdminDeckOperationResponse(BaseModel):
    deck_id: UUID
    status: DeckStatus


class AdminDeckSummary(BaseModel):
    deck_id: UUID
    region_tag: str
    status: DeckStatus
    card_count: int = 0
    generation_metrics: dict[str, int | float] | None = None
    failure_reason: str | None = None
    activated_at: datetime | None = None
    created_at: datetime


class AdminDeckCardReview(BaseModel):
    card_id: UUID
    concept_id: str | None = None
    image_url: str
    label_en: str
    labels: dict[str, str]
    verified: bool


class AdminDeckDetail(AdminDeckSummary):
    concepts: list[AdminConceptInput] = Field(default_factory=list)
    cards: list[AdminDeckCardReview] = Field(default_factory=list)


class AdminDeckListResponse(BaseModel):
    decks: list[AdminDeckSummary] = Field(default_factory=list)


class JoinRequest(BaseModel):
    nickname: str = Field(min_length=1, max_length=32)
    native_lang: str = Field(min_length=1, max_length=64)
    common_langs: list[str] = Field(min_length=1, max_length=12)


class JoinResponse(BaseModel):
    session_token: str


class PlayerState(BaseModel):
    nickname: str
    score: int = 0
    rank: int | None = None
    rounds_played: int = 0
    rounds_cap: int


class PairState(BaseModel):
    partner_nickname: str
    common_lang: str


class LabelState(BaseModel):
    text: str


class OptionState(BaseModel):
    id: UUID
    text: str


class TurnState(BaseModel):
    role: str | None = None
    card_image_url: str | None = None
    label: LabelState | None = None
    options: list[OptionState] | None = None
    audio_url: str | None = None
    attempts_left: int | None = None
    deadline_ts: int | None = None


class RoundResult(BaseModel):
    outcome: str
    points_delta: int
    message: str


class LeaderboardEntry(BaseModel):
    nickname: str
    score: int


class StateResponse(BaseModel):
    state_version: int
    phase: Phase
    player: PlayerState
    pair: PairState | None = None
    turn: TurnState | None = None
    last_result: RoundResult | None = None
    leaderboard_top: list[LeaderboardEntry] = Field(default_factory=list)


class LeaderboardResponse(BaseModel):
    entries: list[LeaderboardEntry] = Field(default_factory=list)


class MetricsResponse(BaseModel):
    validated_pairs: int = 0
    training_eligible_pairs: int = 0
    language_count: int = 0
    languages: list[str] = Field(default_factory=list)
    cost_per_validated_sample_usd: float | None = None
    gauntlet_pass_rate: float | None = None
    deck_images_per_minute: float | None = None
    deck_cost_per_image_usd: float | None = None


class GuessRequest(BaseModel):
    option_id: UUID


class AudioUploadResponse(BaseModel):
    status: str
    reason: str | None = None


class HealthResponse(BaseModel):
    status: str
    database: str
    environment: str | None = None
    instance_marker: str | None = None
    database_name: str | None = None
