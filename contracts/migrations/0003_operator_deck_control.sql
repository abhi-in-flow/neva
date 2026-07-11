-- Forward migration for operator-controlled demo deck generation.
--
-- Generated decks now stop in a reviewable ready state. An explicit activation
-- transaction promotes exactly one deck to live while retaining prior decks for
-- quick rollback. Operator inputs and generation metrics remain attached to the
-- deck so the demo team can inspect what produced each card set.

ALTER TABLE decks
    DROP CONSTRAINT IF EXISTS decks_status_check;

ALTER TABLE decks
    ADD CONSTRAINT decks_status_check
    CHECK (status IN ('draft', 'generating', 'ready', 'live', 'failed'));

ALTER TABLE decks
    ADD COLUMN IF NOT EXISTS generation_input JSONB NOT NULL DEFAULT '{}'::jsonb,
    ADD COLUMN IF NOT EXISTS generation_metrics JSONB,
    ADD COLUMN IF NOT EXISTS failure_reason TEXT,
    ADD COLUMN IF NOT EXISTS activated_at TIMESTAMPTZ;

ALTER TABLE cards
    ADD COLUMN IF NOT EXISTS concept_id TEXT;

COMMENT ON COLUMN decks.generation_input IS
    'Operator-provided concept metadata used for generation and review';

COMMENT ON COLUMN decks.generation_metrics IS
    'Final deck generation throughput, rejection, and estimated cost metrics';

COMMENT ON COLUMN cards.concept_id IS
    'Stable operator or curated concept identifier used to generate this card';
