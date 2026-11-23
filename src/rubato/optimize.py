"""Offline reference: the best schedule with perfect knowledge of arrivals and outcomes.

It is a *bound* for small instances, not a policy: the online policies never see
future arrivals. Discrete time (integer instants), first-attempt outcomes taken
from the trace (a step whose first attempt is not valid cannot complete).

Objective (lexicographic, encoded with weights): urgent jobs on time, then
background jobs on time, then jobs finished at all.

Two solvers share the same model: ``enumerate_best`` (exhaustive, tiny cases) and
``cpsat_best`` (OR-Tools CP-SAT, optional extra ``rubato[opt]``).
"""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass
from itertools import combinations

from .model import Job
from .resources import ResourceSpec


@dataclass(frozen=True)
class OptStep:
    key: tuple[str, str]
    job: str
    arrival: int
    duration: int
    need: dict[str, int]  # per resource id
    deps: tuple[tuple[str, str], ...]
    feasible: bool


@dataclass
class OptResult:
    score: tuple[int, int, int]  # urgent on time, background on time, done
    starts: dict[tuple[str, str], int]
    solver: str


def _steps(jobs: Sequence[Job], resources: Sequence[ResourceSpec]) -> list[OptStep]:
    out = []
    for j in jobs:
        for s in j.steps:
            o, _ = s.outcome(1)
            amount = {**s.admission_amount()}
            need = {r.id: r.need_of(amount) for r in resources if r.applies(j.client, s.model)}
            out.append(
                OptStep(
                    (j.id, s.id),
                    j.id,
                    j.arrival,
                    max(1, o.duration),
                    need,
                    tuple((j.id, d) for d in s.depends_on),
                    o.valid,
                )
            )
    return out


def _windows(r: ResourceSpec, horizon: int, t: int) -> list[tuple[int, int]]:
    """Windows [a, b) that contain instant t, for the admission-time accounting rule."""
    if r.window == "rolling":
        assert r.length
        return [(t - r.length + 1, t + 1)]
    if r.window == "calendar":
        if r.reset_tz:
            raise ValueError("the offline optimizer supports calendar windows without time zones only")
        a = r.window_start(t)
        return [(a, a + (r.length or horizon))]
    if r.window == "total":
        return [(0, horizon)]
    return []


def score_of(jobs: Sequence[Job], steps: list[OptStep], starts: dict) -> tuple[int, int, int]:
    by_job: dict[str, list[OptStep]] = {}
    for s in steps:
        by_job.setdefault(s.job, []).append(s)
    u = b = d = 0
    for j in jobs:
        ss = by_job[j.id]
        if all(s.key in starts for s in ss):
            d += 1
            fin = max(starts[s.key] + s.duration for s in ss)
            if j.deadline is None or fin <= j.deadline:
                if j.urgent:
                    u += 1
                else:
                    b += 1
    return (u, b, d)


def _fits(resources, steps_by_key, starts: dict, cand: list[OptStep], t: int, horizon: int) -> bool:
    for r in resources:
        add = sum(s.need.get(r.id, 0) for s in cand)
        if not add:
            continue
        if r.window == "concurrency":
            running = sum(
                steps_by_key[k].need.get(r.id, 0)
                for k, st in starts.items()
                if st <= t < st + steps_by_key[k].duration
            )
            if running + add > r.capacity:
                return False
            continue
        for a, b in _windows(r, horizon, t):
            used = sum(steps_by_key[k].need.get(r.id, 0) for k, st in starts.items() if a <= st < b)
            if used + add > r.capacity:
                return False
    return True


def enumerate_best(jobs: Sequence[Job], resources: Sequence[ResourceSpec], horizon: int) -> OptResult:
    steps = _steps(jobs, resources)
    by_key = {s.key: s for s in steps}
    best: list = [(-1, -1, -1), {}]

    def rec(t: int, starts: dict) -> None:
        if t >= horizon:
            sc = score_of(jobs, steps, starts)
            if sc > best[0]:
                best[0], best[1] = sc, dict(starts)
            return
        eligible = [
            s
            for s in steps
            if s.feasible
            and s.key not in starts
            and s.arrival <= t
            and t + s.duration <= horizon
            and all(d in starts and starts[d] + by_key[d].duration <= t for d in s.deps)
        ]
        for k in range(len(eligible), -1, -1):
            for cand in combinations(eligible, k):
                if _fits(resources, by_key, starts, list(cand), t, horizon):
                    nxt = dict(starts)
                    for s in cand:
                        nxt[s.key] = t
                    rec(t + 1, nxt)

    rec(0, {})
    return OptResult(best[0], best[1], "enumeration")


def cpsat_best(
    jobs: Sequence[Job], resources: Sequence[ResourceSpec], horizon: int, time_limit: float = 10.0
) -> OptResult:
    from ortools.sat.python import cp_model

    steps = _steps(jobs, resources)
    m = cp_model.CpModel()
    x: dict[tuple, cp_model.IntVar] = {}
    present = {}
    for s in steps:
        for t in range(s.arrival, horizon - s.duration + 1):
            if s.feasible:
                x[(s.key, t)] = m.NewBoolVar(f"x{s.key}{t}")
        present[s.key] = m.NewBoolVar(f"p{s.key}")
        m.Add(sum(x[(s.key, t)] for t in range(horizon) if (s.key, t) in x) == present[s.key])
    by_key = {s.key: s for s in steps}

    def start(k):
        return sum(t * x[(k, t)] for t in range(horizon) if (k, t) in x)

    for s in steps:
        for d in s.deps:
            m.AddImplication(present[s.key], present[d])
            m.Add(start(s.key) >= start(d) + by_key[d].duration).OnlyEnforceIf(present[s.key])
    for r in resources:
        for t in range(horizon):
            if r.window == "concurrency":
                terms = [
                    s.need.get(r.id, 0) * x[(s.key, t0)]
                    for s in steps
                    for t0 in range(max(0, t - s.duration + 1), t + 1)
                    if (s.key, t0) in x
                ]
                if terms:
                    m.Add(sum(terms) <= r.capacity)
                continue
            for a, b in _windows(r, horizon, t):
                terms = [
                    s.need.get(r.id, 0) * x[(s.key, t0)]
                    for s in steps
                    for t0 in range(max(0, a), min(b, horizon))
                    if (s.key, t0) in x
                ]
                if terms:
                    m.Add(sum(terms) <= r.capacity)
    done, ontime = {}, {}
    n = len(jobs)
    for j in jobs:
        ss = [s for s in steps if s.job == j.id]
        done[j.id] = m.NewBoolVar(f"d{j.id}")
        ontime[j.id] = m.NewBoolVar(f"o{j.id}")
        for s in ss:
            m.AddImplication(done[j.id], present[s.key])
            if j.deadline is None:
                m.AddImplication(ontime[j.id], present[s.key])
            else:
                m.Add(
                    sum(
                        x[(s.key, t)]
                        for t in range(horizon)
                        if (s.key, t) in x and t + s.duration <= j.deadline
                    )
                    >= ontime[j.id]
                )
        m.AddImplication(ontime[j.id], done[j.id])
    w_b = n + 1
    w_u = w_b * (n + 1)
    m.Maximize(
        sum(w_u * ontime[j.id] for j in jobs if j.urgent)
        + sum(w_b * ontime[j.id] for j in jobs if not j.urgent)
        + sum(done[j.id] for j in jobs)
    )
    solver = cp_model.CpSolver()
    solver.parameters.max_time_in_seconds = time_limit
    solver.parameters.num_workers = 1  # deterministic
    st = solver.Solve(m)
    if st not in (cp_model.OPTIMAL, cp_model.FEASIBLE):
        raise RuntimeError("CP-SAT found no solution")
    starts = {k: t for (k, t), v in x.items() if solver.Value(v)}
    return OptResult(
        score_of(jobs, steps, starts), starts, "cp-sat" + ("" if st == cp_model.OPTIMAL else "(feasible)")
    )
