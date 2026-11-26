"""Weighted permits and transport recovery against the isolated Compose stack."""

from __future__ import annotations

import os
import time
from collections import Counter, deque
from concurrent.futures import ThreadPoolExecutor
from contextlib import ExitStack

import httpx
import pytest
from service_helpers import TOKENS, advance, checked_compose_stack, client, job, needs_service, ok, reset

from rubato.local import probe

pytestmark = [
    pytest.mark.postgres,
    needs_service,
    pytest.mark.skipif(
        os.environ.get("RUBATO_TEST_COMPOSE") != "1", reason="needs two local Compose replicas"
    ),
]

SHARED = {"id": "shared", "unit": "concurrent", "capacity": 1, "window": "concurrency"}
FAIR = {"name": "fair", "weights": {"citalab": 2}, "resource": "shared"}


@pytest.fixture
def fair_stack():
    stack, ports = checked_compose_stack()
    assert probe(ports["api2"], "/readyz").get("ok")
    return stack, ports


def replica(who: str, ports: dict[str, int]) -> httpx.Client:
    return httpx.Client(
        base_url=f"http://127.0.0.1:{ports['api2']}",
        headers={"Authorization": f"Bearer {TOKENS[who]}"},
        timeout=10,
    )


def test_claim_retry_across_replicas_returns_original_permit_in_fifo_order(fair_stack):
    _, ports = fair_stack
    reset([{**SHARED, "capacity": 3}], FAIR)
    with client("radar") as first, replica("radar", ports) as second:
        for jid in ("oldest", "middle", "newest"):
            ok(first.post("/v1/jobs", json=job(jid)))
        payload = {"slots": 3}
        key = {"Idempotency-Key": "one-logical-delivery"}
        with ThreadPoolExecutor(max_workers=2) as executor:
            calls = [
                executor.submit(p.post, "/v1/claims", json=payload, headers=key) for p in (first, second)
            ]
            permits = [ok(call.result())["permit"] for call in calls]
        assert permits[0] == permits[1]
        assert permits[0]["client_job_id"] == "oldest"
        assert second.post("/v1/claims", json={"slots": 2}, headers=key).status_code == 409
        for jid in ("middle", "newest"):
            assert ok(second.post("/v1/claims", json=payload))["permit"]["client_job_id"] == jid
        advance(65)
        assert ok(second.post("/v1/claims", json=payload, headers=key))["permit"] == permits[0]
        expired = second.post(f"/v1/attempts/{permits[0]['attempt']}/start")
        assert expired.status_code == 409 and expired.json()["error"] == "permit_expired"


def test_sixty_filtered_deliveries_keep_the_weighted_cursor_across_restart(fair_stack):
    stack, ports = fair_stack
    reset([SHARED, {"id": "requests", "unit": "requests", "capacity": 100}], FAIR)

    with ExitStack() as contexts:
        primary = {who: contexts.enter_context(client(who)) for who in ("citalab", "radar")}
        secondary = {who: contexts.enter_context(replica(who, ports)) for who in primary}
        admin = contexts.enter_context(client("admin"))
        radar = primary["radar"]

        # Keep the shared slot occupied until both clients have registered their backlog.
        ok(radar.post("/v1/jobs", json=job("blocker", urgent=True)))
        blocker = ok(radar.post("/v1/claims", json={"slots": 2}))["permit"]
        ok(radar.post(f"/v1/attempts/{blocker['attempt']}/start"))
        assert ok(primary["citalab"].post("/v1/claims", json={"slots": 1}))["permit"] is None
        pending = {
            who: deque(f"queued-{n}" for n in range(count))
            for who, count in {"citalab": 41, "radar": 21}.items()
        }
        for who, names in pending.items():
            for jid in names:
                ok(primary[who].post("/v1/jobs", json=job(jid)))

        def finish(peer, permit):
            ok(peer.post(f"/v1/attempts/{permit['attempt']}/finish", json={"usage": {"requests": 1}}))

        finish(radar, blocker)
        turns = []
        for index, who in enumerate(["citalab", "citalab", "radar"] * 20):
            other = "citalab" if who == "radar" else "radar"
            assert (
                ok(secondary[other].post("/v1/claims", json={"job": pending[other][0], "step": "s"}))[
                    "permit"
                ]
                is None
            )
            chosen = pending[who].popleft()
            p = ok(primary[who].post("/v1/claims", json={"job": chosen, "step": "s"}))["permit"]
            assert p and p["client_job_id"] == chosen
            assert p["attempt"] not in {aid for _, aid in turns}
            turns.append((who, p["attempt"]))
            ok(primary[who].post(f"/v1/attempts/{p['attempt']}/start"))
            if index == 1:
                stack.compose("restart", "api", "api2", "scheduler", timeout=60)
                deadline = time.monotonic() + 30
                while not all(probe(ports[service], "/readyz").get("ok") for service in ("api", "api2")):
                    assert time.monotonic() < deadline, "replicas did not recover"
                    time.sleep(0.2)
            finish(secondary[who], p)
        assert Counter(who for who, _ in turns) == {"citalab": 40, "radar": 20}
        book = {r["id"]: r for r in ok(admin.get("/v1/usage"))["resources"]}
        assert book["requests"]["used"] in (61, 62)  # scheduler may already have prepared the next grant
        confirmed = ok(admin.get("/v1/admin/attempts", params={"state": "finished"}))
        assert {a["id"] for a in confirmed} == {blocker["attempt"], *(aid for _, aid in turns)}
        report = ok(admin.get("/v1/admin/overview"))["fair_turns"]
        observed = {row["client_id"]: row for row in report["clients"]}
        for who, count in (("citalab", 40), ("radar", 20)):
            assert observed[who]["delivered"] == observed[who]["wait_samples"] == count
            assert observed[who]["wait_p95_s"] >= 0
            assert observed[who]["requested_slots"] is True


def test_speculative_turn_expiry_leaves_only_actual_deliveries(fair_stack):
    _, ports = fair_stack
    reset([SHARED], FAIR)
    with client("admin") as admin, client("citalab") as citalab, replica("radar", ports) as radar:
        # With a weight of two, citalab should get its second turn even if a prepared permit expires.
        ok(citalab.post("/v1/jobs", json=job("done")))
        p = ok(citalab.post("/v1/claims"))["permit"]
        ok(citalab.post(f"/v1/attempts/{p['attempt']}/start"))
        assert ok(radar.post("/v1/claims"))["permit"] is None
        ok(citalab.post("/v1/jobs", json=job("next")))
        ok(radar.post("/v1/jobs", json=job("alternate")))
        ok(citalab.post(f"/v1/attempts/{p['attempt']}/finish", json={"usage": {"requests": 1}}))
        ok(admin.post("/v1/admin/schedule"))
        prepared = ok(admin.get("/v1/admin/attempts", params={"state": "granted"}))
        assert [p["job_id"] for p in prepared] == ["citalab:next"]
        advance(65)
        assert ok(radar.post("/v1/claims", json={"job": "alternate", "step": "s"}))["permit"] is None
        recovered = ok(citalab.post("/v1/claims", json={"job": "next", "step": "s"}))["permit"]
        assert recovered and recovered["attempt"] != prepared[0]["id"]
        old = ok(admin.get("/v1/admin/attempts", params={"state": "expired"}))
        assert [p["id"] for p in old] == [prepared[0]["id"]]
