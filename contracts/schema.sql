-- Dialect Data Factory canonical Postgres contract.
--
-- This schema coordinates independently running API, game, gauntlet, and deck
-- processes. Gameplay writes turns and durable jobs; workers claim those jobs
-- with SKIP LOCKED. Audio triage may finish before or after human guessing, so
-- quality metadata is stored on the turn and a separate package job creates the
-- immutable golden record only when both machine and human gates are complete.

CREATE EXTENSION IF NOT EXISTS pgcrypto;

CREATE TABLE players (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    nickname TEXT NOT NULL CHECK (char_length(nickname) BETWEEN 1 AND 32),
    native_lang TEXT NOT NULL,
    common_langs JSONB NOT NULL DEFAULT '[]'::jsonb,
    session_token_hash TEXT NOT NULL UNIQUE,
    created_at TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE TABLE pairs (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    player_a UUID NOT NULL REFERENCES players(id),
    player_b UUID NOT NULL REFERENCES players(id),
    common_lang TEXT NOT NULL,
    status TEXT NOT NULL DEFAULT 'active' CHECK (status IN ('active', 'completed', 'abandoned')),
    created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    CHECK (player_a <> player_b)
);

CREATE TABLE matchmaking_queue (
    player_id UUID PRIMARY KEY REFERENCES players(id) ON DELETE CASCADE,
    enqueued_at TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE TABLE decks (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    region_tag TEXT NOT NULL,
    status TEXT NOT NULL DEFAULT 'draft'
        CHECK (status IN ('draft', 'generating', 'ready', 'live', 'failed')),
    -- Redacted operator inputs used to reproduce and review this deck.
    generation_input JSONB NOT NULL DEFAULT '{}'::jsonb,
    -- Final deck-generation throughput and cost counters.
    generation_metrics JSONB,
    failure_reason TEXT,
    activated_at TIMESTAMPTZ,
    created_at TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE TABLE cards (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    deck_id UUID NOT NULL REFERENCES decks(id),
    concept_id TEXT,
    image_path TEXT NOT NULL,
    label_common JSONB NOT NULL,
    -- JSON array of card UUID strings from this same deck.
    decoys JSONB NOT NULL DEFAULT '[]'::jsonb,
    verified BOOLEAN NOT NULL DEFAULT FALSE,
    created_at TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE TABLE turns (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    pair_id UUID NOT NULL REFERENCES pairs(id),
    speaker_id UUID NOT NULL REFERENCES players(id),
    guesser_id UUID NOT NULL REFERENCES players(id),
    card_id UUID NOT NULL REFERENCES cards(id),
    status TEXT NOT NULL DEFAULT 'awaiting_audio'
        CHECK (status IN ('awaiting_audio', 'awaiting_label_confirm', 'awaiting_guess', 'scored')),
    audio_path TEXT,
    audio_flac_path TEXT,
    duration_s NUMERIC(5,2),
    -- Machine triage result; null until the triage job completes.
    quality JSONB,
    attempts SMALLINT NOT NULL DEFAULT 0 CHECK (attempts BETWEEN 0 AND 2),
    outcome TEXT NOT NULL DEFAULT 'pending'
        CHECK (outcome IN ('pending', 'validated', 'wrong', 'unclear')),
    created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    CHECK (speaker_id <> guesser_id)
);

CREATE TABLE jobs (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    kind TEXT NOT NULL CHECK (kind IN ('triage', 'package')),
    -- Both job kinds use exactly {"turn_id": "<uuid>"}.
    payload JSONB NOT NULL,
    status TEXT NOT NULL DEFAULT 'pending'
        CHECK (status IN ('pending', 'processing', 'complete', 'failed')),
    tries SMALLINT NOT NULL DEFAULT 0,
    last_error TEXT,
    created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    claimed_at TIMESTAMPTZ,
    completed_at TIMESTAMPTZ
);

CREATE INDEX jobs_claim_idx ON jobs (status, created_at) WHERE status = 'pending';
CREATE UNIQUE INDEX jobs_turn_kind_unique_idx
    ON jobs (kind, (payload->>'turn_id'));

CREATE TABLE records (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    turn_id UUID NOT NULL UNIQUE REFERENCES turns(id),
    golden JSONB NOT NULL,
    training_eligible BOOLEAN NOT NULL,
    shard_file TEXT,
    created_at TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE TABLE metrics_counters (
    key TEXT PRIMARY KEY,
    value BIGINT NOT NULL DEFAULT 0,
    updated_at TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE TABLE api_calls (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    model TEXT NOT NULL,
    operation TEXT NOT NULL,
    request_meta JSONB NOT NULL DEFAULT '{}'::jsonb,
    response_meta JSONB NOT NULL DEFAULT '{}'::jsonb,
    status TEXT NOT NULL CHECK (status IN ('success', 'error')),
    latency_ms INTEGER,
    estimated_cost_microusd BIGINT,
    created_at TIMESTAMPTZ NOT NULL DEFAULT now()
);
