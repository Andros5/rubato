# Rubato

Rubato is a scheduler for AI workloads that share one budget of requests, tokens or
money. It decides which work starts now, which waits, **why**, and **when it will be
reconsidered**, and it shows what a policy change costs and to whom.

It ships as two things that use the same decision function (`rubato.core.decidir`):

- a **credential-free simulator** to compare policies on a trace before touching a
  provider, with a self-contained HTML report;
- an **admission service** (FastAPI + PostgreSQL) that hands out permits to several
  applications, with transactional accounting, idempotent operations and recovery of
  uncertain attempts.

Everything distributed here is synthetic. The provider used by the demo is a local
simulator: no keys, no cost.

## Install the simulator

```powershell
py -3.13 -m venv .venv
.\.venv\Scripts\python.exe -m pip install rubato-1.1.0-py3-none-any.whl   # or: pip install -e ".[dev]" from a clone
.\.venv\Scripts\rubato.exe init-demo .            # copies examples/ (synthetic trace, scenarios, frozen policies)
.\.venv\Scripts\rubato.exe simulate --trace examples/demo.jsonl --scenario urgencia-tardia --policies radar,reserva --out runs/demo
.\.venv\Scripts\rubato.exe compare --run runs/demo --report informe.html
.\.venv\Scripts\rubato.exe explain --run runs/demo --policy radar --job candidatura-urgente
```

`simulate` prints one line per policy (urgent jobs on time, background done and
pending, p95 wait). `compare` writes an offline HTML page with two timelines, filters,
a decision card per job, quota and backlog charts and a per-objective summary that
never averages objectives together. `explain` prints every decision about one job:

```
radar: candidatura-urgente (radar/application) arrived 17:15:00 UTC [hypothetical], deadline 18:00:00 UTC
  17:15:00 UTC cv#1 DEFER [capacity_exhausted] rpd: needs 1, 0 available; reconsider 08:00:00 UTC
  result: pending
```

Under the reserve policy the same application finishes on time, and a background
analysis explains its own wait:

```
reserva: match-05-017 (radar/match) arrived 16:28:44 UTC [synthetic]
  16:28:44 UTC match#1 DEFER [deadline_reserve] rpm: needs 1, 0 available; rpd: needs 1, 4 available but 4 held for urgent work; reconsider 18:30:00 UTC
  18:30:00 UTC match#1 DEFER [capacity_exhausted] rpd: needs 1, 0 available; reconsider 08:00:00 UTC
  result: pending
```

## Run the service

```powershell
$env:RUBATO_TEST_CLOCK = '1'          # test/demo mode: enables the reset and clock endpoints
docker compose up --build -d          # PostgreSQL, API, planner, simulated provider
.\.venv\Scripts\python.exe -m pip install -e ".[service]"
.\.venv\Scripts\rubato.exe demo-service   # two clients share one quota; an urgent job arrives late
```

Without `RUBATO_TEST_CLOCK=1` the stack starts in normal mode (no reset or clock
endpoints): that is the mode an application in `shadow` or `enforce` should talk to.

Open **http://127.0.0.1:8000/panel** (administrator token) for the live panel: quota per
resource, queue and waiting reasons per client, latest decisions, uncertain attempts with
one-click reconciliation, the active policy, and the `off` / `shadow` / `enforce` switch of
each client. Applications started with mode `auto` follow that switch within 30 s.
`rubato shadow-report <file>` summarises a shadow phase (agreement, divergences, added latency).
In a terminal: `rubato status` (modes, quota, waiting, alerts) and `rubato why radar:<id>`
(every decision about a live job). Optional start at logon: `scripts/ops/arranque_windows.ps1`.

Clients register jobs, ask for a permit (`POST /v1/claims`), confirm the start before
calling the provider and settle the real usage afterwards. Each client can run in
`off`, `shadow` or `enforce` mode (`rubato.sdk.Gate`). See `docs/referencia.md`.

## What the results say

On the synthetic day, reserving 4 requests of the daily quota saves a late urgent
application in 5 of 7 scenarios and costs 1–2 background jobs; on a day without
urgency it only adds background wait. On 13 days of the author's real history the
reserve **tied** the reference policy (the urgent misses there were invalid outputs and
long retries, not quota), so the reference stays the default. Details and limits:
`docs/evaluacion.md`.

## Documentation

| Document | Content |
|---|---|
| `docs/guia.md` | Short guide in Spanish |
| `docs/referencia.md` | CLI and HTTP API reference |
| `docs/trazas.md` | Trace format `rubato.trace/v1`, provenance, capture |
| `docs/decisiones.md`, `docs/adr/` | Design decisions and the three architecture records |
| `docs/evaluacion.md` | Scenario results, date-block evaluation, when the reserve did worse |
| `docs/servicio.md`, `docs/operacion.md` | Service flow, accounting, runbook, drills |
| `docs/integraciones.md` | Adapters for Swiss Job Radar and CitaLab (separate branches) |
| `docs/demo.md` | Two-minute narrated demo |
| `CHANGELOG.md`, `docs/release-v1.0.0.md` | Release notes and what was verified where |

## Tests

```powershell
.\.venv\Scripts\python.exe -m pytest -q                       # simulator, no network
$env:RUBATO_TEST_API = 'http://127.0.0.1:8000'               # stack started with RUBATO_TEST_CLOCK=1
.\.venv\Scripts\python.exe -m pytest -q -m postgres          # service on real PostgreSQL
```

License: MIT.
