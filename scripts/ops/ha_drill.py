"""Failover drill: two API replicas, two consumers, one replica killed during a reservation
and the other during a simulated provider call.

Compose:   docker compose --profile ha --profile demo up -d   (RUBATO_GRANT_TTL=5 recommended)
           python scripts/ops/ha_drill.py --kill docker:rubato-api2-1,docker:rubato-api-1
Kubernetes: python scripts/ops/ha_drill.py --a http://127.0.0.1:18000 --b http://127.0.0.1:18001 \
           --kill kubectl:rubato/api-0,kubectl:rubato/api-1 --provider http://127.0.0.1:18100

Measures: time until the interrupted work is served again, duplicate permits/starts,
balance discrepancy between Rubato's confirmed ledger and the provider receipts, and
claim latency under load. Prints one JSON report.
"""

from __future__ import annotations

import argparse
import json
import os
import platform
import statistics
import subprocess
import threading
import time

import httpx

TOK = {  # Compose defaults; override for other environments (e.g. the kind cluster's secrets.env)
    who: os.environ.get(f"RUBATO_TEST_{who.upper()}_TOKEN", f"{who}-local-token")
    for who in ("admin", "radar", "citalab")
}


def hdr(who):
    return {"Authorization": f"Bearer {TOK[who]}"}


def kill(target: str) -> None:
    kind, name = target.split(":", 1)
    if kind == "docker":
        subprocess.run(["docker", "kill", name], check=True, capture_output=True)
    elif kind == "kubectl":  # kubectl:<namespace>/<label selector>#<index>
        ns, rest = name.split("/", 1)
        selector, _, idx = rest.partition("#")
        pods = subprocess.run(
            [
                "kubectl",
                "-n",
                ns,
                "get",
                "pods",
                "-l",
                selector,
                "--field-selector=status.phase=Running",
                "-o",
                "jsonpath={.items[*].metadata.name}",
            ],
            check=True,
            capture_output=True,
            text=True,
        ).stdout.split()
        pod = sorted(pods)[int(idx or 0)]
        subprocess.run(
            ["kubectl", "-n", ns, "delete", "pod", pod, "--grace-period=0", "--force", "--wait=false"],
            check=True,
            capture_output=True,
        )
    else:
        raise ValueError(target)


def revive(target: str) -> None:
    kind, name = target.split(":", 1)
    if kind == "docker":
        subprocess.run(["docker", "start", name], check=True, capture_output=True)
    # Kubernetes recreates deleted pods by itself


class Consumer:
    def __init__(self, who: str, urls: list[str], provider: str):
        self.who, self.urls, self.provider = who, urls, provider
        self.latencies: list[float] = []
        self.failovers = 0

    def req(self, method, path, **kw):
        last = None
        for _ in range(60):
            for url in self.urls:
                try:
                    t0 = time.perf_counter()
                    r = httpx.request(method, url + path, headers=hdr(self.who), timeout=10, **kw)
                    if path == "/v1/claims":
                        self.latencies.append((time.perf_counter() - t0) * 1000)
                    if r.status_code >= 500:
                        raise httpx.HTTPError(f"{r.status_code}")
                    return r
                except httpx.HTTPError as e:
                    last = e
                    self.failovers += 1
            time.sleep(0.5)
        raise RuntimeError(f"no replica answered: {last}")

    def run_job(self, jid: str, before_start=None, during_call=None) -> dict:
        self.req(
            "POST",
            "/v1/jobs",
            json={
                "id": jid,
                "class": "match",
                "steps": [
                    {"id": "s", "model": "sim-flash", "estimate": {"requests": 1}, "maximum": {"requests": 1}}
                ],
            },
        )
        t_first = time.monotonic()
        served_after_kill = None
        t_kill = None
        while True:
            p = self.req("POST", "/v1/claims", json={"job": jid, "step": "s", "slots": 4}).json()["permit"]
            if not p:
                time.sleep(0.5)
                continue
            if before_start:
                before_start()
                before_start = None
                t_kill = time.monotonic()
                time.sleep(0.2)
                continue  # the worker "died" holding this permit; a new claim must recover the step
            if served_after_kill is None and t_kill is not None:
                served_after_kill = time.monotonic() - t_kill
            s = self.req("POST", f"/v1/attempts/{p['attempt']}/start")
            if s.status_code == 409:
                continue
            body = httpx.post(
                f"{self.provider}/v1/generate",
                json={"key": p["attempt"], "model": "sim-flash", "tokens_in": 100, "max_tokens_out": 50},
            ).json()
            if during_call:
                during_call()
                during_call = None
            f = self.req(
                "POST",
                f"/v1/attempts/{p['attempt']}/finish",
                json={"status": "ok", "usage": body["usage"], "receipt": body["receipt"]},
            )
            assert f.status_code == 200, f.text
            return {
                "job": jid,
                "attempt": p["attempt"],
                "s": time.monotonic() - t_first,
                "served_again_after_kill_s": served_after_kill,
            }


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--a", default="http://127.0.0.1:8000")
    ap.add_argument("--b", default="http://127.0.0.1:8001")
    ap.add_argument("--provider", default="http://127.0.0.1:8100")
    ap.add_argument("--kill", default="docker:rubato-api2-1,docker:rubato-api-1")
    ap.add_argument("--jobs", type=int, default=40)
    a = ap.parse_args()
    kill_b, kill_a = a.kill.split(",")
    admin = httpx.Client(base_url=a.a, headers=hdr("admin"), timeout=30)
    admin.post("/v1/admin/reset").raise_for_status()
    admin.put(
        "/v1/admin/resources",
        json=[
            {"id": "quota", "unit": "requests", "capacity": 100000, "window": "total"},
            {"id": "rpm", "unit": "requests", "capacity": 600, "window": "rolling", "length": 60},
        ],
    ).raise_for_status()
    receipts_before = len(httpx.get(f"{a.provider}/v1/receipts").json())
    c1 = Consumer("radar", [a.a, a.b], a.provider)
    c2 = Consumer("citalab", [a.b, a.a], a.provider)
    results: dict[str, list] = {"radar": [], "citalab": []}
    t0 = time.monotonic()
    tag = f"ha{int(time.time())}"

    def loop(c: Consumer, n: int, special: dict):
        for k in range(n):
            results[c.who].append(c.run_job(f"{tag}-{c.who}-{k}", **special.get(k, {})))

    events = {}
    sp2 = {
        a.jobs // 4: {
            "before_start": lambda: (
                kill(kill_b),
                events.setdefault("killed_b_during_reservation", time.monotonic() - t0),
            )
        }
    }
    sp1 = {
        a.jobs // 2: {
            "during_call": lambda: (
                revive(kill_b),
                time.sleep(3),
                kill(kill_a),
                events.setdefault("killed_a_during_call", time.monotonic() - t0),
            )
        }
    }
    th = [
        threading.Thread(target=loop, args=(c1, a.jobs, sp1)),
        threading.Thread(target=loop, args=(c2, a.jobs, sp2)),
    ]
    for t in th:
        t.start()
    for t in th:
        t.join()
    revive(kill_a)
    for _ in range(60):
        try:
            if httpx.get(f"{a.a}/readyz", timeout=2).status_code == 200:
                break
        except httpx.HTTPError:
            time.sleep(1)
    admin = httpx.Client(base_url=a.a, headers=hdr("admin"), timeout=30)
    usage = admin.get("/v1/usage").json()
    quota = next(r for r in usage["resources"] if r["id"] == "quota")
    receipts = len(httpx.get(f"{a.provider}/v1/receipts").json()) - receipts_before
    started = admin.get("/v1/admin/attempts", params={"state": "finished"}).json()
    per_step: dict = {}
    for at in started:
        per_step[(at["job_id"], at["step_id"])] = per_step.get((at["job_id"], at["step_id"]), 0) + 1
    lat = c1.latencies + c2.latencies
    report = {
        "env": {"host": platform.platform(), "python": platform.python_version(), "replicas": [a.a, a.b]},
        "jobs": {k: len(v) for k, v in results.items()},
        "events_s": {k: round(v, 2) for k, v in events.items()},
        "served_again_after_kill_s": [
            round(r["served_again_after_kill_s"], 2)
            for r in results["citalab"]
            if r["served_again_after_kill_s"] is not None
        ],
        "duplicate_finished_attempts_per_step": sum(1 for v in per_step.values() if v > 1),
        "rubato_confirmed_requests": quota["used"],
        "provider_receipts": receipts,
        "balance_discrepancy": quota["used"] - receipts,
        "unknown_attempts": quota["unknown"],
        "claim_latency_ms": {
            "n": len(lat),
            "p50": round(statistics.median(lat), 1),
            "p95": round(sorted(lat)[int(0.95 * (len(lat) - 1))], 1),
        },
        "failovers": c1.failovers + c2.failovers,
        "wall_s": round(time.monotonic() - t0, 1),
    }
    print(json.dumps(report, indent=1))
    ok = report["duplicate_finished_attempts_per_step"] == 0 and report["balance_discrepancy"] == 0
    return 0 if ok else 1


if __name__ == "__main__":
    raise SystemExit(main())
