"""Competition and receipt reconciliation in a dedicated interview stack."""

from __future__ import annotations

import platform
import time
from collections import Counter
from concurrent.futures import ThreadPoolExecutor
from datetime import UTC, datetime
from pathlib import Path

import httpx

from . import __version__
from .local import LocalError, LocalStack, probe
from .runs import json_file


def verify_effects(expected, receipts, balance, unknown):
    keys = Counter(row["key"] for row in receipts)
    return {
        "duplicate_effects": sum(n - 1 for n in keys.values() if n > 1),
        "missing_effects": sorted(set(expected) - keys.keys()),
        "unexpected_effects": sorted(keys.keys() - set(expected)),
        "balance_discrepancy": balance - sum(r["usage"]["requests"] for r in receipts),
        "unknown_attempts": unknown,
    }


def run_drill(root: Path, out: Path) -> dict:
    stack = LocalStack(root, trial=True).resolve()
    config, ports = stack.config()
    initial = stack.containers(verify=True)
    api_names = ("api", "api2")
    before = {s for s, row in initial.items() if row["State"] == "running"}
    stack.change(True)
    created = datetime.now(UTC).isoformat()
    report = {
        "environment": stack.project,
        "rubato": __version__,
        "timestamp": created,
        "host": platform.platform(),
        "parameters": {"contestants": 20, "expected_calls_per_step": 1},
        "events": [],
        "passed": False,
    }
    started = time.monotonic()

    def event(name, **data):
        report["events"].append({"event": name, "seconds": round(time.monotonic() - started, 3), **data})

    def request(c, method, path, **kwargs):
        response = c.request(method, path, **kwargs)
        response.raise_for_status()
        return response.json()

    def api_client(service, token):
        return httpx.Client(
            base_url=f"http://127.0.0.1:{ports[service]}",
            headers={"Authorization": f"Bearer {token}"},
            timeout=10,
            trust_env=False,
        )

    try:
        rows = stack.containers(verify=True)
        stack.verify_bindings(ports, rows)
        for service in ("db", *api_names, "provider", "scheduler"):
            if rows.get(service, {}).get("State") != "running":
                raise LocalError(f"El ensayo necesita {service} en marcha.")
        for api in api_names:
            readiness = probe(ports[api], "/readyz")
            if readiness.get("environment") != "entrevista-ensayo" or not readiness.get("ok"):
                raise LocalError(f"{api} no pertenece a la pila del ensayo.")
        if probe(ports["provider"], "/healthz").get("provider") != "rubato-simulated":
            raise LocalError("El ensayo necesita el proveedor simulado.")
        tokens = {
            item.split(":")[0]: item.split(":")[1]
            for item in config["api"]["environment"]["RUBATO_CLIENTS"].split(",")
        }
        with (
            api_client("api", tokens["admin"]) as admin,
            api_client("api2", tokens["admin"]) as replica,
            api_client("api", tokens["radar"]) as radar,
            api_client("api2", tokens["radar"]) as radar_replica,
            httpx.Client(
                base_url=f"http://127.0.0.1:{ports['provider']}", timeout=10, trust_env=False
            ) as provider,
        ):
            request(admin, "POST", "/v1/admin/reset")
            resource = {"id": "drill", "unit": "requests", "capacity": 1, "window": "total"}
            request(admin, "PUT", "/v1/admin/resources", json=[resource])
            previous = {r["key"] for r in request(provider, "GET", "/v1/receipts")}
            step = {"id": "s", "model": "sim-flash", "estimate": {"requests": 1}}
            for n in range(20):
                request(
                    radar, "POST", "/v1/jobs", json={"id": f"contest-{n}", "class": "drill", "steps": [step]}
                )

            def claim(n):
                peer = radar if n % 2 else radar_replica
                t = time.monotonic()
                result = request(
                    peer, "POST", "/v1/claims", json={"job": f"contest-{n}", "step": "s", "slots": 20}
                )
                return result["permit"], (time.monotonic() - t) * 1000

            with ThreadPoolExecutor(max_workers=20) as pool:
                contest = list(pool.map(claim, range(20)))
            granted = [permit for permit, _ in contest if permit]
            report["contested_permits"] = len(granted)
            report["claim_ms"] = sorted(round(lat, 2) for _, lat in contest)
            event("contest", submitted=20, granted=len(granted))
            if len(granted) != 1:
                raise LocalError("El último permiso no tuvo un único ganador.")
            winning = granted[0]
            aid = winning["attempt"]
            request(radar, "POST", f"/v1/attempts/{aid}/start")
            receipt = request(provider, "POST", "/v1/generate", json={"key": aid, "model": "sim-flash"})
            event("provider_receipt", attempt=aid, receipt=receipt["receipt"])
            failure_t = time.monotonic()
            stack._docker(["kill", rows["api"]["ID"]])
            event("replica_stopped", service="api")
            request(replica, "POST", "/v1/admin/clock", json={"advance": 15})
            request(replica, "POST", "/v1/admin/schedule")
            pending = request(replica, "GET", "/v1/admin/attempts?state=unknown")
            quota = request(replica, "GET", "/v1/usage")["resources"][0]
            event("attempt_unknown", count=len(pending), held=quota["unknown"])
            if [p["id"] for p in pending] != [aid] or quota["unknown"] != 1:
                raise LocalError("El intento incierto no conservó su reserva.")
            recovered = request(provider, "GET", f"/v1/receipts/{aid}")
            resolved = request(
                replica,
                "POST",
                f"/v1/attempts/{aid}/reconcile",
                json={
                    "consumed": True,
                    "status": "ok",
                    "usage": recovered["usage"],
                    "receipt": recovered["receipt"],
                },
            )
            event("reconciled", attempt=aid, state=resolved["state"])
            # Open one more unit to measure a genuine claim on the surviving replica.
            request(replica, "PUT", "/v1/admin/resources", json=[{**resource, "capacity": 2}])
            other = next(n for n in range(20) if f"radar:contest-{n}" != winning["job"])
            p = request(radar_replica, "POST", "/v1/claims", json={"job": f"contest-{other}", "step": "s"})[
                "permit"
            ]
            if not p:
                raise LocalError("La réplica superviviente no concedió el permiso siguiente.")
            event("work_resumed", attempt=p["attempt"])
            report["recovery_s"] = round(time.monotonic() - failure_t, 3)
            request(radar_replica, "POST", f"/v1/attempts/{p['attempt']}/start")
            result = request(
                provider, "POST", "/v1/generate", json={"key": p["attempt"], "model": "sim-flash"}
            )
            event("provider_receipt", attempt=p["attempt"], receipt=result["receipt"])
            request(
                radar_replica,
                "POST",
                f"/v1/attempts/{p['attempt']}/finish",
                json={
                    "status": "ok",
                    "usage": result["usage"],
                    "receipt": result["receipt"],
                },
            )
            quota = request(replica, "GET", "/v1/usage")["resources"][0]
            receipts = [r for r in request(provider, "GET", "/v1/receipts") if r["key"] not in previous]
            report["accounting"] = verify_effects(
                [aid, p["attempt"]], receipts, quota["used"], quota["unknown"]
            )
            report["confirmed_requests"] = quota["used"]
            report["provider_receipts"] = receipts
            report["passed"] = resolved["state"] == "reconciled" and not any(report["accounting"].values())
    except (LocalError, httpx.HTTPError, KeyError) as exc:
        report["error"] = str(exc)
    finally:
        try:
            stack.containers(verify=True)
            stack.compose("start", "--wait", "--wait-timeout", "60", *api_names, timeout=80)
            not_running_before = [
                s for s in ("api2", "api", "scheduler", "provider", "db") if s not in before
            ]
            if not_running_before:
                stack.compose("stop", *not_running_before, timeout=60)
        except LocalError as exc:
            report["restore_error"] = str(exc)
            report["passed"] = False
        out.parent.mkdir(parents=True, exist_ok=True)
        json_file(out, report)
    return report


def main() -> int:
    import argparse

    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument("--out", type=Path, default=Path("runs/entrevista/ensayo.json"))
    args = p.parse_args()
    report = run_drill(Path.cwd(), args.out)
    print(f"{'Correcto' if report['passed'] else 'Fallido'} · {args.out}", flush=True)
    return 0 if report["passed"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
