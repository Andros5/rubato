"""F2: hand-checked simulator cases."""

from datetime import datetime
from zoneinfo import ZoneInfo

from rubato.metrics import summarize
from rubato.model import Job, Outcome, Provenance, Reason, Status, Step
from rubato.policies import FifoPolicy, ReservePolicy
from rubato.resources import ResourceSpec
from rubato.sim import ExternalEvent, simulate
from rubato.synth import SynthConfig, generate
from rubato.tariffs import Tariff

P = {"arrival": Provenance.SYNTHETIC}
FIFO = FifoPolicy()


def J(jid, arrival, *steps, **kw):
    return Job(jid, kw.pop("client", "app"), kw.pop("cls", "bg"), arrival, tuple(steps), provenance=P, **kw)


def S(sid="s", req=1, tin=0, dur=1, outcomes=(), deps=(), attempts=None, model="m"):
    est = {"requests": req, "tokens_in": tin}
    return Step(sid, est, tuple(deps), model, None, dur, attempts or max(1, len(outcomes)), tuple(outcomes))


def starts(res, jid):
    return [a.start for a in res.attempts if a.job_id == jid]


def test_two_simultaneous_arrivals_follow_trace_order_deterministically():
    rate = [ResourceSpec("rate", "requests", 1, "rolling", 1)]
    a, b = J("a", 0, S()), J("b", 0, S())
    r1 = simulate([a, b], rate, FIFO)
    r2 = simulate([b, a], rate, FIFO)
    assert (starts(r1, "a"), starts(r1, "b")) == ([0], [1])
    assert (starts(r2, "b"), starts(r2, "a")) == ([0], [1])
    assert [d.to_dict() for d in simulate([a, b], rate, FIFO).decisions] == [
        d.to_dict() for d in r1.decisions
    ]


def zurich(*args):
    return int(datetime(*args, tzinfo=ZoneInfo("Europe/Zurich")).timestamp())


def test_midnight_reset_in_local_zone_and_call_spanning_reset_stays_in_old_window():
    rpd = [ResourceSpec("rpd", "requests", 2, "calendar", reset_tz="Europe/Zurich")]
    t = zurich(2026, 11, 24, 23, 59, 50)
    midnight = zurich(2026, 11, 25, 0, 0, 0)
    jobs = [J(x, t, S(dur=30)) for x in ("a", "b", "c")]
    res = simulate(jobs, rpd, FIFO)
    assert starts(res, "a") == [t] and starts(res, "b") == [t]
    deferral = next(d for d in res.decisions if d.job_id == "c")
    assert (deferral.reason, deferral.next_t) == (Reason.CAPACITY_EXHAUSTED, midnight)
    # a and b are still running at midnight, but their usage belongs to the previous day
    assert starts(res, "c") == [midnight]


def test_daylight_saving_day_has_23_hours():
    spec = ResourceSpec("rpd", "requests", 1, "calendar", reset_tz="Europe/Zurich")
    t = zurich(2026, 3, 29, 12, 0, 0)
    assert spec.next_reset(t) - spec.window_start(t) == 23 * 3600


def test_insufficient_tokens_waits_for_rolling_window_and_oversized_step_closes():
    tpm = [ResourceSpec("tpm", "tokens_in", 10_000, "rolling", 60)]
    jobs = [J("a", 0, S(tin=8000)), J("b", 0, S(tin=5000)), J("huge", 0, S(tin=12_000))]
    res = simulate(jobs, tpm, FIFO)
    d = next(d for d in res.decisions if d.job_id == "b")
    assert (d.reason, d.resource, d.next_t) == (Reason.RATE_LIMITED, "tpm", 60)
    assert d.value["blocks"][0]["available"] == 2000
    assert starts(res, "b") == [60]
    h = next(d for d in res.decisions if d.job_id == "huge")
    assert h.reason == Reason.NEVER_FITS and "raise the limit" in h.detail
    assert res.by_id()["huge"].status == "closed"


def test_cancellation_closes_future_steps_but_running_attempt_consumes():
    total = [ResourceSpec("q", "requests", 10, "total")]
    job = J("j", 0, S("s1", dur=10), S("s2", deps=("s1",)))
    res = simulate([job], total, FIFO, events=[ExternalEvent(5, "cancel", job_id="j")])
    assert res.by_id()["j"].status == "cancelled"
    assert res.consumed["q"] == 1
    assert [(d.step_id, d.reason) for d in res.decisions if d.reason == Reason.CANCELLED] == [
        ("s2", Reason.CANCELLED)
    ]
    assert not [a for a in res.attempts if a.step_id == "s2"]


def test_failed_dependency_after_max_attempts():
    total = [ResourceSpec("q", "requests", 10, "total")]
    err = Outcome(1, {"requests": 1}, Status.ERROR)
    job = J("j", 0, S("s1", outcomes=(err, err), attempts=2), S("s2", deps=("s1",)))
    res = simulate([job], total, FIFO)
    assert res.by_id()["j"].status == "failed"
    assert res.consumed["q"] == 2
    reasons = {(d.step_id, d.reason) for d in res.decisions}
    assert ("s1", Reason.MAX_ATTEMPTS) in reasons and ("s2", Reason.DEPENDENCY_FAILED) in reasons


def test_exhaustion_by_two_limits_reports_both_and_waits_for_the_last():
    lim = [
        ResourceSpec("rpm", "requests", 2, "rolling", 60),
        ResourceSpec("tpm", "tokens_in", 10_000, "rolling", 60),
    ]
    jobs = [J("a", 0, S(tin=1000)), J("a2", 30, S(tin=8000)), J("b", 40, S(tin=5000))]
    res = simulate(jobs, lim, FIFO)
    d = next(d for d in res.decisions if d.job_id == "b")
    assert {b["resource"]: b["next_t"] for b in d.value["blocks"]} == {"rpm": 60, "tpm": 90}
    assert (d.reason, d.resource, d.next_t) == (Reason.RATE_LIMITED, "tpm", 90)
    assert starts(res, "b") == [90]


def test_no_loop_when_nothing_is_eligible():
    total = [ResourceSpec("q", "requests", 1, "total")]
    res = simulate([J("a", 0, S()), J("b", 0, S())], total, FIFO)
    assert res.by_id()["b"].status == "pending"
    assert res.by_id()["b"].reason == Reason.CAPACITY_EXHAUSTED
    down = simulate(
        [J("c", 0, S(), client="x")], total, FIFO, events=[ExternalEvent(0, "client_down", client="x")]
    )
    assert down.by_id()["c"].status == "pending" and down.by_id()["c"].reason is None
    assert any(d.reason == Reason.CLIENT_UNAVAILABLE for d in down.decisions)


def test_client_restart_resumes_work():
    total = [ResourceSpec("q", "requests", 5, "total")]
    ev = [ExternalEvent(0, "client_down", client="x"), ExternalEvent(50, "client_up", client="x")]
    res = simulate([J("c", 0, S(), client="x")], total, FIFO, events=ev)
    assert starts(res, "c") == [50]


def test_429_pauses_the_model_and_respects_retry_after():
    rate = [ResourceSpec("rpm", "requests", 10, "rolling", 60)]
    o429 = Outcome(1, {"requests": 1}, Status.RATE_LIMITED, retry_after=30)
    ok = Outcome(1, {"requests": 1})
    res = simulate([J("a", 0, S(outcomes=(o429, ok), attempts=2)), J("b", 2, S())], rate, FIFO)
    assert starts(res, "a") == [0, 31] and starts(res, "b") == [31]
    pause = next(d for d in res.decisions if d.job_id == "b")
    assert (pause.reason, pause.next_t) == (Reason.PROVIDER_PAUSE, 31)


def test_actual_usage_above_estimate_is_settled_and_limits_later_work():
    tpm = [ResourceSpec("tpm", "tokens_in", 10_000, "rolling", 60)]
    heavy = S(tin=2000, outcomes=(Outcome(1, {"requests": 1, "tokens_in": 9000}),))
    light = S(tin=2000, outcomes=(Outcome(1, {"requests": 1, "tokens_in": 500}),))
    res = simulate([J("a", 0, heavy), J("b", 2, S(tin=2000))], tpm, FIFO)
    assert starts(res, "b") == [60]
    res2 = simulate([J("a", 0, light), J("b", 2, S(tin=2000))], tpm, FIFO)
    assert starts(res2, "b") == [2]


def test_money_budget_in_integer_micro_units_with_versioned_tariff():
    tariff = Tariff("sim-2026-11", "CHF", {"m": (1, 4)})  # CHF per 1M tokens in/out
    budget = [ResourceSpec("budget", "cost_micro", 30_000, "total")]
    step = Step(
        "s",
        {"requests": 1, "tokens_in": 10_000, "tokens_out": 1000},
        model="m",
        outcomes=(Outcome(1, {"requests": 1, "tokens_in": 12_000, "tokens_out": 1000}),),
    )
    res = simulate([J("a", 0, step), J("b", 0, step)], budget, FIFO, tariff=tariff)
    assert res.attempts[0].admitted["cost_micro"] == 14_000  # 10000*1 + 1000*4 reserved
    # settled to the actual 12000*1 + 1000*4 = 16000 each: an estimate-only cap can be overrun,
    # which is why strict caps reserve an enforceable maximum (docs/decisiones.md D7)
    assert res.consumed["budget"] == 2 * 16_000
    assert all(isinstance(v, int) for v in res.consumed.values())


def test_same_seed_versions_and_inputs_give_same_sequence_and_metrics():
    def run():
        tr = generate(SynthConfig(seed=11))
        pol = ReservePolicy(reserve={"rpd": 4}, release_offset=17 * 3600)
        res = simulate(tr.jobs, tr.resources, pol)
        m = summarize(res)
        m.pop("planner")
        return [d.to_dict() for d in res.decisions], m

    assert run() == run()
