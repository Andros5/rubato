"""F0: the five-turn acceptance case, solved by hand in PLAN_RUBATO.md section F0."""

from rubato.model import Reason
from rubato.scenarios import F0_HORIZON, f0_jobs, f0_policies, f0_resources
from rubato.sim import simulate


def run(policy: str, urgent: bool = True, invalid_cv: bool = False):
    return simulate(
        f0_jobs(urgent, invalid_cv), f0_resources(), f0_policies()[policy], horizon=F0_HORIZON
    ).by_id()


def counts(res):
    done = [r for r in res.values() if r.status == "done"]
    on_time = [r for r in done if r.on_time]
    a = sum(1 for r in on_time if r.job.cls == "analysis")
    u = sum(1 for r in on_time if r.job.urgent)
    return a, u, len(done)


def test_fifo_finishes_three_analyses_and_no_urgent():
    res = run("fifo")
    assert counts(res) == (3, 0, 3)
    assert res["candidatura-1"].status == "pending"
    assert res["candidatura-1"].reason == Reason.CAPACITY_EXHAUSTED


def test_reserve_two_requests_finishes_one_analysis_and_the_urgent_job():
    res = run("reserva")
    assert counts(res) == (1, 1, 2)
    assert res["candidatura-1"].finished_at == 5
    sim = simulate(f0_jobs(), f0_resources(), f0_policies()["reserva"], horizon=F0_HORIZON)
    first = next(d for d in sim.decisions if d.job_id == "analisis-2")
    # blocked by the rate (analisis-1 took turn 1) and by the reserve; the reserve clears last
    assert (first.t, first.reason, first.next_t) == (0, Reason.DEADLINE_RESERVE, 3)
    assert {b["resource"] for b in first.value["blocks"]} == {"rate", "quota"}
    assert "2 held for urgent work" in first.detail


def test_twin_day_without_urgency_releases_reserve_at_turn_4_late():
    res = run("reserva", urgent=False)
    done = {k: r.finished_at for k, r in res.items() if r.status == "done"}
    assert done == {"analisis-1": 1, "analisis-2": 4, "analisis-3": 5}
    assert [r.on_time for r in res.values()] == [True, False, False]


def test_http_200_with_invalid_output_consumes_but_does_not_finish():
    sim = simulate(
        f0_jobs(True, invalid_cv=True), f0_resources(), f0_policies()["reserva"], horizon=F0_HORIZON
    )
    res = sim.by_id()
    assert res["candidatura-1"].status == "failed"
    # analysis-1 + invalid CV, then the freed capacity goes to analysis-2 (late); no letter
    assert sim.consumed["quota"] == 3
    assert not [a for a in sim.attempts if a.step_id == "letter"]
    assert res["analisis-2"].finished_at == 5
    letter = [d for d in sim.decisions if d.job_id == "candidatura-1" and d.step_id == "letter"]
    assert letter[-1].reason == Reason.DEPENDENCY_FAILED


def test_runs_without_credentials(monkeypatch):
    for k in ("GEMINI_API_KEY", "GOOGLE_API_KEY", "OPENAI_API_KEY", "ANTHROPIC_API_KEY"):
        monkeypatch.delenv(k, raising=False)
    assert counts(run("reserva")) == (1, 1, 2)
