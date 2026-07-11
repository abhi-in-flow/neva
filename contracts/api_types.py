"""Define the frozen server/client API contract for every player-facing view.

The backend owns game rules. The frontend renders StateResponse and submits
requests defined here; it must not infer, cache, or manufacture hidden state.
These models also freeze the unauthenticated venue-TV leaderboard and
throughput-metrics payloads, plus demo-grade operator admin shapes for deck
control, redacted observability reads, and the authenticated filesystem-backed
Gemma tune demonstration, so backend and frontend work can proceed in parallel
without inventing field names.
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


class AdminDeckPromptGenerateRequest(BaseModel):
    """Primary operator path: one-line theme → Gemini concepts → NB2 images."""

    region_tag: str = Field(min_length=1, max_length=80, pattern=r"^[a-z0-9][a-z0-9_-]*$")
    prompt: str = Field(min_length=1, max_length=240)
    card_count: int = Field(default=8, ge=6, le=20)

    @model_validator(mode="after")
    def require_single_line_prompt(self) -> "AdminDeckPromptGenerateRequest":
        """Reject multi-line theme text; operators enter one demo-friendly line."""
        if "\n" in self.prompt or "\r" in self.prompt:
            raise ValueError("prompt must be a single line")
        stripped = self.prompt.strip()
        if not stripped:
            raise ValueError("prompt must not be blank")
        self.prompt = stripped
        return self


class AdminDeckOperationResponse(BaseModel):
    deck_id: UUID
    status: DeckStatus


class AdminDeckSummary(BaseModel):
    deck_id: UUID
    region_tag: str
    status: DeckStatus
    card_count: int = 0
    # Demo decks may include string mode markers (e.g. generation_mode) alongside
    # numeric throughput/cost counters from live Gemini generation.
    generation_metrics: dict[str, int | float | str | bool] | None = None
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


class AdminApiCallSummary(BaseModel):
    """One redacted GenAI instrumentation row for the operator traces panel."""

    id: UUID
    model: str
    operation: str
    status: str
    latency_ms: int | None = None
    estimated_cost_microusd: int | None = None
    created_at: datetime
    request_meta: dict[str, object] = Field(default_factory=dict)
    response_meta: dict[str, object] = Field(default_factory=dict)


class AdminApiCallListResponse(BaseModel):
    calls: list[AdminApiCallSummary] = Field(default_factory=list)


class AdminWorkerHeartbeat(BaseModel):
    """One worker liveness row suitable for the operator status strip."""

    worker_id: str
    process_id: int | None = None
    status: str | None = None
    started_at: datetime | None = None
    heartbeat_at: datetime | None = None
    healthy: bool = False
    metadata: dict[str, object] = Field(default_factory=dict)


class AdminWorkerStatusResponse(BaseModel):
    workers: list[AdminWorkerHeartbeat] = Field(default_factory=list)
    any_healthy: bool = False


class AdminJobSummary(BaseModel):
    """One gauntlet job row without audio paths or participant nicknames."""

    id: UUID
    kind: str
    turn_id: UUID | None = None
    status: str
    tries: int = 0
    last_error: str | None = None
    created_at: datetime
    available_at: datetime | None = None
    claimed_at: datetime | None = None
    completed_at: datetime | None = None


class AdminJobListResponse(BaseModel):
    jobs: list[AdminJobSummary] = Field(default_factory=list)
    counts_by_status: dict[str, int] = Field(default_factory=dict)


class AdminPipelineFunnelResponse(BaseModel):
    """Aggregate eligibility funnel for the admin metrics panel."""

    validated_pairs: int = 0
    packaged_records: int = 0
    training_eligible_pairs: int = 0
    gauntlet_pass_rate: float | None = None
    jobs_pending: int = 0
    jobs_processing: int = 0
    jobs_failed: int = 0


class AdminTuneArtifactProfile(StrEnum):
    """Training profile represented by one published adapter artifact."""

    SMOKE = "smoke"
    FULL = "full"


class AdminTuneJobKind(StrEnum):
    """GPU operation kinds accepted by the filesystem supervisor."""

    TRAIN_SMOKE = "train_smoke"
    INFER_LIVE = "infer_live"


class AdminTuneJobStatus(StrEnum):
    """Lifecycle states published for one tune-demo job."""

    QUEUED = "queued"
    RUNNING = "running"
    COMPLETED = "completed"
    FAILED = "failed"


class AdminTuneSampleCounts(BaseModel):
    """Aggregate frozen-corpus counts safe for the admin demo."""

    total: int = Field(default=0, ge=0)
    train: int = Field(default=0, ge=0)
    holdout: int = Field(default=0, ge=0)


class AdminTuneCorpusMetadata(BaseModel):
    """Safe identity and counts for the prepared tuning corpus."""

    ready: bool = False
    status: str = Field(default="unavailable", max_length=40)
    input_mode: str | None = Field(default=None, max_length=20)
    model_id: str | None = Field(default=None, max_length=160)
    sample_counts: AdminTuneSampleCounts = Field(default_factory=AdminTuneSampleCounts)
    language_counts: dict[str, int] = Field(default_factory=dict)
    source_corpus_sha256: str | None = Field(
        default=None,
        pattern=r"^[a-f0-9]{64}$",
    )
    dataset_manifest_sha256: str | None = Field(
        default=None,
        pattern=r"^[a-f0-9]{64}$",
    )


class AdminTuneArtifactMetadata(BaseModel):
    """Published adapter facts with all local paths and private metrics omitted."""

    available: bool = False
    compatible: bool = False
    status: str = Field(default="unavailable", max_length=40)
    profile: AdminTuneArtifactProfile | None = None
    model_id: str | None = Field(default=None, max_length=160)
    input_mode: str | None = Field(default=None, max_length=20)
    sample_counts: AdminTuneSampleCounts = Field(default_factory=AdminTuneSampleCounts)
    language_counts: dict[str, int] = Field(default_factory=dict)
    lora_rank: int | None = Field(default=None, ge=1, le=4096)
    completed_steps: int | None = Field(default=None, ge=0)
    final_loss: float | None = None
    duration_seconds: float | None = Field(default=None, ge=0)
    peak_vram_gib: float | None = Field(default=None, ge=0)
    source_corpus_sha256: str | None = Field(
        default=None,
        pattern=r"^[a-f0-9]{64}$",
    )
    adapter_sha256: str | None = Field(
        default=None,
        pattern=r"^[a-f0-9]{64}$",
    )
    created_at: datetime | None = None


class AdminTuneSupervisorStatus(BaseModel):
    """Filesystem supervisor heartbeat and readiness without host details."""

    healthy: bool = False
    stale: bool = True
    status: str = Field(default="unavailable", max_length=40)
    heartbeat_at: datetime | None = None
    message: str | None = Field(default=None, max_length=200)


class AdminTuneHeldoutSample(BaseModel):
    """Approved held-out example exposed without its underlying audio path."""

    sample_id: str = Field(
        min_length=1,
        max_length=80,
        pattern=r"^[A-Za-z0-9][A-Za-z0-9_-]*$",
    )
    native_language: str = Field(min_length=1, max_length=80)
    target: str = Field(min_length=1, max_length=500)
    audio_available: bool = False


class AdminTuneHeldoutComparison(BaseModel):
    """Approved qualitative base-versus-full-adapter output."""

    sample_id: str = Field(
        min_length=1,
        max_length=80,
        pattern=r"^[A-Za-z0-9][A-Za-z0-9_-]*$",
    )
    target: str = Field(min_length=1, max_length=500)
    base_output: str = Field(min_length=1, max_length=2000)
    tuned_output: str = Field(min_length=1, max_length=2000)


class AdminTuneJobEvent(BaseModel):
    """One bounded, structured progress event safe for browser display."""

    timestamp: datetime
    stage: str = Field(min_length=1, max_length=80)
    message: str = Field(min_length=1, max_length=240)
    progress: float | None = Field(default=None, ge=0, le=1)
    step: int | None = Field(default=None, ge=0)
    loss: float | None = None
    elapsed_seconds: float | None = Field(default=None, ge=0)
    peak_vram_gib: float | None = Field(default=None, ge=0)


class AdminTuneJobResult(BaseModel):
    """Safe result union for smoke proof or live comparison jobs."""

    training_proof: AdminTuneArtifactMetadata | None = None
    base_output: str | None = Field(default=None, max_length=2000)
    tuned_output: str | None = Field(default=None, max_length=2000)


class AdminTuneJobSummary(BaseModel):
    """Current GPU job summary embedded in the tune overview."""

    job_id: UUID
    kind: AdminTuneJobKind
    status: AdminTuneJobStatus
    stage: str = Field(min_length=1, max_length=80)
    progress: float = Field(default=0, ge=0, le=1)
    created_at: datetime
    started_at: datetime | None = None
    completed_at: datetime | None = None


class AdminTuneJobDetail(AdminTuneJobSummary):
    """Poll response containing sanitized events and optional safe result."""

    events: list[AdminTuneJobEvent] = Field(default_factory=list, max_length=200)
    result: AdminTuneJobResult | None = None
    failure_reason: str | None = Field(default=None, max_length=240)


class AdminTuneJobOperationResponse(BaseModel):
    """Accepted tune job identifier returned by mutating admin routes."""

    job_id: UUID
    kind: AdminTuneJobKind
    status: AdminTuneJobStatus


class AdminTuneOverview(BaseModel):
    """Complete safe state rendered by the authenticated Tune admin tab."""

    supervisor: AdminTuneSupervisorStatus = Field(
        default_factory=AdminTuneSupervisorStatus
    )
    corpus: AdminTuneCorpusMetadata = Field(default_factory=AdminTuneCorpusMetadata)
    smoke_artifact: AdminTuneArtifactMetadata | None = None
    full_artifact: AdminTuneArtifactMetadata | None = None
    full_adapter_ready: bool = False
    readiness_reason: str | None = Field(default=None, max_length=240)
    current_job: AdminTuneJobSummary | None = None
    heldout_samples: list[AdminTuneHeldoutSample] = Field(
        default_factory=list,
        max_length=20,
    )
    heldout_comparisons: list[AdminTuneHeldoutComparison] = Field(
        default_factory=list,
        max_length=20,
    )


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
