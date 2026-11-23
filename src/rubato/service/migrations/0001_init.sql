-- Rubato service schema v1. Times are integer epoch seconds (UTC).

CREATE TABLE clients (
    id          text PRIMARY KEY,
    token_hash  text NOT NULL UNIQUE,
    role        text NOT NULL DEFAULT 'client' CHECK (role IN ('client', 'admin')),
    models      jsonb NOT NULL DEFAULT '[]',
    slots       integer NOT NULL DEFAULT 1 CHECK (slots >= 0),
    available_until bigint,                       -- executor declared available until
    created_at  bigint NOT NULL
);

CREATE TABLE config_versions (
    id          bigserial PRIMARY KEY,
    kind        text NOT NULL CHECK (kind IN ('policy', 'resources')),
    body        jsonb NOT NULL,
    created_at  bigint NOT NULL,
    created_by  text NOT NULL
);

CREATE TABLE resources (
    id          text PRIMARY KEY,
    unit        text NOT NULL,
    capacity    bigint NOT NULL CHECK (capacity >= 0),
    win         text NOT NULL,
    length      bigint,
    reset_tz    text,
    win_offset  bigint NOT NULL DEFAULT 0,
    scope       jsonb NOT NULL DEFAULT '{}',
    strict      boolean NOT NULL DEFAULT true,
    version     integer NOT NULL DEFAULT 1
);

CREATE TABLE jobs (
    id            text PRIMARY KEY,               -- client || ':' || client_job_id
    client_id     text NOT NULL REFERENCES clients(id),
    client_job_id text NOT NULL,
    cls           text NOT NULL,
    urgent        boolean NOT NULL DEFAULT false,
    priority      integer NOT NULL DEFAULT 0,
    arrival       bigint NOT NULL,
    deadline      bigint,
    state         text NOT NULL DEFAULT 'active' CHECK (state IN ('active', 'done', 'failed', 'cancelled')),
    seq           bigserial,
    created_at    bigint NOT NULL,
    UNIQUE (client_id, client_job_id)
);

CREATE TABLE steps (
    job_id            text NOT NULL REFERENCES jobs(id),
    step_id           text NOT NULL,
    depends_on        text[] NOT NULL DEFAULT '{}',
    model             text,
    estimate          jsonb NOT NULL,
    maximum           jsonb,
    cap_mode          text NOT NULL DEFAULT 'strict' CHECK (cap_mode IN ('strict', 'orientative')),
    duration_estimate bigint NOT NULL DEFAULT 1,
    max_attempts      integer NOT NULL DEFAULT 1,
    attempts          integer NOT NULL DEFAULT 0,
    state             text NOT NULL DEFAULT 'waiting'
        CHECK (state IN ('waiting', 'ready', 'granted', 'running', 'unknown', 'done', 'failed', 'closed')),
    ready_since       bigint,
    next_reconsider   bigint,
    last_reason       text,
    PRIMARY KEY (job_id, step_id)
);
CREATE INDEX steps_ready_idx ON steps (state, ready_since) WHERE state = 'ready';
CREATE INDEX steps_reconsider_idx ON steps (next_reconsider) WHERE state = 'ready';

CREATE TABLE attempts (
    id               text PRIMARY KEY,
    job_id           text NOT NULL,
    step_id          text NOT NULL,
    n                integer NOT NULL,
    client_id        text NOT NULL,
    state            text NOT NULL
        CHECK (state IN ('granted', 'started', 'finished', 'expired', 'unknown', 'reconciled', 'released')),
    admitted         jsonb NOT NULL,
    granted_at       bigint NOT NULL,
    grant_expires_at bigint NOT NULL,
    started_at       bigint,
    finished_at      bigint,
    status           text,
    usage            jsonb,
    receipt          text,
    finish_request   text,                        -- hash of the accepted finish/reconcile payload
    delivered        boolean NOT NULL DEFAULT false,  -- handed to one claim already
    FOREIGN KEY (job_id, step_id) REFERENCES steps(job_id, step_id),
    UNIQUE (job_id, step_id, n)
);
-- at most one live attempt per step
CREATE UNIQUE INDEX attempts_live_idx ON attempts (job_id, step_id) WHERE state IN ('granted', 'started', 'unknown');
CREATE INDEX attempts_grant_exp_idx ON attempts (grant_expires_at) WHERE state = 'granted';

CREATE TABLE ledger (
    id          bigserial PRIMARY KEY,
    resource_id text NOT NULL REFERENCES resources(id),
    t           bigint NOT NULL,                  -- admission instant: decides the window
    amount      bigint NOT NULL,
    attempt_id  text NOT NULL,
    urgent      boolean NOT NULL,
    client_id   text NOT NULL,
    state       text NOT NULL CHECK (state IN ('reserved', 'confirmed', 'unknown', 'released')),
    updated_at  bigint NOT NULL
);
CREATE INDEX ledger_resource_t_idx ON ledger (resource_id, t);
CREATE INDEX ledger_attempt_idx ON ledger (attempt_id);

CREATE TABLE pauses (
    scope_kind  text NOT NULL,
    scope_value text NOT NULL,
    until       bigint NOT NULL,
    reason      text,
    PRIMARY KEY (scope_kind, scope_value)
);

CREATE TABLE decisions (
    id        bigserial PRIMARY KEY,
    t         bigint NOT NULL,
    job_id    text NOT NULL,
    step_id   text NOT NULL,
    attempt   integer NOT NULL,
    policy    text NOT NULL,
    action    text NOT NULL,
    reason    text NOT NULL,
    detail    text NOT NULL DEFAULT '',
    resource  text,
    value     jsonb,
    next_t    bigint
);
CREATE INDEX decisions_job_idx ON decisions (job_id, id);

CREATE TABLE events (
    id         bigserial PRIMARY KEY,
    t          bigint NOT NULL,
    kind       text NOT NULL,
    client_id  text,
    job_id     text,
    step_id    text,
    attempt_id text,
    corr       text,
    payload    jsonb NOT NULL DEFAULT '{}'
);
CREATE INDEX events_job_idx ON events (job_id, id);

CREATE TABLE idempotency (
    client_id    text NOT NULL,
    key          text NOT NULL,
    op           text NOT NULL,
    request_hash text NOT NULL,
    status_code  integer NOT NULL,
    response     jsonb NOT NULL,
    created_at   bigint NOT NULL,
    PRIMARY KEY (client_id, key, op)
);

CREATE TABLE test_clock (
    id     integer PRIMARY KEY CHECK (id = 1),
    offset_s bigint NOT NULL DEFAULT 0
);
INSERT INTO test_clock (id, offset_s) VALUES (1, 0);
