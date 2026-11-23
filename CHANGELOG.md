# Changelog

## 1.1.0 — 2026-11-23 (local release, not published)

- Live panel at `/panel`: quota per resource (background, urgent, uncertain), queue and
  waiting reasons per client, latest decisions, uncertain attempts with one-click
  reconciliation, active policy editor, alerts (uncertain attempts, provider pauses,
  quota below 10 %), live shadow-phase card (agreement and divergences of the last 24 h
  from the service events). Self-contained, escaped, works on a phone.
- Client modes controlled from the service: migration `0003_client_mode.sql`,
  `GET /v1/mode`, `PUT /v1/admin/clients/{id}/mode`; SDK `Gate(mode="auto")` and the
  radar and CitaLab adapters (`auto`) follow the switch within 30 s and keep the last
  known mode if Rubato is unreachable.
- `rubato status` (the panel in the terminal) and `scripts/ops/arranque_windows.ps1` (optional
  start at logon; not registered automatically). CI also checks formatting.
- Planner: hourly housekeeping of idempotency keys older than 7 days (`RUBATO_IDEMPOTENCY_DAYS`),
  run once at start; its logs were never printed (`basicConfig` without `force`) and now are.
- `GET /v1/admin/overview` and `rubato shadow-report` (agreement, divergences, added
  latency, readiness for enforce).
- Fix: a focused claim (adapter asking for one step) released the speculative permits the
  background planner had granted to the same client for other steps; before, with one
  slot, the adapter waited until those permits expired (60 s).

## 1.0.1 — 2026-11-23 (local release, not published) — audit

### Performance
- Ledger keeps entries sorted by admission instant and indexed by attempt: window
  queries are binary searches (calendar windows convert the time zone once per query,
  not once per entry) and each decision round uses a light tentative overlay instead of
  copying the ledger. The simulator only iterates active jobs. One week of the radar
  history (2,133 jobs) simulates in 1.45 s instead of 17.2 s (p95 per round 0.54 ms
  instead of 7.5 ms) with identical decisions and metrics; the date-block evaluation
  takes 3.7 s instead of ~34 s with identical results.

### Fixes
- Dependency cycles between steps are rejected by the trace loader, the simulator and the
  service (they used to leave the job waiting forever without a reason).
- Service: strict validation of job registrations (types, ranges, at most 100 steps) and
  of `slots`; occupied concurrency slots count whatever their age (they were dropped
  after the 3-day ledger lookback); Prometheus label values are escaped.
- HTML report: every trace-derived text inserted as HTML is escaped (a crafted job id
  could inject markup or script).
- SDK: when the provider call fails and Rubato cannot settle, the application's error is
  raised instead of Rubato's.
- Compose starts in normal mode; the reset and clock endpoints need `RUBATO_TEST_CLOCK=1`.
- `deploy/k8s/secrets.env.example` was ignored by Git (`*secret*` rule); now tracked.
- `scripts/ops/k8s_up.ps1` stops on native command failures and loads the image as a
  single-platform archive (Docker's containerd image store breaks `kind load docker-image`).

## 1.0.0 — 2026-11-23 (local release, not published)

### Simulator
- Single decision function `core.decidir` shared by simulator and service; stable reason
  codes with the deciding numbers and the reconsideration instant.
- Policies: arrival order (`fifo`), Radar reference (`radar`, default), urgent reserve
  (`reserva`: per-resource or glob reserve, release instant/offset, aging, impossible
  deadlines, protection of known urgent demand).
- Deterministic event simulator: rolling/calendar (time-zone aware)/concurrency/total
  windows; requests, tokens and integer money with versioned tariffs; dependencies,
  invalid outputs, errors, 429 pauses, cancellations, client restarts, actual usage
  above or below the estimate.
- Trace format `rubato.trace/v1` with per-field provenance; seeded synthetic generator;
  fail-safe prospective capture; read-only importer for Swiss Job Radar history.
- Offline optimum (exhaustive and CP-SAT) as a bound; job-by-job what-if.
- CLI: `init-demo`, `synth`, `simulate`, `compare`, `explain`, `evaluate`,
  `import-radar`, `demo-service`. Self-contained HTML report.

### Service
- FastAPI + PostgreSQL admission with all-or-nothing reservations, idempotent
  operations, permit expiry, uncertain attempts and reconciliation, provider pauses,
  shadow observations; migrations 0001–0002; planner process; simulated provider with
  receipts; Python SDK with `off` / `shadow` / `enforce`.
- Docker Compose (API, planner, PostgreSQL, provider, optional second replica);
  Kubernetes manifests and kind configuration for a local test cluster.
- Metrics, JSON logs with correlation ids, health/readiness, runbook, restore and
  failover drills.

### Integrations (separate branches, not merged)
- swiss-job-radar `rubato-adapter`, citalab `rubato-adapter`.

### Known limits
- See `docs/release-v1.0.0.md`.
