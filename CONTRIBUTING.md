# Contributing to Rubato

Thanks for your interest. Rubato is small on purpose: one decision function shared by a
simulator and a service, with every claim backed by a test. Contributions that keep it
that way are very welcome.

## Set up

```bash
python -m venv .venv
. .venv/bin/activate            # Windows: .\.venv\Scripts\activate
pip install -e ".[dev]"
pytest -q                       # simulator, CLI, report, SDK: no network, no keys
```

Run service and migration tests on the separate Compose trial project.

```bash
(
  set -e
  export RUBATO_ENVIRONMENT=entrevista-ensayo
  export RUBATO_API_PORT=18800 RUBATO_API2_PORT=18801 RUBATO_PROVIDER_PORT=18810 RUBATO_DB_PORT=56532
  compose=(docker compose --env-file config/entrevista-compose.example.vars -p rubato-entrevista-ensayo --profile ha --profile demo)
  "${compose[@]}" build api
  trap '"${compose[@]}" stop' EXIT
  "${compose[@]}" up -d --wait --wait-timeout 90
  RUBATO_TEST_API=http://127.0.0.1:18800 \
  RUBATO_TEST_PROVIDER=http://127.0.0.1:18810 \
  RUBATO_TEST_DSN=postgresql://rubato:rubato-demo-only@127.0.0.1:56532/rubato \
  RUBATO_TEST_COMPOSE=1 RUBATO_TEST_ADMIN_TOKEN=demo-admin-local \
  RUBATO_TEST_RADAR_TOKEN=demo-radar-local RUBATO_TEST_CITALAB_TOKEN=demo-citalab-local \
  python -m pytest -q -m postgres
)
```

## Ground rules

- **One decision function.** Policy logic lives in `rubato.core.decidir` and
  `rubato.policies`. The simulator and the service must keep calling the same code.
- **Behaviour first.** Every change comes with a test that shows the behaviour (a
  scenario, a decision, an API response), not only a unit of code.
- **Honest data.** Keep arrival provenance (`observed`, `derived`, `synthetic`,
  `hypothetical`) separate. Distributed examples are synthetic. Never commit private
  traces, databases, prompts or credentials.
- **Stable reasons.** Reason codes (`deadline_reserve`, `capacity_exhausted`, …) are an
  interface: add new ones, never rename existing ones.
- **Reversible integrations.** Client adapters must keep an `off` mode that behaves
  exactly like the application without Rubato.
- **Style.** `ruff check src tests scripts` and `ruff format src tests scripts` must
  pass. CI runs them on Linux and Windows.

## Pull requests

1. Open an issue first for anything larger than a fix, so the design can be agreed.
2. Keep pull requests focused and describe the observable change.
3. Update `CHANGELOG.md` and the relevant document in `docs/`.
4. Record design decisions with their rejected alternative in `docs/decisiones.md` or a
   new ADR in `docs/adr/`.

## Reporting bugs

Use the bug report template. Include the command, the trace or scenario (synthetic if
possible) and the decision you expected. `rubato explain` and `rubato why` usually give
the relevant lines.
