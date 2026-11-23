"""F5: transactional admission service on real PostgreSQL (run against Compose or Kubernetes)."""

from __future__ import annotations

import os
import subprocess
import time
from concurrent.futures import ThreadPoolExecutor

import httpx
import pytest
from service_helpers import PROVIDER, advance, client, job, needs_service, ok, reset

pytestmark = [pytest.mark.postgres, needs_service]

ONE = [{"id": "quota", "unit": "requests", "capacity": 1, "window": "total"}]
TEN = [{"id": "quota", "unit": "requests", "capacity": 10, "window": "total"}]


def test_twenty_concurrent_claims_for_the_last_permit_get_at_most_one():
    reset(ONE)
    for who in ("radar", "citalab"):
        with client(who) as c:
            for k in range(10):
                ok(c.post("/v1/jobs", json=job(f"{who}-{k}")), 201)

    def claim(i):
        who = "radar" if i % 2 else "citalab"
        with client(who) as c:
            return ok(c.post("/v1/claims", json={"slots": 20}))

    with ThreadPoolExecutor(20) as pool:
        results = list(pool.map(claim, range(20)))
    permits = [r["permit"] for r in results if r.get("permit")]
    assert len(permits) == 1
    with client("admin") as a:
        u = ok(a.get("/v1/usage"))
    q = next(r for r in u["resources"] if r["id"] == "quota")
    assert (q["used"], q["available"]) == (1, 0)
    granted = ok(client("admin").get("/v1/admin/attempts", params={"state": "granted"}))
    assert len(granted) == 1


def test_repeated_register_start_finish_do_not_duplicate_consumption():
    reset(TEN)
    with client("radar") as c:
        a = ok(c.post("/v1/jobs", json=job("j1"), headers={"Idempotency-Key": "k1"}), 201)
        b = ok(c.post("/v1/jobs", json=job("j1"), headers={"Idempotency-Key": "k1"}), 201)
        assert a == b
        r = c.post("/v1/jobs", json=job("j1", cls="other"), headers={"Idempotency-Key": "k1"})
        assert r.status_code == 409 and r.json()["error"] == "idempotency_conflict"
        p = ok(c.post("/v1/claims", json={}))["permit"]
        s1 = ok(c.post(f"/v1/attempts/{p['attempt']}/start"))
        s2 = ok(c.post(f"/v1/attempts/{p['attempt']}/start"))
        assert s1 == s2
        body = {"status": "ok", "usage": {"requests": 1}, "receipt": "r1"}
        f1 = ok(c.post(f"/v1/attempts/{p['attempt']}/finish", json=body))
        f2 = ok(c.post(f"/v1/attempts/{p['attempt']}/finish", json=body))
        assert f1 == f2
        r = c.post(f"/v1/attempts/{p['attempt']}/finish", json={**body, "status": "invalid"})
        assert r.status_code == 409
        u = ok(c.get("/v1/usage"))
        assert next(x for x in u["resources"] if x["id"] == "quota")["used"] == 1
        assert ok(c.get("/v1/jobs/j1"))["state"] == "done"


def test_crash_before_start_releases_the_permit():
    reset(TEN)
    with client("radar") as c:
        ok(c.post("/v1/jobs", json=job("j")), 201)
        p = ok(c.post("/v1/claims", json={}))["permit"]
        advance(61)  # grant TTL is 60 s; the worker died before calling start
        p2 = ok(c.post("/v1/claims", json={}))["permit"]
        assert p2 and p2["attempt"] != p["attempt"] and p2["step"] == p["step"]
        r = c.post(f"/v1/attempts/{p['attempt']}/start")
        assert r.status_code == 409 and r.json()["error"] == "permit_expired"
        used = next(x for x in ok(c.get("/v1/usage"))["resources"] if x["id"] == "quota")["used"]
        assert used == 1  # only the live grant is booked; the expired one was released


def test_crash_after_start_becomes_unknown_and_reconciles_with_provider_receipt():
    reset(TEN)
    with client("radar") as c:
        ok(c.post("/v1/jobs", json=job("j")), 201)
        p = ok(c.post("/v1/claims", json={}))["permit"]
        ok(c.post(f"/v1/attempts/{p['attempt']}/start"))
        # the provider processed the call, then the worker crashed before /finish
        receipt = httpx.post(
            f"{PROVIDER}/v1/generate",
            json={"key": p["attempt"], "model": "sim-flash", "tokens_in": 10, "max_tokens_out": 20},
        ).json()
        advance(601)  # attempt timeout 600 s
        assert ok(c.post("/v1/claims", json={}))["permit"] is None  # not re-run blindly
        unknown = ok(client("admin").get("/v1/admin/attempts", params={"state": "unknown"}))
        assert [u["id"] for u in unknown] == [p["attempt"]]
        q = next(x for x in ok(c.get("/v1/usage"))["resources"] if x["id"] == "quota")
        assert (q["used"], q["unknown"]) == (1, 1)  # conservative accounting while uncertain
        got = httpx.get(f"{PROVIDER}/v1/receipts/{p['attempt']}").json()
        assert got["receipt"] == receipt["receipt"]
        ok(
            c.post(
                f"/v1/attempts/{p['attempt']}/reconcile",
                json={"consumed": True, "usage": got["usage"], "receipt": got["receipt"]},
            )
        )
        j = ok(c.get("/v1/jobs/j"))
        assert j["state"] == "done" and j["attempts"][0]["state"] == "reconciled"


def test_uncertain_attempt_without_receipt_can_be_released_and_retried():
    reset(TEN)
    with client("radar") as c:
        ok(c.post("/v1/jobs", json=job("j")), 201)
        p = ok(c.post("/v1/claims", json={}))["permit"]
        ok(c.post(f"/v1/attempts/{p['attempt']}/start"))
        advance(601)
        ok(c.post("/v1/claims", json={}))
        assert httpx.get(f"{PROVIDER}/v1/receipts/{p['attempt']}").status_code == 404
        ok(
            c.post(
                f"/v1/attempts/{p['attempt']}/reconcile", json={"consumed": False, "evidence": "no receipt"}
            )
        )
        p2 = ok(c.post("/v1/claims", json={}))["permit"]
        assert p2 and p2["step"] == "s"
        assert next(x for x in ok(c.get("/v1/usage"))["resources"] if x["id"] == "quota")["used"] == 1


def test_clients_only_see_their_own_jobs_and_admin_manages_limits():
    reset(TEN)
    with client("radar") as r, client("citalab") as c:
        ok(r.post("/v1/jobs", json=job("secret-job")), 201)
        assert c.get("/v1/jobs/radar:secret-job").status_code == 404
        assert c.put("/v1/admin/resources", json=TEN).status_code == 403
        assert all(d["job_id"].startswith("citalab:") for d in ok(c.get("/v1/decisions")))


def test_429_pauses_the_model_and_reports_reason():
    reset(TEN)
    with client("radar") as c:
        ok(
            c.post(
                "/v1/jobs",
                json=job(
                    "a", steps=[{"id": "s", "estimate": {"requests": 1}, "model": "m", "max_attempts": 2}]
                ),
            ),
            201,
        )
        ok(
            c.post("/v1/jobs", json=job("b", steps=[{"id": "s", "estimate": {"requests": 1}, "model": "m"}])),
            201,
        )
        p = ok(c.post("/v1/claims", json={}))["permit"]
        ok(c.post(f"/v1/attempts/{p['attempt']}/start"))
        ok(
            c.post(
                f"/v1/attempts/{p['attempt']}/finish",
                json={"status": "rate_limited", "usage": {"requests": 1}, "retry_after": 30},
            )
        )
        assert ok(c.post("/v1/claims", json={}))["permit"] is None
        reasons = {d["reason"] for d in ok(c.get("/v1/decisions"))}
        assert "provider_pause" in reasons
        advance(31)
        assert ok(c.post("/v1/claims", json={}))["permit"] is not None


def test_strict_token_cap_without_maximum_is_labelled_orientative():
    reset(TEN + [{"id": "tpm", "unit": "tokens_in", "capacity": 1000, "window": "rolling", "length": 60}])
    with client("radar") as c:
        r = ok(
            c.post(
                "/v1/jobs", json=job("j", steps=[{"id": "s", "estimate": {"requests": 1, "tokens_in": 10}}])
            ),
            201,
        )
        assert any("orientative" in w for w in r["warnings"])
        r2 = ok(
            c.post(
                "/v1/jobs",
                json=job(
                    "k",
                    steps=[
                        {
                            "id": "s",
                            "estimate": {"requests": 1, "tokens_in": 10},
                            "maximum": {"tokens_in": 20},
                        }
                    ],
                ),
            ),
            201,
        )
        assert r2["warnings"] == []


def test_reserve_policy_runs_in_the_service_with_the_same_core():
    reset(
        [{"id": "quota", "unit": "requests", "capacity": 3, "window": "total"}],
        {"name": "reserva", "reserve": {"quota": 2}},
    )
    with client("radar") as c:
        for k in range(3):
            ok(c.post("/v1/jobs", json=job(f"bg{k}")), 201)
        got = [ok(c.post("/v1/claims", json={"slots": 5}))["permit"] for _ in range(3)]
        assert sum(1 for g in got if g) == 1
        reasons = [d for d in ok(c.get("/v1/decisions")) if d["reason"] == "deadline_reserve"]
        assert reasons and "held for urgent work" in reasons[0]["detail"]
        ok(c.post("/v1/jobs", json=job("urgent", urgent=True, cls="application")), 201)
        p = ok(c.post("/v1/claims", json={"job": "urgent", "step": "s"}))["permit"]
        assert p and p["job"] == "radar:urgent"


@pytest.mark.skipif(os.environ.get("RUBATO_TEST_COMPOSE") != "1", reason="needs docker compose control")
def test_restart_preserves_jobs_and_audit():
    reset(TEN)
    with client("radar") as c:
        ok(c.post("/v1/jobs", json=job("survivor")), 201)
        p = ok(c.post("/v1/claims", json={}))["permit"]
        ok(c.post(f"/v1/attempts/{p['attempt']}/start"))
    subprocess.run(["docker", "compose", "restart", "api", "scheduler"], check=True, capture_output=True)
    for _ in range(60):
        try:
            if httpx.get(f"{os.environ['RUBATO_TEST_API']}/readyz", timeout=2).status_code == 200:
                break
        except httpx.HTTPError:
            pass
        time.sleep(1)
    with client("radar") as c:
        j = ok(c.get("/v1/jobs/survivor"))
        assert j["attempts"][0]["state"] == "started"
        ok(c.post(f"/v1/attempts/{p['attempt']}/finish", json={"status": "ok", "usage": {"requests": 1}}))
        assert ok(c.get("/v1/jobs/survivor"))["state"] == "done"
        assert [d["action"] for d in ok(c.get("/v1/decisions", params={"job": "survivor"}))] == ["authorize"]


def test_client_job_ids_may_contain_colons_and_slashes():
    reset(TEN)
    jid = "campaign/google:H:fam-1:primary/patient/abc/1"
    with client("citalab") as c:
        ok(c.post("/v1/jobs", json=job(jid)), 201)
        p = ok(c.post("/v1/claims", json={"job": jid, "step": "s"}))["permit"]
        assert p and p["job"] == f"citalab:{jid}"
        assert ok(c.get(f"/v1/jobs/{jid}"))["id"] == f"citalab:{jid}"
        assert ok(c.post(f"/v1/jobs/{jid}/cancel"))["state"] == "cancelled"


def test_a_claim_for_one_step_does_not_grant_another_step_of_the_same_client():
    reset(TEN)
    with client("radar") as c:
        ok(c.post("/v1/jobs", json=job("older")), 201)
        ok(c.post("/v1/jobs", json=job("newer")), 201)
        p = ok(c.post("/v1/claims", json={"job": "newer", "step": "s"}))["permit"]
        assert p and p["job"] == "radar:newer"
        assert [
            a["job_id"] for a in ok(client("admin").get("/v1/admin/attempts", params={"state": "granted"}))
        ] == ["radar:newer"]


def test_a_long_running_attempt_keeps_its_concurrency_slot_past_the_ledger_lookback():
    reset([{"id": "workers", "unit": "concurrent", "capacity": 1, "window": "concurrency"}])
    with client("radar") as c:
        ok(c.post("/v1/jobs", json=job("long")), 201)
        ok(c.post("/v1/jobs", json=job("next")), 201)
        p = ok(c.post("/v1/claims", json={"job": "long", "step": "s"}))["permit"]
        ok(c.post(f"/v1/attempts/{p['attempt']}/start"))
        advance(4 * 86400)  # beyond the 3-day ledger lookback; the call has not been settled
        assert ok(c.post("/v1/claims", json={"job": "next", "step": "s"}))["permit"] is None


def test_a_focused_claim_is_not_blocked_by_a_speculative_planner_grant():
    reset(TEN)
    with client("radar") as c, client("admin") as a:
        ok(c.post("/v1/jobs", json=job("older")), 201)
        ok(c.post("/v1/claims", json={"job": "older", "step": "missing"}))  # marks the executor available
        ok(c.post("/v1/jobs", json=job("newer")), 201)
        ok(a.post("/v1/admin/schedule"))  # the planner pre-grants the only slot to "older"
        p = ok(c.post("/v1/claims", json={"job": "newer", "step": "s"}))["permit"]
        assert p and p["job"] == "radar:newer"
        assert ok(c.get("/v1/jobs/older"))["steps"][0]["state"] == "ready"
