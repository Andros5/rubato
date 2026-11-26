"""Client-side claim benchmark for the isolated Compose project."""

from __future__ import annotations

import argparse
import os
import platform
import time
from collections import Counter
from concurrent.futures import ThreadPoolExecutor
from datetime import UTC, datetime
from pathlib import Path

import httpx

from rubato import __version__
from rubato.drill import verify_effects
from rubato.local import LocalError, LocalStack, probe
from rubato.metrics import pct
from rubato.runs import json_file


def distribution(values):
    return {"n": len(values), "p50": pct(values, 0.5), "p95": pct(values, 0.95)}


def measure(admin, provider, owner_clients, count, jobs, warmup, policy):
    step = {"id": "request", "model": "sim-flash", "estimate": {"requests": 1}}
    admin.post("/v1/admin/reset").raise_for_status()
    all_jobs = count * (warmup + jobs)
    admin.put(
        "/v1/admin/resources",
        json=[
            {"id": "shared", "unit": "requests", "window": "total", "capacity": all_jobs},
        ],
    ).raise_for_status()
    config = {"name": policy}
    if policy == "fair":
        config.update({"resource": "shared", "weights": {}})
    admin.put("/v1/admin/policy", json=config).raise_for_status()
    previous = {r["key"] for r in provider.get("/v1/receipts").raise_for_status().json()}
    receipts_expected = {}

    def submit(i, n):
        c = owner_clients[i]
        name = f"bench-{n}"
        c.post("/v1/jobs", json={"id": name, "class": "bench", "steps": [step]}).raise_for_status()
        return time.perf_counter()

    def execute(i, start_id, length, check):
        c = owner_clients[i]
        claims, waits, finished = [], [], []
        for n in range(start_id, start_id + length):
            name = f"bench-{n}"
            expires = time.monotonic() + 60
            while True:
                t0 = time.perf_counter()
                result = c.post("/v1/claims", json={"job": name, "step": "request"}).raise_for_status().json()
                claims.append(round((time.perf_counter() - t0) * 1000, 3))
                if permit := result.get("permit"):
                    break
                if time.monotonic() > expires:
                    raise RuntimeError(f"timed out waiting for client {i}, job {n}")
                time.sleep(0.02)
            if check:
                waits.append(round((time.perf_counter() - check[i, n]) * 1000, 3))
            aid = permit["attempt"]
            c.post(f"/v1/attempts/{aid}/start").raise_for_status()
            outcome = (
                provider.post("/v1/generate", json={"key": aid, "model": "sim-flash"})
                .raise_for_status()
                .json()
            )
            c.post(
                f"/v1/attempts/{aid}/finish",
                json={
                    "status": "ok",
                    "usage": outcome["usage"],
                    "receipt": outcome["receipt"],
                },
            ).raise_for_status()
            finished.append((aid, (i, n)))
        return claims, waits, finished

    with ThreadPoolExecutor(max_workers=count) as pool:
        for i in range(count):
            for n in range(warmup):
                submit(i, n)
        for result in pool.map(lambda i: execute(i, 0, warmup, None), range(count)):
            receipts_expected.update(result[2])
        queued = {(i, n): submit(i, n) for n in range(warmup, warmup + jobs) for i in range(count)}
        begin = time.perf_counter()
        results = list(pool.map(lambda i: execute(i, warmup, jobs, queued), range(count)))
        elapsed = time.perf_counter() - begin
    for _, _, finished in results:
        receipts_expected.update(finished)
    duplicates = Counter(receipts_expected.values())
    receipts = [r for r in provider.get("/v1/receipts").raise_for_status().json() if r["key"] not in previous]
    usage = admin.get("/v1/usage").raise_for_status().json()["resources"][0]
    accounting = verify_effects(receipts_expected, receipts, usage["used"], usage["unknown"])
    pending = all_jobs - len(receipts_expected)
    duplicated = sum(n - 1 for n in duplicates.values() if n > 1)
    return {
        "clients": count,
        "jobs_per_client": jobs,
        "warmup_jobs_per_client": warmup,
        "window_s": round(elapsed, 3),
        "claim_ms": distribution([v for calls, _, _ in results for v in calls]),
        "client_wait_ms": {f"client-{i + 1}": distribution(waits) for i, (_, waits, _) in enumerate(results)},
        "pending": pending,
        "duplicates_per_step": duplicated,
        "accounting": accounting,
        "passed": not pending and not duplicated and not any(accounting.values()),
    }


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--out", type=Path, default=Path("runs/benchmarks/claims.json"))
    parser.add_argument("--jobs", type=int, default=5)
    parser.add_argument("--warmup", type=int, default=1)
    parser.add_argument("--repetitions", type=int, default=3)
    parser.add_argument("--policy", choices=("radar", "fair"), default="fair")
    args = parser.parse_args()
    if not 1 <= args.jobs <= 100 or not 0 <= args.warmup <= 20 or not 1 <= args.repetitions <= 10:
        parser.error("invalid benchmark size")
    stack = LocalStack(Path.cwd(), trial=True).resolve()
    running = any(r["State"] == "running" for r in stack.containers(verify=True).values())
    config, ports = stack.config()
    stack.change(True)
    stack.containers(verify=True)
    url = f"http://127.0.0.1:{ports['api']}"
    report = {
        "environment": stack.project,
        "timestamp": datetime.now(UTC).isoformat(),
        "rubato": __version__,
        "host": platform.platform(),
        "cpus": os.cpu_count(),
        "python": platform.python_version(),
        "docker": stack._docker(["info", "--format", "{{.ServerVersion}}"]).strip(),
        "policy": args.policy,
        "results": [],
    }
    peers = []
    try:
        if probe(ports["api"], "/readyz").get("environment") != "entrevista-ensayo" or (
            probe(ports["provider"], "/healthz").get("provider") != "rubato-simulated"
        ):
            raise LocalError("unverified benchmark targets")
        admin_token = config["api"]["environment"]["RUBATO_CLIENTS"].split(",")[0].split(":")[1]
        with (
            httpx.Client(
                base_url=url, headers={"Authorization": f"Bearer {admin_token}"}, timeout=30, trust_env=False
            ) as admin,
            httpx.Client(
                base_url=f"http://127.0.0.1:{ports['provider']}", timeout=30, trust_env=False
            ) as provider,
        ):
            for i in range(20):
                new = (
                    admin.post("/v1/admin/clients", json={"id": f"bench-{i + 1}", "slots": 1})
                    .raise_for_status()
                    .json()
                )
                port = ports["api"] if i % 2 == 0 else ports["api2"]
                peers.append(
                    httpx.Client(
                        base_url=f"http://127.0.0.1:{port}",
                        headers={"Authorization": f"Bearer {new['token']}"},
                        timeout=30,
                        trust_env=False,
                    )
                )
            for count in (1, 5, 20):
                for repeat in range(1, args.repetitions + 1):
                    report["results"].append(
                        {
                            "repeat": repeat,
                            **measure(
                                admin,
                                provider,
                                peers,
                                count,
                                args.jobs,
                                args.warmup,
                                args.policy,
                            ),
                        }
                    )
                    args.out.parent.mkdir(parents=True, exist_ok=True)
                    json_file(args.out, report)
    finally:
        for client in peers:
            client.close()
        if not running:
            stack.change(False)
    return int(not all(r["passed"] for r in report["results"]))


if __name__ == "__main__":
    raise SystemExit(main())
