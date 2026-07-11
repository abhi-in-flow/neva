-- Wave 2 forward migration for retry scheduling, atomic speech de-duplication,
-- worker liveness, and canonical-metric query paths.
--
-- This migration is backward compatible with the frozen API and golden-record
-- shapes. It preserves immutable job creation timestamps while moving retry
-- scheduling to available_at, and adds only operational coordination tables.

ALTER TABLE jobs
    ADD COLUMN IF NOT EXISTS available_at TIMESTAMPTZ;

UPDATE jobs
SET available_at = created_at
WHERE available_at IS NULL;

ALTER TABLE jobs
    ALTER COLUMN available_at SET DEFAULT now(),
    ALTER COLUMN available_at SET NOT NULL;

DROP INDEX IF EXISTS jobs_claim_idx;
CREATE INDEX jobs_claim_idx
    ON jobs (status, available_at, created_at)
    WHERE status = 'pending';

CREATE TABLE IF NOT EXISTS speaker_audio_fingerprints (
    speaker_id UUID NOT NULL REFERENCES players(id),
    fingerprint TEXT NOT NULL,
    turn_id UUID NOT NULL UNIQUE REFERENCES turns(id),
    created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    PRIMARY KEY (speaker_id, fingerprint)
);

CREATE TABLE IF NOT EXISTS worker_heartbeats (
    worker_id TEXT PRIMARY KEY,
    process_id INTEGER NOT NULL,
    status TEXT NOT NULL CHECK (status IN ('starting', 'running', 'stopping')),
    started_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    heartbeat_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    metadata JSONB NOT NULL DEFAULT '{}'::jsonb
);

CREATE INDEX IF NOT EXISTS turns_validated_idx
    ON turns (speaker_id, created_at)
    WHERE outcome = 'validated';

CREATE INDEX IF NOT EXISTS records_training_eligible_idx
    ON records (created_at)
    WHERE training_eligible IS TRUE;

CREATE INDEX IF NOT EXISTS decks_live_activation_idx
    ON decks (activated_at DESC, created_at DESC)
    WHERE status = 'live';

CREATE INDEX IF NOT EXISTS api_calls_gauntlet_success_idx
    ON api_calls (created_at)
    WHERE status = 'success' AND operation = 'gauntlet_triage';
