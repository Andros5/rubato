"""Panel and server-controlled client modes."""

import httpx
import pytest
from service_helpers import API, client, job, needs_service, ok, reset

from rubato.sdk import Gate, Outcome, RubatoClient


@pytest.mark.postgres
@needs_service
def test_admin_sets_the_mode_and_the_client_reads_it():
    reset([{"id": "quota", "unit": "requests", "capacity": 5, "window": "total"}])
    with client("radar") as r, client("admin") as a, client("citalab") as c:
        assert ok(r.get("/v1/mode"))["mode"] == "off"
        assert ok(a.put("/v1/admin/clients/radar/mode", json={"mode": "shadow"}))["mode"] == "shadow"
        assert ok(r.get("/v1/mode"))["mode"] == "shadow"
        assert ok(c.get("/v1/mode"))["mode"] == "off"
        assert c.put("/v1/admin/clients/radar/mode", json={"mode": "enforce"}).status_code == 403
        assert a.put("/v1/admin/clients/radar/mode", json={"mode": "maybe"}).status_code == 422
        assert a.put("/v1/admin/clients/nobody/mode", json={"mode": "off"}).status_code == 404


@pytest.mark.postgres
@needs_service
def test_overview_feeds_the_panel():
    reset([{"id": "quota", "unit": "requests", "capacity": 1, "window": "total"}])
    with client("radar") as r:
        ok(r.post("/v1/jobs", json=job("a")), 201)
        ok(r.post("/v1/jobs", json=job("b", urgent=True, priority=10)), 201)
        ok(r.post("/v1/claims", json={}))
    with client("admin") as a:
        o = ok(a.get("/v1/admin/overview"))
        assert client("radar").get("/v1/admin/overview").status_code == 403
    q = o["resources"][0]
    assert (q["id"], q["used"], q["available"], q["used_urgent"]) == ("quota", 1, 0, 1)
    assert {c["id"] for c in o["clients"]} == {"radar", "citalab"}
    assert any(r["last_reason"] == "capacity_exhausted" for r in o["reasons"])
    assert [w["job_id"] for w in o["waiting"]] == ["radar:a"]
    assert o["policy"]["label"].startswith("radar@") and o["decisions"]
    with client("radar") as r:  # a shadow question is counted in the panel
        ok(r.post("/v1/claims", json={"job": "a", "step": "s", "shadow": True}))
    with client("admin") as a:
        sh = ok(a.get("/v1/admin/overview"))["shadow_24h"]
    assert sh == [{"client_id": "radar", "action": "defer", "reason": "capacity_exhausted", "n": 1}]
    page = httpx.get(f"{API}/panel")
    assert page.status_code == 200 and "Rubato · panel" in page.text and "const esc=" in page.text
    assert "https://" not in page.text  # self-contained


def fake_service(modes):
    calls = []

    def handle(request):
        calls.append(request.url.path)
        if request.url.path == "/v1/mode":
            m = modes.pop(0)
            if isinstance(m, Exception):
                raise m
            return httpx.Response(200, json={"mode": m})
        if request.url.path == "/v1/claims":
            return httpx.Response(200, json={"shadow": {"action": "authorize"}})
        return httpx.Response(200, json={})

    return RubatoClient("http://rubato", "t", transport=httpx.MockTransport(handle)), calls


def test_auto_mode_follows_the_panel_and_survives_an_unreachable_service():
    rc, calls = fake_service(["shadow", httpx.ConnectError("down"), "off"])
    gate = Gate(rc, "auto", mode_ttl=0)
    ran = lambda: ("v", Outcome("ok", {"requests": 1}))  # noqa: E731
    assert gate.run("j", "s", ran).mode == "shadow"
    assert gate.run("j", "s", ran).mode == "shadow"  # service unreachable: keep the last known mode
    assert gate.run("j", "s", ran).mode == "off"
    assert calls.count("/v1/observations") == 2


def test_auto_mode_is_cached():
    rc, calls = fake_service(["enforce"])
    gate = Gate(rc, "auto", mode_ttl=3600)
    assert gate.current_mode() == "enforce" and gate.current_mode() == "enforce"
    assert calls == ["/v1/mode"]


def test_auto_mode_starts_off_until_the_service_answers():
    rc, _ = fake_service([httpx.ConnectError("down")])
    assert Gate(rc, "auto").current_mode() == "off"
