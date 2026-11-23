"""Restore drill: dump the Compose database, restore it into a disposable PostgreSQL,
start a fresh API on the copy and check that traceability survives and decisions resume.

    python scripts/ops/restore_drill.py        (needs: docker compose up -d)

Creates state first: a finished job, an uncertain (unknown) attempt and a ready job.
Prints a JSON report; exit code 0 only if every check passes. Cleans up its containers.
"""

from __future__ import annotations

import json
import subprocess
import sys
import tempfile
import time
from pathlib import Path

import httpx

API = "http://127.0.0.1:8000"
TOK = {"admin": "admin-local-token", "radar": "radar-local-token"}
PG_IMAGE = "postgres:17-alpine"
DSN_PW = "rubato-local-only"


def sh(*args, **kw):
    return subprocess.run(list(args), check=True, capture_output=True, **kw)


def c(who, base=API):
    return httpx.Client(base_url=base, headers={"Authorization": f"Bearer {TOK[who]}"}, timeout=30)


def wait(url, seconds=90):
    for _ in range(seconds):
        try:
            if httpx.get(url, timeout=2).status_code == 200:
                return True
        except httpx.HTTPError:
            pass
        time.sleep(1)
    return False


def main() -> int:
    a, r = c("admin"), c("radar")
    a.post("/v1/admin/reset").raise_for_status()
    a.put(
        "/v1/admin/resources", json=[{"id": "quota", "unit": "requests", "capacity": 10, "window": "total"}]
    ).raise_for_status()
    for jid in ("done-job", "uncertain-job", "waiting-job"):
        r.post(
            "/v1/jobs",
            json={"id": jid, "class": "match", "steps": [{"id": "s", "estimate": {"requests": 1}}]},
        )
    p = r.post("/v1/claims", json={"job": "done-job", "step": "s"}).json()["permit"]
    r.post(f"/v1/attempts/{p['attempt']}/start")
    r.post(f"/v1/attempts/{p['attempt']}/finish", json={"status": "ok", "usage": {"requests": 1}})
    p2 = r.post("/v1/claims", json={"job": "uncertain-job", "step": "s"}).json()["permit"]
    r.post(f"/v1/attempts/{p2['attempt']}/start")
    a.post("/v1/admin/clock", json={"advance": 601})
    a.post("/v1/admin/schedule")  # detects the uncertain attempt
    before = {
        "decisions": len(a.get("/v1/decisions", params={"limit": 10000}).json()),
        "unknown": [x["id"] for x in a.get("/v1/admin/attempts", params={"state": "unknown"}).json()],
        "usage": a.get("/v1/usage").json()["resources"][0],
    }
    t0 = time.monotonic()
    dump = Path(tempfile.mkdtemp()) / "rubato.dump"
    with dump.open("wb") as fh:
        subprocess.run(
            ["docker", "compose", "exec", "-T", "db", "pg_dump", "-U", "rubato", "-Fc", "rubato"],
            check=True,
            stdout=fh,
        )
    t_dump = time.monotonic() - t0
    pg, api = "rubato-restore-pg", "rubato-restore-api"
    subprocess.run(["docker", "rm", "-f", pg, api], capture_output=True)
    try:
        sh(
            "docker",
            "run",
            "-d",
            "--name",
            pg,
            "-e",
            "POSTGRES_USER=rubato",
            "-e",
            f"POSTGRES_PASSWORD={DSN_PW}",
            "-e",
            "POSTGRES_DB=rubato",
            "-p",
            "127.0.0.1:55433:5432",
            PG_IMAGE,
        )
        for _ in range(60):
            if (
                subprocess.run(
                    ["docker", "exec", pg, "pg_isready", "-U", "rubato"], capture_output=True
                ).returncode
                == 0
            ):
                break
            time.sleep(1)
        time.sleep(2)
        t1 = time.monotonic()
        subprocess.run(
            ["docker", "exec", "-i", pg, "pg_restore", "-U", "rubato", "-d", "rubato", "--no-owner"],
            check=True,
            stdin=dump.open("rb"),
            capture_output=True,
        )
        t_restore = time.monotonic() - t1
        sh(
            "docker",
            "run",
            "-d",
            "--name",
            api,
            "-p",
            "127.0.0.1:8002:8000",
            "-e",
            f"RUBATO_DSN=postgresql://rubato:{DSN_PW}@host.docker.internal:55433/rubato",
            "-e",
            "RUBATO_TEST_CLOCK=1",
            "rubato:1.1.0",
        )
        assert wait("http://127.0.0.1:8002/readyz"), "restored API not ready"
        a2, r2 = c("admin", "http://127.0.0.1:8002"), c("radar", "http://127.0.0.1:8002")
        after = {
            "decisions": len(a2.get("/v1/decisions", params={"limit": 10000}).json()),
            "unknown": [x["id"] for x in a2.get("/v1/admin/attempts", params={"state": "unknown"}).json()],
            "usage": a2.get("/v1/usage").json()["resources"][0],
            "done_job": r2.get("/v1/jobs/done-job").json()["state"],
        }
        resumed = r2.post("/v1/claims", json={"job": "waiting-job", "step": "s"}).json()["permit"]
        rec = r2.post(
            f"/v1/attempts/{p2['attempt']}/reconcile",
            json={"consumed": True, "usage": {"requests": 1}, "receipt": "manual-check"},
        ).json()
        report = {
            "dump_s": round(t_dump, 2),
            "restore_s": round(t_restore, 2),
            "dump_bytes": dump.stat().st_size,
            "before": before,
            "after": after,
            "decisions_resume": bool(resumed),
            "uncertain_reconciled": rec.get("state"),
            "checks": {
                "decisions_preserved": after["decisions"] == before["decisions"],
                "uncertain_still_visible": after["unknown"] == before["unknown"]
                and len(after["unknown"]) == 1,
                "balance_preserved": after["usage"] == before["usage"],
                "finished_job_preserved": after["done_job"] == "done",
                "decisions_resume": bool(resumed),
                "uncertain_reconciled": rec.get("state") == "reconciled",
            },
        }
    finally:
        subprocess.run(["docker", "rm", "-f", pg, api], capture_output=True)
    print(json.dumps(report, indent=1))
    return 0 if all(report["checks"].values()) else 1


if __name__ == "__main__":
    sys.exit(main())
