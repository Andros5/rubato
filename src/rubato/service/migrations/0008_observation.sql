ALTER TABLE resources ADD COLUMN alert_at integer CHECK (alert_at BETWEEN 1 AND 100);

CREATE TABLE scheduler_windows (
    started_at timestamptz PRIMARY KEY,
    completed_at timestamptz NOT NULL
);

CREATE TABLE replay_checkpoints (
    id bigint GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    captured_at bigint NOT NULL,
    through_event bigint NOT NULL,
    through_decision bigint NOT NULL,
    body jsonb NOT NULL
);
