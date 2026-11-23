# Rubato

Shared-budget scheduler for AI workloads. Several applications share a request,
token or money budget; Rubato decides which work starts now, which waits, when it
will be reconsidered, and what a policy change costs. The first deliverable is a
credential-free simulator.

```powershell
py -3.13 -m venv .venv
.\.venv\Scripts\python.exe -m pip install -e ".[dev]"
.\.venv\Scripts\python.exe -m pytest -q
```

See `docs/` for design decisions. Status and evidence: `ESTADO.md` (Spanish).
