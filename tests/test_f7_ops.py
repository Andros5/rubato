"""F7: schema migration from the previous version, metrics without per-job labels, readiness."""

from __future__ import annotations

import os
import uuid

import httpx
import psycopg
import pytest
from service_helpers import API, client, job, needs_service, ok, reset

from rubato.service import db, store

pytestmark = [pytest.mark.postgres]
DSN = os.environ.get("RUBATO_TEST_DSN")  # superuser DSN of a disposable PostgreSQL (Compose db)


@pytest.mark.skipif(not DSN, reason="set RUBATO_TEST_DSN to a disposable PostgreSQL")
def test_migration_from_previous_schema_keeps_data():
    name = f"mig_{uuid.uuid4().hex[:8]}"
    with psycopg.connect(DSN, autocommit=True) as admin:
        admin.execute(f"CREATE DATABASE {name}")
    url = DSN.rsplit("/", 1)[0] + "/" + name
    try:
        with db.connect(url) as conn:
            assert db.migrate(conn, upto="0001_init.sql") == ["0001_init.sql"]
            with conn.transaction():  # rows as the previous version wrote them
                store.bootstrap_clients(conn, "radar:tok")
                conn.execute(
                    "INSERT INTO jobs (id, client_id, client_job_id, cls, arrival, created_at) "
                    "VALUES ('radar:before-upgrade', 'radar', 'before-upgrade', 'match', 1, 1)"
                )
                conn.execute(
                    "INSERT INTO steps (job_id, step_id, estimate, state) "
                    "VALUES ('radar:before-upgrade', 's', '{\"requests\": 1}', 'ready')"
                )
            assert db.schema_version(conn) == "0001_init.sql"
            assert db.migrate(conn) == [
                "0002_meta_and_audit_idx.sql",
                "0003_client_mode.sql",
                "0004_step_provider.sql",
            ]
            row = conn.execute("SELECT id, state, meta FROM jobs").fetchone()
            assert row == {"id": "radar:before-upgrade", "state": "active", "meta": {}}
            assert db.migrate(conn) == []  # idempotent
            assert db.schema_version(conn) == "0004_step_provider.sql"
    finally:
        with psycopg.connect(DSN, autocommit=True) as admin:
            admin.execute(f"DROP DATABASE {name} WITH (FORCE)")


@needs_service
def test_metrics_cover_queue_permits_reasons_and_ledger_without_job_labels():
    reset([{"id": "quota", "unit": "requests", "capacity": 1, "window": "total"}])
    with client("radar") as c:
        ok(c.post("/v1/jobs", json=job("metric-a", meta={"ref_kind": "job"})), 201)
        ok(c.post("/v1/jobs", json=job("metric-b")), 201)
        p = ok(c.post("/v1/claims", json={}))["permit"]
        ok(c.post(f"/v1/attempts/{p['attempt']}/start"))
    text = httpx.get(f"{API}/metrics").text
    for name in (
        "rubato_steps",
        "rubato_queue_oldest_ready_age_seconds",
        "rubato_attempts",
        "rubato_grant_to_start_seconds_avg",
        "rubato_decisions",
        "rubato_ledger_amount",
        "rubato_http_requests",
    ):
        assert f"# TYPE {name}" in text
    assert 'reason="capacity_exhausted"' in text
    assert "metric-a" not in text and "metric-b" not in text and "job=" not in text
    assert ok(httpx.get(f"{API}/readyz"))["schema"] == "0004_step_provider.sql"


@needs_service
def test_correlation_id_is_echoed():
    r = httpx.get(f"{API}/healthz", headers={"X-Correlation-Id": "corr-123"})
    assert r.headers["X-Correlation-Id"] == "corr-123"


@pytest.mark.skipif(not DSN, reason="set RUBATO_TEST_DSN to a disposable PostgreSQL")
def test_upgraded_database_keeps_scheduling_old_jobs():
    name = f"mig_{uuid.uuid4().hex[:8]}"
    with psycopg.connect(DSN, autocommit=True) as admin:
        admin.execute(f"CREATE DATABASE {name}")
    url = DSN.rsplit("/", 1)[0] + "/" + name
    try:
        with db.connect(url) as conn:
            db.migrate(conn, upto="0001_init.sql")
            with conn.transaction():
                store.bootstrap_clients(conn, "radar:tok")
                conn.execute(
                    "INSERT INTO jobs (id, client_id, client_job_id, cls, arrival, created_at) "
                    "VALUES ('radar:old', 'radar', 'old', 'match', 1, 1)"
                )
                conn.execute(
                    "INSERT INTO steps (job_id, step_id, estimate, state, ready_since) "
                    "VALUES ('radar:old', 's', '{\"requests\": 1}', 'ready', 1)"
                )
            db.migrate(conn)
            with conn.transaction():
                c = conn.execute("SELECT * FROM clients WHERE id='radar'").fetchone()
                r = store.claim(conn, c)
            assert r["permit"]["job"] == "radar:old"
    finally:
        with psycopg.connect(DSN, autocommit=True) as admin:
            admin.execute(f"DROP DATABASE {name} WITH (FORCE)")


@pytest.mark.skipif(not DSN, reason="set RUBATO_TEST_DSN to a disposable PostgreSQL")
def test_maintenance_expires_old_idempotency_keys_only():
    name = f"mig_{uuid.uuid4().hex[:8]}"
    with psycopg.connect(DSN, autocommit=True) as admin:
        admin.execute(f"CREATE DATABASE {name}")
    url = DSN.rsplit("/", 1)[0] + "/" + name
    try:
        with db.connect(url) as conn:
            db.migrate(conn)
            with conn.transaction():
                t = store.now(conn)
                for key, age in (("old", 8 * 86400), ("new", 3600)):
                    conn.execute(
                        "INSERT INTO idempotency VALUES ('radar', %s, 'jobs', 'h', 201, '{}', %s)",
                        (key, t - age),
                    )
                assert store.maintenance(conn) == {"idempotency_deleted": 1}
            assert [r["key"] for r in conn.execute("SELECT key FROM idempotency").fetchall()] == ["new"]
    finally:
        with psycopg.connect(DSN, autocommit=True) as admin:
            admin.execute(f"DROP DATABASE {name} WITH (FORCE)")
