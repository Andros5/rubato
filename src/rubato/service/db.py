"""PostgreSQL connection helpers and the migration runner."""

from __future__ import annotations

import os
from importlib import resources as ires

import psycopg
from psycopg.rows import dict_row

MIGRATIONS_PKG = "rubato.service.migrations"
# Early 1.2.0 installations used these filenames before provider support took 0004.
LEGACY_NAMES = {
    "0005_scheduler_status.sql": "0004_scheduler_status.sql",
    "0006_budgets_and_turns.sql": "0005_budgets_and_turns.sql",
    "0007_policy_revisions.sql": "0006_policy_revisions.sql",
    "0008_observation.sql": "0007_observation.sql",
    "0009_incident_and_deliveries.sql": "0008_incident_and_deliveries.sql",
}


def dsn() -> str:
    return os.environ.get("RUBATO_DSN", "postgresql://rubato:rubato@localhost:5432/rubato")


def connect(url: str | None = None) -> psycopg.Connection:
    return psycopg.connect(url or dsn(), row_factory=dict_row, autocommit=False)


def migration_files() -> list[tuple[str, str]]:
    files = sorted(p for p in ires.files(MIGRATIONS_PKG).iterdir() if p.name.endswith(".sql"))
    return [(p.name, p.read_text(encoding="utf-8")) for p in files]


def migrate(conn: psycopg.Connection, upto: str | None = None) -> list[str]:
    """Apply pending migrations in order inside one transaction each. Returns applied names."""
    with conn.transaction():
        conn.execute(
            "SELECT pg_advisory_xact_lock(7342001)"
        )  # concurrent workers/replicas migrate one at a time
        conn.execute(
            "CREATE TABLE IF NOT EXISTS schema_migrations (name text PRIMARY KEY, applied_at timestamptz DEFAULT now())"
        )
    done = {r["name"] for r in conn.execute("SELECT name FROM schema_migrations").fetchall()}
    conn.commit()
    applied = []
    for name, sql in migration_files():
        if name in done:
            continue
        if upto is not None and name > upto:
            break
        with conn.transaction():
            conn.execute("SELECT pg_advisory_xact_lock(7342001)")
            if conn.execute("SELECT 1 FROM schema_migrations WHERE name=%s", (name,)).fetchone():
                continue
            if name in LEGACY_NAMES:
                # Preserve the old entry for rollback and carry its original application time forward.
                alias = conn.execute(
                    "INSERT INTO schema_migrations (name, applied_at) "
                    "SELECT %s, applied_at FROM schema_migrations WHERE name=%s RETURNING name",
                    (name, LEGACY_NAMES[name]),
                ).fetchone()
                if alias:
                    continue
            conn.execute(sql)
            conn.execute("INSERT INTO schema_migrations (name) VALUES (%s)", (name,))
        applied.append(name)
    return applied


def schema_version(conn: psycopg.Connection) -> str | None:
    r = conn.execute("SELECT max(name) AS v FROM schema_migrations").fetchone()
    conn.commit()
    return r["v"] if r else None
