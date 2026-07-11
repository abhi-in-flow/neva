-- Forward migration for the asynchronous triage/package contract.
--
-- Early Phase 0 databases were created while jobs supported only triage. This
-- migration preserves those databases, adds durable machine-quality metadata,
-- permits the package stage, and guarantees one job of each kind per turn.
-- The statements are safe to run once through the migration runner.

ALTER TABLE turns
    ADD COLUMN IF NOT EXISTS quality JSONB;

ALTER TABLE jobs
    DROP CONSTRAINT IF EXISTS jobs_kind_check;

ALTER TABLE jobs
    ADD CONSTRAINT jobs_kind_check
    CHECK (kind IN ('triage', 'package'));

COMMENT ON COLUMN cards.decoys IS
    'JSON array of card UUID strings from the same deck';

COMMENT ON COLUMN jobs.payload IS
    'For triage and package: exactly {"turn_id": "<uuid>"}';

CREATE UNIQUE INDEX IF NOT EXISTS jobs_turn_kind_unique_idx
    ON jobs (kind, (payload->>'turn_id'));
