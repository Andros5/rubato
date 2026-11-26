ALTER TABLE replay_checkpoints
    ADD COLUMN record_until timestamptz,
    ADD COLUMN stopped_at timestamptz,
    ADD COLUMN stop_reason text,
    ADD COLUMN through_config bigint NOT NULL DEFAULT 0,
    ADD COLUMN round_count integer NOT NULL DEFAULT 0,
    ADD COLUMN round_bytes integer NOT NULL DEFAULT 0;

CREATE INDEX replay_checkpoints_live_idx ON replay_checkpoints (record_until)
    WHERE stopped_at IS NULL AND record_until IS NOT NULL;

CREATE TABLE replay_rounds (
    checkpoint_id bigint NOT NULL REFERENCES replay_checkpoints (id) ON DELETE CASCADE,
    ordinal integer NOT NULL,
    t bigint NOT NULL,
    body jsonb NOT NULL,
    PRIMARY KEY (checkpoint_id, ordinal)
);

ALTER TABLE events ADD COLUMN recorded_at timestamptz;
ALTER TABLE events ALTER COLUMN recorded_at SET DEFAULT clock_timestamp();
ALTER TABLE decisions ADD COLUMN recorded_at timestamptz;
ALTER TABLE decisions ALTER COLUMN recorded_at SET DEFAULT clock_timestamp();
ALTER TABLE config_versions ADD COLUMN recorded_at timestamptz;
ALTER TABLE config_versions ALTER COLUMN recorded_at SET DEFAULT clock_timestamp();

ALTER TABLE attempts ADD COLUMN delivered_at bigint, ADD COLUMN ready_at_delivery bigint;
CREATE INDEX attempts_turn_delivered_idx ON attempts (turn_config, delivered_at)
    WHERE delivered_at IS NOT NULL;
ALTER TABLE clients ADD COLUMN last_claim_at bigint, ADD COLUMN last_slot_claim_at bigint;
