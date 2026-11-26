"""Capture and replay a real sequence of scheduling decisions without changing the ledger."""

from __future__ import annotations

import json
import time

import pytest
from service_helpers import client, job, needs_service, ok, reset

pytestmark = [pytest.mark.postgres, needs_service]


def test_incident_replays_the_resource_and_policy_changes_from_capture_to_resolution(tmp_path, capsys):
    from rubato.cli import main

    reset([{"id": "day", "unit": "requests", "capacity": 1}])
    with client("admin") as admin, client("radar") as worker, client("citalab") as contender:
        ok(worker.post("/v1/jobs", json=job("already-spent")))
        prior = ok(worker.post("/v1/claims", json={"slots": 1}))["permit"]["attempt"]
        ok(worker.post(f"/v1/attempts/{prior}/start"))
        ok(worker.post(f"/v1/attempts/{prior}/finish", json={"usage": {"requests": 1}}))
        ok(worker.post("/v1/jobs", json=job("incident", meta={"ref": "sample-reference"})))

        capture = ok(admin.post("/v1/admin/replay-checkpoints", json={"record_seconds": 120}), 201)
        url = f"/v1/admin/replay-checkpoints/{capture['id']}"
        assert admin.post("/v1/admin/replay-checkpoints", json={"record_seconds": 120}).status_code == 409
        assert ok(worker.post("/v1/claims", json={"job": "incident", "step": "s"}))["permit"] is None
        ok(contender.post("/v1/jobs", json=job("later")))
        assert (
            ok(contender.post("/v1/claims", json={"slots": 0, "job": "later", "step": "s"}))["permit"] is None
        )

        ok(admin.put("/v1/admin/policy", json={"name": "reserva", "reserve": {"day": 0}}))
        ok(admin.put("/v1/admin/resources", json=[{"id": "day", "unit": "requests", "capacity": 2}]))
        permit = ok(worker.post("/v1/claims", json={"job": "incident", "step": "s"}))["permit"]["attempt"]
        ok(worker.post(f"/v1/attempts/{permit}/start"))
        ok(worker.post(f"/v1/attempts/{permit}/finish", json={"usage": {"requests": 1}}))
        stopped = ok(admin.post(url + "/stop"))
        assert ok(admin.post(url + "/stop")) == stopped

        ok(worker.post("/v1/jobs", json=job("after-stop")))
        ok(worker.post("/v1/claims", json={"job": "after-stop", "step": "s"}))
        result = ok(admin.post(url + "/replay", json={"job": "radar:incident", "policy": {"name": "fifo"}}))
        assert result["coverage"]["status"] == "complete"
        assert all(row["matched"] for row in result["rounds"])
        assert any(
            item["reason"] == "capacity_exhausted" for row in result["rounds"] for item in row["reference"]
        )
        assert any(
            row["policy"] == "reserva@2" and any(item["action"] == "authorize" for item in row["reference"])
            for row in result["rounds"]
        )
        assert any(e["kind"] == "attempt_finished" and e["attempt_id"] == permit for e in result["events"])
        export = ok(admin.get(url))
        assert result["matched_rounds"] == export["coverage"]["rounds"] >= 2
        assert all(e["job_id"] != "radar:after-stop" for e in export["events"])
        assert {r["kind"] for r in export["configuration"]} == {"policy", "resources"}
        assert "sample-reference" not in json.dumps(export)
        saved = tmp_path / "incident.json"
        saved.write_text(json.dumps(export), encoding="utf-8")
        policy = tmp_path / "fifo.json"
        policy.write_text('{"name":"fifo"}', encoding="utf-8")
        assert main(["replay-incident", str(saved), "--job", "radar:incident", "--policy", str(policy)]) == 0
        assert json.loads(capsys.readouterr().out) == result
        assert ok(admin.get("/v1/usage"))["resources"][0]["used"] == 2
        assert worker.post(url + "/replay", json={"job": "radar:incident"}).status_code == 403


def test_admin_can_rediscover_recordings_and_see_they_expired():
    reset([{"id": "day", "unit": "requests", "capacity": 1}])
    url = "/v1/admin/replay-checkpoints"
    with client("admin") as admin, client("radar") as worker:
        assert ok(admin.get(url))["items"] == []
        assert worker.get(url).status_code == 403
        running = ok(admin.post(url, json={"record_seconds": 120}), 201)["id"]
        older = ok(admin.post(url), 201)["id"]
        for _ in range(12):
            ok(admin.post(url), 201)

        recent = ok(admin.get(url))["items"]
        assert len(recent) == 13
        assert recent[-1]["id"] == running and recent[-1]["state"] == "recording"
        assert all(row["state"] == "snapshot" for row in recent[:-1])
        assert all(
            set(row) == {"id", "captured_at", "record_until", "stopped_at", "round_count", "state"}
            for row in recent
        )

        ok(admin.post(f"{url}/{running}/stop"))
        recovered = ok(admin.get(url, params={"include": older}))["items"]
        assert recovered[-1]["id"] == older and recovered[-1]["state"] == "snapshot"
        stopped = ok(admin.get(url, params={"include": running}))["items"][-1]
        assert stopped["id"] == running and stopped["state"] == "manual" and stopped["stopped_at"]

        short = ok(admin.post(url, json={"record_seconds": 1}), 201)["id"]
        deadline = time.monotonic() + 8
        while (row := ok(admin.get(url))["items"][0])["state"] == "recording":
            assert time.monotonic() < deadline
            time.sleep(0.1)
        assert row["id"] == short and row["state"] == "elapsed"
        assert row["stopped_at"] is None and row["record_until"] is not None
