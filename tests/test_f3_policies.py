"""F3: policies, offline optimum and explanations."""

import random

import pytest

from rubato.explain import explain_job, what_if, winners
from rubato.metrics import summarize
from rubato.model import Job, Outcome, Provenance, ReadyItem, Reason, Status, Step
from rubato.optimize import enumerate_best
from rubato.policies import FifoPolicy, RadarPolicy, ReservePolicy
from rubato.resources import ResourceSpec
from rubato.scenarios import F0_HORIZON, f0_jobs, f0_policies, f0_resources
from rubato.sim import simulate

P = {"arrival": Provenance.SYNTHETIC}


def day(urgent: bool, quota: int = 3):
    res = [
        ResourceSpec("rate", "requests", 1, "rolling", 1),
        ResourceSpec("quota", "requests", quota, "calendar", 5),
    ]
    pols = f0_policies()
    runs = {p.label: simulate(f0_jobs(urgent), res, p, horizon=F0_HORIZON) for p in pols.values()}
    return runs, {k: summarize(v) for k, v in runs.items()}


def test_day_where_reserving_helps():
    _, m = day(urgent=True)
    assert m["reserva@1"]["urgent"]["done_on_time"] == 1 > m["fifo@1"]["urgent"]["done_on_time"] == 0
    assert winners(m)["urgent_on_time"] == ["reserva@1"]


def test_day_where_reserving_hurts_background_and_reference_wins_visibly():
    runs, m = day(urgent=False)
    assert m["fifo@1"]["background"]["done_on_time"] == 3
    assert m["reserva@1"]["background"]["done_on_time"] == 1
    w = winners(m)
    assert w["background_on_time"] == ["fifo@1"]
    assert w["urgent_on_time"] == ["fifo@1", "reserva@1"]  # tie is shown, not hidden
    diff = what_if(runs["fifo@1"], runs["reserva@1"])
    assert sorted(diff["lose"]) == ["analisis-2", "analisis-3"] and diff["gain"] == []


def test_day_where_it_makes_no_difference():
    _, m = day(urgent=True, quota=10)
    keys = ("done_on_time", "done", "pending")
    assert {k: m["fifo@1"]["urgent"][k] for k in keys} == {k: m["reserva@1"]["urgent"][k] for k in keys}
    assert m["fifo@1"]["background"]["done_on_time"] == m["reserva@1"]["background"]["done_on_time"] == 3


def test_radar_reference_orders_by_priority_then_eligibility():
    def item(j, prio, since):
        return ReadyItem(j, "radar", "c", False, prio, 0, None, "s", 1, since, {"requests": 1}, {}, 1)

    ready = [item("a", 0, 5), item("b", 10, 9), item("c", 0, 1), item("d", 10, 2)]
    assert [r.job_id for r in RadarPolicy().order(10, ready)] == ["d", "b", "c", "a"]


def test_aging_limit_lets_old_background_ignore_the_speculative_reserve():
    res = [ResourceSpec("quota", "requests", 3, "calendar", 100)]
    jobs = [
        Job(f"a{i}", "radar", "bg", 0, (Step("s", {"requests": 1}, duration_estimate=10),), provenance=P)
        for i in range(3)
    ]
    no_aging = simulate(jobs, res, ReservePolicy(reserve={"quota": 2}), horizon=100)
    aging = simulate(jobs, res, ReservePolicy(reserve={"quota": 2}, aging_limit=30), horizon=100)
    assert sum(r.status == "done" for r in no_aging.jobs) == 1
    assert sum(r.status == "done" for r in aging.jobs) == 3
    assert sorted(a.start for a in aging.attempts) == [0, 30, 30]


def test_impossible_deadline_drop_or_background():
    res = [ResourceSpec("q", "requests", 5, "total")]
    late = Job(
        "u",
        "radar",
        "application",
        0,
        (Step("cv", {"requests": 1}, duration_estimate=10),),
        urgent=True,
        deadline=5,
        provenance=P,
    )
    dropped = simulate([late], res, ReservePolicy(impossible="drop"))
    d = dropped.decisions[0]
    assert (d.reason, d.value["earliest_finish"]) == (Reason.DEADLINE_IMPOSSIBLE, 10)
    assert dropped.by_id()["u"].status == "closed"
    bg = simulate([late], res, ReservePolicy(impossible="background"))
    assert bg.by_id()["u"].status == "done" and not bg.by_id()["u"].on_time


def test_backfill_records_which_job_was_overtaken():
    res = [ResourceSpec("tpm", "tokens_in", 10_000, "rolling", 60)]
    jobs = [
        Job("first", "radar", "bg", 0, (Step("s", {"tokens_in": 8000}),), provenance=P),
        Job("big", "radar", "bg", 1, (Step("s", {"tokens_in": 6000}),), provenance=P),
        Job("small", "radar", "bg", 2, (Step("s", {"tokens_in": 1000}),), provenance=P),
    ]
    out = simulate(jobs, res, FifoPolicy())
    d = next(d for d in out.decisions if d.job_id == "small")
    assert d.reason == Reason.BACKFILL and "big" in d.detail
    text = explain_job(out, "big", unit="turn")
    assert "DEFERRED [rate_limited]" in text and "reconsider t=60" in text


def test_optimizer_matches_f0_by_hand_and_bounds_online_policies():
    urgent = enumerate_best(f0_jobs(True), f0_resources(), F0_HORIZON)
    twin = enumerate_best(f0_jobs(False), f0_resources(), F0_HORIZON)
    assert urgent.score == (1, 1, 2)
    assert twin.score == (0, 3, 3)
    for jobs, best in ((f0_jobs(True), urgent), (f0_jobs(False), twin)):
        for pol in f0_policies().values():
            m = summarize(simulate(jobs, f0_resources(), pol, horizon=F0_HORIZON))
            online = (m["urgent"]["done_on_time"], m["background"]["done_on_time"])
            assert online <= best.score[:2]


def random_instance(seed: int):
    r = random.Random(seed)
    jobs = []
    for i in range(r.randint(2, 4)):
        urgent = r.random() < 0.35
        arrival = r.randint(0, 3)
        n = r.choice((1, 1, 2)) if urgent else 1
        steps = []
        for k in range(n):
            dur = r.randint(1, 2)
            ok = r.random() > 0.1
            steps.append(
                Step(
                    f"s{k}",
                    {"requests": 1},
                    (f"s{k - 1}",) if k else (),
                    None,
                    None,
                    dur,
                    1,
                    (Outcome(dur, {"requests": 1}, Status.OK if ok else Status.INVALID),),
                )
            )
        deadline = arrival + r.randint(1, 4) if r.random() < 0.8 else None
        jobs.append(
            Job(
                f"j{i}",
                "c",
                "u" if urgent else "b",
                arrival,
                tuple(steps),
                urgent=urgent,
                deadline=deadline,
                provenance=P,
            )
        )
    res = [
        ResourceSpec("rate", "requests", r.randint(1, 2), "rolling", r.randint(1, 2)),
        ResourceSpec("quota", "requests", r.randint(2, 4), "total"),
        ResourceSpec("conc", "concurrent", 2, "concurrency"),
    ]
    return jobs, res


@pytest.mark.parametrize("seed", range(25))
def test_cpsat_matches_enumeration_on_small_instances(seed):
    pytest.importorskip("ortools")
    from rubato.optimize import cpsat_best

    jobs, res = random_instance(seed)
    horizon = 6
    assert cpsat_best(jobs, res, horizon).score == enumerate_best(jobs, res, horizon).score
