CREATE TABLE scheduler_status (
    singleton boolean PRIMARY KEY DEFAULT true CHECK (singleton),
    completed_at timestamptz NOT NULL,
    interval_s double precision NOT NULL CHECK (interval_s > 0)
);
