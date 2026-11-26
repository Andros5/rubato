import pytest

from rubato.explain import winners
from rubato.metrics import summarize
from rubato.model import Job, Reason, Step
from rubato.policies import FifoPolicy, ReservePolicy
from rubato.resources import ResourceSpec
from rubato.sim import simulate


def job(name, *, arrival=0, urgent=False, model=None, client="radar", duration=1, deps=()):
    return Job(
        name,
        client,
        "test",
        arrival,
        (Step("s", {"requests": 1}, deps, model, duration_estimate=duration),),
        urgent=urgent,
    )


def test_reserve_counts_urgent_usage_once_across_rounds():
    jobs = [job("urgent", urgent=True), job("bg1"), job("bg2"), job("urgent2", arrival=2, urgent=True)]
    run = simulate(jobs, [ResourceSpec("rpd", "requests", 3)], ReservePolicy(reserve={"rpd": 2}))
    assert [(a.job_id, a.start) for a in run.attempts] == [("urgent", 0), ("bg1", 0), ("urgent2", 2)]
    decision = next(d for d in run.decisions if d.job_id == "bg2")
    assert decision.reason == Reason.DEADLINE_RESERVE
    assert decision.value["blocks"][0]["held"] == 1


@pytest.mark.parametrize("dimension", ["model", "client"])
def test_known_demand_matches_the_resource_scope(dimension):
    jobs = [
        job("urgent", urgent=True, model="a", client="a", duration=3),
        job("bg", model="b", client="b"),
    ]
    resources = [
        ResourceSpec("a", "requests", 1, scope={dimension: "a"}),
        ResourceSpec("b", "requests", 1, scope={dimension: "b"}),
    ]
    run = simulate(jobs, resources, ReservePolicy(reserve={"*": 0}))
    assert [(a.job_id, a.start) for a in run.attempts] == [("urgent", 0), ("bg", 0)]


def test_dependent_step_protects_its_own_model_and_shared_quota():
    jobs = [
        Job(
            "urgent",
            "a",
            "test",
            0,
            (
                Step("a", {"requests": 1}, model="a", duration_estimate=3),
                Step("b", {"requests": 1}, ("a",), "b"),
            ),
            urgent=True,
        ),
        job("bg", model="b", client="b"),
    ]
    for scope in ({}, {"model": "b"}):
        capacity = 2 if not scope else 1
        run = simulate(
            jobs, [ResourceSpec("q", "requests", capacity, scope=scope)], ReservePolicy(reserve={"q": 0})
        )
        assert [(a.step_id, a.start) for a in run.attempts] == [("a", 0), ("b", 3)]
        assert next(d for d in run.decisions if d.job_id == "bg").reason == Reason.DEADLINE_RESERVE


@pytest.mark.parametrize("serial", [False, True])
def test_deadline_uses_critical_path(serial):
    urgent = Job(
        "urgent",
        "radar",
        "test",
        0,
        (
            Step("a", {"requests": 1}, duration_estimate=4),
            Step("b", {"requests": 1}, ("a",) if serial else (), duration_estimate=4),
        ),
        urgent=True,
        deadline=5,
    )
    run = simulate(
        [urgent], [ResourceSpec("slots", "concurrent", 2, "concurrency")], ReservePolicy(impossible="drop")
    )
    assert run.jobs[0].status == ("closed" if serial else "done")
    assert run.jobs[0].finished_at == (None if serial else 4)


def test_missing_wait_does_not_win_over_measured_wait_or_zero():
    jobs = [job("bg")]
    policies = {"none": ReservePolicy(reserve={"q": 1}), "zero": FifoPolicy()}
    results = {
        k: summarize(simulate(jobs, [ResourceSpec("q", "requests", 1)], p, horizon=10))
        for k, p in policies.items()
    }
    assert results["none"]["background"]["wait_p95"] is None
    assert results["zero"]["background"]["wait_p95"] == 0
    assert winners(results)["background_wait_p95 (lower)"] == ["zero"]
    assert winners({"none": results["none"]})["background_wait_p95 (lower)"] == []
