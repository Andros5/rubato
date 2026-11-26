"""F10: several providers, each with its own limits per client."""

import pytest

from rubato.model import Job, Outcome, Provenance, Reason, Status, Step
from rubato.policies import FifoPolicy, ReservePolicy
from rubato.resources import ResourceSpec
from rubato.sim import ExternalEvent, simulate
from rubato.trace import SCHEMA, Trace, TraceError, dumps, loads

P = {"arrival": Provenance.SYNTHETIC}
FIFO = FifoPolicy()


def J(jid, client, provider, arrival=0, model=None, outcomes=(), attempts=1, **kw):
    step = Step("s", {"requests": 1}, (), model, None, 1, attempts, tuple(outcomes), provider=provider)
    return Job(jid, client, "bg", arrival, (step,), provenance=P, **kw)


def starts(res, jid):
    return [a.start for a in res.attempts if a.job_id == jid]


def last_decision(res, jid):
    return [d for d in res.decisions if d.job_id == jid][-1]


PROVIDERS = [
    ResourceSpec("a-total", "requests", 3, "total", scope={"provider": "a"}),
    ResourceSpec("a-radar", "requests", 1, "total", scope={"provider": "a", "client": "radar"}),
    ResourceSpec("b-total", "requests", 1, "total", scope={"provider": "b"}),
]


def test_each_client_has_its_own_share_of_each_provider():
    jobs = [
        J("r1", "radar", "a"),
        J("r2", "radar", "a"),
        J("c1", "citalab", "a"),
        J("c2", "citalab", "a"),
        J("r3", "radar", "b"),
    ]
    res = simulate(jobs, PROVIDERS, FIFO)
    assert starts(res, "r1") == [0] and starts(res, "r2") == []
    d = next(d for d in res.decisions if d.job_id == "r2")
    assert (d.t, d.reason, d.resource) == (0, Reason.CAPACITY_EXHAUSTED, "a-radar")
    # the other client still uses provider a, and radar still uses provider b
    assert starts(res, "c1") == [0] and starts(res, "c2") == [0] and starts(res, "r3") == [0]
    assert res.consumed == {"a-total": 3, "a-radar": 1, "b-total": 1}


def test_exhausting_one_provider_does_not_block_another():
    jobs = [J("a1", "radar", "a"), J("a2", "citalab", "a"), J("a3", "citalab", "a")]
    jobs += [J("a4", "citalab", "a"), J("b1", "citalab", "b")]
    res = simulate(jobs, PROVIDERS, FIFO)
    assert starts(res, "a4") == []
    assert last_decision(res, "a4").resource == "a-total"
    assert starts(res, "b1") == [0]  # backfills ahead of a4, on its own provider


def test_steps_without_provider_are_not_charged_to_provider_limits():
    res = simulate([J("x", "radar", None), J("y", "radar", None)], PROVIDERS, FIFO)
    assert starts(res, "x") == [0] and starts(res, "y") == [0]
    assert res.consumed == {"a-total": 0, "a-radar": 0, "b-total": 0}


def test_a_429_without_model_pauses_only_that_provider():
    limited = Outcome(1, {"requests": 1}, Status.RATE_LIMITED, retry_after=10)
    ok = Outcome(1, {"requests": 1})
    rate = [ResourceSpec("any", "requests", 100, "total")]
    jobs = [
        J("first", "radar", "a", outcomes=(limited, ok), attempts=2),
        J("later-a", "radar", "a", arrival=2),
        J("later-b", "radar", "b", arrival=2),
    ]
    res = simulate(jobs, rate, FIFO)
    assert starts(res, "first") == [0, 11]
    assert starts(res, "later-b") == [2]
    assert starts(res, "later-a")[0] == 11
    d = next(d for d in res.decisions if d.job_id == "later-a")
    assert (d.reason, d.next_t) == (Reason.PROVIDER_PAUSE, 11)


def test_pause_event_scoped_to_a_provider():
    ev = [ExternalEvent(0, "pause", scope=("provider", "a"), until=5)]
    res = simulate([J("a1", "radar", "a"), J("b1", "radar", "b")], [], FIFO, ev)
    assert starts(res, "a1") == [5] and starts(res, "b1") == [0]


def test_urgent_demand_on_one_provider_does_not_hold_capacity_of_another():
    """Known urgent work on provider b is protected on b's limits only."""
    res_specs = [
        ResourceSpec("a-quota", "requests", 1, "total", scope={"provider": "a"}),
        ResourceSpec("b-quota", "requests", 1, "total", scope={"provider": "b"}),
    ]
    pol = ReservePolicy(reserve={"*-quota": 0})
    urgent = J("urgent", "radar", "b", urgent=True, deadline=200)
    bg_a, bg_b = J("bg-a", "citalab", "a"), J("bg-b", "citalab", "b")
    ev = [ExternalEvent(0, "pause", scope=("provider", "b"), until=100)]
    res = simulate([urgent, bg_a, bg_b], res_specs, pol, ev)
    assert starts(res, "bg-a") == [0]  # provider a is free to use
    assert starts(res, "urgent") == [100]
    assert starts(res, "bg-b") == []  # b's only request went to the urgent job


def test_unknown_scope_keys_are_rejected_instead_of_applying_to_everything():
    with pytest.raises(ValueError, match="unknown scope"):
        ResourceSpec("x", "requests", 1, "total", scope={"vendor": "a"})
    with pytest.raises(ValueError, match="non-empty"):
        ResourceSpec("x", "requests", 1, "total", scope={"provider": ""})


def test_trace_keeps_the_provider_of_steps_events_and_resources():
    tr = Trace(
        {"type": "header", "schema": SCHEMA, "time_unit": "s"},
        list(PROVIDERS),
        [J("r1", "radar", "a", model="m")],
        [ExternalEvent(3, "consume", client="other", usage={"requests": 1}, provider="a")],
    )
    text = dumps(tr)
    back = loads(text)
    assert back.jobs[0].steps[0].provider == "a"
    assert back.events[0].provider == "a"
    assert back.resources[1].scope == {"provider": "a", "client": "radar"}
    assert dumps(back) == text
    # usage outside Rubato on provider a counts against provider a only
    res = simulate(back.jobs, back.resources, FIFO, back.events)
    assert res.consumed == {"a-total": 2, "a-radar": 1, "b-total": 0}

    bad = text.replace('"provider":"a"}', '"vendor":"a"}', 1)
    with pytest.raises(TraceError, match="unknown scope"):
        loads(bad)
    with pytest.raises(TraceError, match="provider"):
        loads(text.replace('"model":"m","provider":"a"', '"model":"m","provider":""'))


# --- service (real PostgreSQL) ----------------------------------------------


def _service():
    import service_helpers as h

    if not h.API:
        pytest.skip("set RUBATO_TEST_API to a running Rubato stack")
    return h


@pytest.mark.postgres
def test_service_applies_each_providers_limits_per_client():
    h = _service()
    h.reset([vars(r) for r in PROVIDERS])

    def step(provider):
        return [{"id": "s", "estimate": {"requests": 1}, "maximum": {"requests": 1}, "provider": provider}]

    with h.client("radar") as c:
        h.ok(c.post("/v1/jobs", json=h.job("r1", steps=step("a"))), 201)
        h.ok(c.post("/v1/jobs", json=h.job("r2", steps=step("a"))), 201)
        h.ok(c.post("/v1/jobs", json=h.job("r3", steps=step("b"))), 201)
        got = [h.ok(c.post("/v1/claims", json={"slots": 3}))["permit"] for _ in range(3)]
        permits = [p for p in got if p]
        assert sorted((p["client_job_id"], p["provider"]) for p in permits) == [("r1", "a"), ("r3", "b")]
        assert h.ok(c.get("/v1/jobs/r2"))["steps"][0]["last_reason"] == "capacity_exhausted"
    with h.client("citalab") as c:
        h.ok(c.post("/v1/jobs", json=h.job("c1", steps=step("a"))), 201)
        p = h.ok(c.post("/v1/claims", json={}))["permit"]
        assert p and p["provider"] == "a"  # radar's share is used up, provider a is not
    with h.client("admin") as a:
        used = {r["id"]: r["used"] for r in h.ok(a.get("/v1/usage"))["resources"]}
    assert used == {"a-total": 2, "a-radar": 1, "b-total": 1}


@pytest.mark.postgres
def test_service_rejects_unknown_scope_keys_and_empty_providers():
    h = _service()
    with h.client("admin") as a:
        r = a.put(
            "/v1/admin/resources",
            json=[{"id": "x", "unit": "requests", "capacity": 1, "scope": {"vendor": "a"}}],
        )
        assert r.status_code == 422 and "unknown scope" in r.text
    h.reset([{"id": "q", "unit": "requests", "capacity": 5, "window": "total"}])
    with h.client("radar") as c:
        bad = [{"id": "s", "estimate": {"requests": 1}, "provider": ""}]
        assert c.post("/v1/jobs", json=h.job("bad", steps=bad)).status_code == 422
