from concurrent.futures import ThreadPoolExecutor
from datetime import UTC, datetime

import pytest
from service_helpers import advance, client, job, needs_service, ok, reset

pytestmark = [pytest.mark.postgres, needs_service]


def test_draft_concurrency_retry_and_revert_preserves_permit():
    reset([{"id": "q", "unit": "requests", "capacity": 3}])
    with client("admin") as admin, client("radar") as user:
        ok(user.post("/v1/jobs", json=job("held")))
        permit = ok(user.post("/v1/claims", json={}))["permit"]
        drafts = [
            ok(
                admin.post(
                    "/v1/admin/policy-drafts",
                    json={
                        "base_revision": 0,
                        "policy": {"name": "reserva", "reserve": {"q": n}},
                    },
                ),
                201,
            )
            for n in (1, 2)
        ]
        assert ok(admin.get("/v1/admin/policy-history"))["active_revision"] == 0
        body = {"reason": "synthetic comparison"}
        paths = [f"/v1/admin/policy-drafts/{d['id']}/activate" for d in drafts]

        def submit(ordinal):
            return admin.post(
                paths[ordinal], headers={"If-Match": '"0"', "Idempotency-Key": f"test-{ordinal}"}, json=body
            )

        with ThreadPoolExecutor(max_workers=2) as pool:
            responses = list(pool.map(submit, (0, 1)))
        assert sorted(r.status_code for r in responses) == [200, 412]
        winner = next(i for i, r in enumerate(responses) if r.status_code == 200)
        revision = responses[winner].json()["revision"]
        assert ok(submit(winner))["revision"] == revision
        reverted = ok(
            admin.post(
                "/v1/admin/policy-history/0/revert",
                headers={"If-Match": f'"{revision}"'},
                json={"reason": "return to reference"},
            )
        )
        assert reverted["revision"] > revision and reverted["policy"]["name"] == "radar"
        assert ok(user.get("/v1/jobs/held"))["attempts"][0]["id"] == permit["attempt"]


def test_quote_is_pinned_and_budget_counts_a_call_once_in_spend():
    resources = [
        {
            "id": "eur",
            "unit": "cost_micro",
            "capacity": 6000,
            "window": "calendar",
            "calendar_period": "month",
            "reset_tz": "UTC",
        },
        {"id": "radar-eur", "unit": "cost_micro", "capacity": 15_000, "scope": {"client": "radar"}},
        {"id": "rpm", "unit": "requests", "capacity": 100, "window": "rolling", "length": 60},
    ]
    reset(resources)
    with client("admin") as admin, client("radar") as user:
        tariff = {
            "schema": "rubato.tariff/v1",
            "version": "old",
            "currency": "EUR",
            "rates": [{"model": "m", "input_per_million": "1.25", "output_per_million": "5"}],
        }
        ok(admin.put("/v1/admin/tariff", json=tariff))
        work = [
            {
                "id": "s",
                "model": "m",
                "estimate": {"requests": 1},
                "maximum": {"tokens_in": 2000, "tokens_out": 600},
            }
        ]
        registered = ok(user.post("/v1/jobs", json=job("price", steps=work)))
        assert not registered["warnings"]
        p = ok(user.post("/v1/claims", json={}))["permit"]
        ok(user.post(f"/v1/attempts/{p['attempt']}/start"))
        tariff["version"] = "new"
        tariff["rates"][0]["input_per_million"] = "1.50"
        ok(admin.put("/v1/admin/tariff", json=tariff))
        finished = ok(
            user.post(
                f"/v1/attempts/{p['attempt']}/finish",
                json={"usage": {"requests": 1, "tokens_in": 2000, "tokens_out": 600}},
            )
        )
        assert finished["usage"]["cost_micro"] == 5500 and finished["tariff"]["version"] == "old"
        ok(user.post("/v1/jobs", json=job("second", steps=work)))
        assert not ok(user.post("/v1/claims", json={}))["permit"]
        assert (
            next(
                d
                for d in ok(user.get("/v1/decisions", params={"job": "second"}))
                if d["reason"] == "capacity_exhausted"
            )["resource"]
            == "eur"
        )
        spend = ok(admin.get("/v1/consumption"))["items"]
        assert len(spend) == 1 and spend[0]["confirmed"] == spend[0]["cost_per_completed_job_micro"] == 5500


def test_investigation_paginates_against_concurrent_insertions_and_scopes_clients():
    reset([{"id": "q", "unit": "requests", "capacity": 3}])
    with client("radar") as r, client("citalab") as c, client("admin") as a:
        for name in ("one", "two", "three"):
            ok(r.post("/v1/jobs", json=job(name)))
        ok(c.post("/v1/jobs", json=job("other")))
        first = ok(r.get("/v1/investigation/jobs", params={"limit": 2, "owner": "citalab"}))
        ok(r.post("/v1/jobs", json=job("new")))
        second = ok(r.get("/v1/investigation/jobs", params={"limit": 2, "cursor": first["next_cursor"]}))
        assert [row["id"] for row in [*first["items"], *second["items"]]] == [
            "radar:three",
            "radar:two",
            "radar:one",
        ]
        assert not second["next_cursor"]
        assert ok(a.get("/v1/investigation/jobs", params={"q": "other"}))["items"][0]["id"] == "citalab:other"
        assert "coverage" in ok(a.get("/v1/admin/digest"))
        ok(a.put("/v1/admin/policy", json={"name": "reserva", "reserve": {"q": 3}}))
        assert ok(r.post("/v1/claims", json={"slots": 4}))["permit"] is None
        query = {"limit": 2, "deferred": True, "owner": "citalab"}
        decisions = ok(r.get("/v1/investigation/decisions", params=query))
        continuation = ok(
            r.get("/v1/investigation/decisions", params={**query, "cursor": decisions["next_cursor"]})
        )
        all_rows = decisions["items"] + continuation["items"]
        assert len(all_rows) == 4 and all(
            row["reason"] == "deadline_reserve" and row["client_id"] == "radar" for row in all_rows
        )
        group = ok(a.get("/v1/investigation/incidents", params={"owner": "radar"}))["items"][0]
        assert (group["resource"], group["jobs"], group["open"], group["resolved_at"]) == ("q", 4, True, None)


def test_parallel_service_estimates_keep_job_eligible_and_explain_one_slot():
    reset([{"id": "q", "unit": "requests", "capacity": 3}], {"name": "reserva", "impossible": "drop"})
    with client("radar") as user:
        t = advance(0)
        steps = [{"id": step, "estimate": {"requests": 1}, "duration_estimate": 4} for step in ("a", "b")]
        ok(user.post("/v1/jobs", json=job("parallel", urgent=True, deadline=t + 7, steps=steps)))
        p = ok(user.post("/v1/claims", json={"slots": 1}))["permit"]
        assert p
        ok(user.post(f"/v1/attempts/{p['attempt']}/start"))
        view = ok(user.get("/v1/jobs/parallel"))
        assert view["deadline_risk"]["critical_path_s"] <= 4
        assert any(x["reason"] == "client_unavailable" for x in view["deadline_risk"]["blocking"])


def test_month_snapshot_covers_old_usage_and_settlement_keeps_admission_month():
    reset(
        [
            {
                "id": "q",
                "unit": "requests",
                "capacity": 2,
                "window": "calendar",
                "calendar_period": "month",
                "reset_tz": "UTC",
            }
        ]
    )
    with client("radar") as r:
        feb = int(datetime(2027, 2, 1, tzinfo=UTC).timestamp())
        end = int(datetime(2027, 3, 1, tzinfo=UTC).timestamp())
        advance(feb + 60 - advance(0))
        ok(r.post("/v1/jobs", json=job("old")))
        old = ok(r.post("/v1/claims", json={}))["permit"]["attempt"]
        ok(r.post(f"/v1/attempts/{old}/start"))
        ok(r.post(f"/v1/attempts/{old}/finish", json={"usage": {"requests": 1}}))
        advance(end - 6 - advance(0))
        assert ok(r.get("/v1/usage"))["resources"][0]["used"] == 1
        ok(r.post("/v1/jobs", json=job("cross")))
        cross = ok(r.post("/v1/claims", json={}))["permit"]["attempt"]
        ok(r.post(f"/v1/attempts/{cross}/start"))
        advance(8)
        ok(r.post(f"/v1/attempts/{cross}/finish", json={"usage": {"requests": 1}}))
        assert ok(r.get("/v1/usage"))["resources"][0]["used"] == 0


def test_checkpoint_replays_a_round_without_mutating_book_or_config():
    reset([{"id": "q", "unit": "requests", "capacity": 2}], {"name": "reserva", "reserve": {"q": 2}})
    with client("radar") as user, client("admin") as admin:
        ok(user.post("/v1/jobs", json=job("waiting")))
        assert ok(user.post("/v1/claims", json={"slots": 1}))["permit"] is None
        checkpoint = ok(admin.post("/v1/admin/replay-checkpoints"), 201)
        url = f"/v1/admin/replay-checkpoints/{checkpoint['id']}"
        result = ok(admin.post(url + "/preview", json={"job": "radar:waiting", "policy": {"name": "radar"}}))
        assert result["reference"]["decisions"][0]["reason"] == "deadline_reserve"
        assert result["candidate"]["decisions"][0]["action"] == "authorize"
        assert ok(admin.get(url))["coverage"]["initial_state"] == "captured"
        assert ok(user.get("/v1/usage"))["resources"][0]["used"] == 0
        assert user.get(url).status_code == 403
