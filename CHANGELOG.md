# Changelog

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
