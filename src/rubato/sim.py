"""Deterministic discrete-event simulator.

Event order at the same instant (documented in docs/decisiones.md):
  0 attempt finishes  -> releases concurrency, settles usage in the admission window
  1 pause ends
  2 cancellations
  3 client availability changes
  4 arrivals (jobs become known)
  5 wake-ups
Then ``decidir`` runs once for that instant. Window resets need no event: the
ledger computes availability as a function of time, so a finish and a reset at
the same instant are independent (the finish settles in its admission window).
Ties inside a class are broken by a stable sequence number (trace order).
"""

from __future__ import annotations

import heapq
import time
from collections import defaultdict, deque
from collections.abc import Sequence
from dataclasses import dataclass, field, replace

from .core import Capacity, Demand, StepDemand, decidir, pause_key, urgent_for
from .model import (
    Action,
    Decision,
    Job,
    Outcome,
    ReadyItem,
    Reason,
    Status,
    critical_path,
    find_cycle,
    tokens_total,
)
from .resources import Ledger, ResourceSpec
from .risk import Estimate, estimate_duration, forecast

FINISH, PAUSE_END, CANCEL, CLIENT, ARRIVAL, CHANGE, WAKEUP = range(7)


@dataclass
class ExternalEvent:
    t: int
    kind: str  # cancel | client_down | client_up | pause | consume
    job_id: str | None = None
    client: str | None = None
    # for pause: ("provider", p) | ("model", m) | ("client", c) | ("all", "*")
    scope: tuple[str, str] | None = None
    until: int | None = None
    usage: dict[str, int] | None = None  # for consume: usage by applications outside Rubato
    model: str | None = None
    provider: str | None = None


@dataclass(frozen=True)
class RuleChange:
    t: int
    policy: object | None = None
    capacities: dict[str, int] = field(default_factory=dict)


@dataclass
class StepState:
    status: str = "waiting"  # waiting | ready | running | done | failed | closed
    attempts: int = 0
    ready_since: int | None = None
    started: list[int] = field(default_factory=list)
    finished_at: int | None = None
    reason: str | None = None  # closing / last deferral reason
    detail: str = ""
    next_t: int | None = None


@dataclass
class AttemptRecord:
    attempt_id: str
    job_id: str
    step_id: str
    attempt: int
    start: int
    end: int
    status: str
    usage: dict[str, int]
    admitted: dict[str, int]
    from_trace: bool
    cost_currency: str | None = None
    cost_source: str | None = None
    settled: bool = False


@dataclass
class JobResult:
    job: Job
    status: str  # done | failed | closed | cancelled | pending
    finished_at: int | None
    first_start: int | None
    reason: str | None

    @property
    def on_time(self) -> bool:
        return (
            self.status == "done"
            and self.finished_at is not None
            and (self.job.deadline is None or self.finished_at <= self.job.deadline)
        )


@dataclass
class SimResult:
    policy: str
    policy_config: dict
    jobs: list[JobResult]
    decisions: list[Decision]
    attempts: list[AttemptRecord]
    resources: list[ResourceSpec]
    consumed: dict[str, int]
    end_t: int
    decision_latency_us: list[float]
    max_queue: int
    usage_log: list[tuple[str, int, int, bool, str]] = field(
        default_factory=list
    )  # rid, t, amount, urgent, owner
    risk: list[dict] = field(default_factory=list)

    def by_id(self) -> dict[str, JobResult]:
        return {r.job.id: r for r in self.jobs}


class Simulator:
    def __init__(
        self,
        jobs: Sequence[Job],
        resources: Sequence[ResourceSpec],
        policy,
        events: Sequence[ExternalEvent] = (),
        horizon: int | None = None,
        max_iterations: int = 1_000_000,
        log_all_deferrals: bool = False,
        tariff=None,
        change: RuleChange | None = None,
    ):
        ids = [j.id for j in jobs]
        if len(set(ids)) != len(ids):
            raise ValueError("duplicate job ids")
        for j in jobs:
            cycle = find_cycle({s.id: s.depends_on for s in j.steps})
            if cycle:
                raise ValueError(f"job {j.id}: dependency cycle {' -> '.join(cycle)}")
        self.jobs = {j.id: j for j in jobs}
        self.seq = {j.id: i for i, j in enumerate(jobs)}
        self.resources = list(resources)
        self.policy = policy
        self.events = list(events)
        self.horizon = horizon
        self.max_iterations = max_iterations
        self.log_all = log_all_deferrals
        self.tariff = tariff
        self.change = change

    def _admission(self, step, t: int) -> dict[str, int]:
        amt = step.admission_amount()
        return self.tariff.with_cost(step.model, amt, t) if self.tariff else amt

    def _usage(self, step, usage: dict[str, int], t: int) -> dict[str, int]:
        u = tokens_total(usage)
        return self.tariff.with_cost(step.model, u, t) if self.tariff else u

    # ------------------------------------------------------------------
    def run(self) -> SimResult:
        ledger = Ledger(self.resources)
        cap = Capacity(ledger)
        policy = self.policy
        heap: list[tuple[int, int, int, object]] = []
        counter = 0

        def push(t: int, kind: int, payload) -> None:
            nonlocal counter
            heapq.heappush(heap, (t, kind, counter, payload))
            counter += 1

        known: set[str] = set()
        state: dict[tuple[str, str], StepState] = {}
        job_status: dict[str, str] = {}
        client_down: set[str] = set()
        decisions: list[Decision] = []
        attempts: list[AttemptRecord] = []
        last_logged: dict[tuple[str, str], tuple] = {}
        wakeups: set[int] = set()
        latencies: list[float] = []
        max_queue = 0
        risk = []
        last_risk = {}
        duration_history = defaultdict(lambda: deque(maxlen=100))

        for j in sorted(
            self.jobs.values(), key=lambda j: j.known_at if j.known_at is not None else j.arrival
        ):
            push(j.known_at if j.known_at is not None else j.arrival, ARRIVAL, j.id)
            for s in j.steps:
                state[(j.id, s.id)] = StepState()
        for ev in self.events:
            kind = {
                "cancel": CANCEL,
                "client_down": CLIENT,
                "client_up": CLIENT,
                "pause": CLIENT,
                "consume": CLIENT,
            }[ev.kind]
            push(ev.t, kind, ev)
        if self.change:
            push(self.change.t, CHANGE, self.change)

        def refresh_ready(job: Job, t: int) -> None:
            for s in job.steps:
                st = state[(job.id, s.id)]
                if st.status != "waiting":
                    continue
                deps = [state[(job.id, d)] for d in s.depends_on]
                if any(d.status in ("failed", "closed") for d in deps):
                    st.status = "closed"
                    st.reason = Reason.DEPENDENCY_FAILED
                    st.detail = "a step it depends on did not complete"
                    decisions.append(
                        Decision(
                            t,
                            job.id,
                            s.id,
                            st.attempts + 1,
                            policy.label,
                            Action.CLOSE,
                            Reason.DEPENDENCY_FAILED,
                            detail=st.detail,
                        )
                    )
                elif all(d.status == "done" for d in deps):
                    st.status = "ready"
                    st.ready_since = t

        def close_job_if_finished(job: Job, t: int) -> None:
            sts = [state[(job.id, s.id)] for s in job.steps]
            if all(s.status == "done" for s in sts):
                job_status[job.id] = "done"
            elif any(s.status in ("failed", "closed") for s in sts) and not any(
                s.status in ("ready", "running") for s in sts
            ):
                # close remaining waiting steps whose dependencies failed
                refresh_ready(job, t)
                sts = [state[(job.id, s.id)] for s in job.steps]
                if not any(s.status in ("ready", "running", "waiting") for s in sts):
                    if job_status.get(job.id) in (None, "active"):
                        job_status[job.id] = "failed"

        def remaining(job: Job, now: int) -> tuple[dict[str, int], int, tuple[StepDemand, ...]]:
            need: dict[str, int] = {}
            durations = {}
            pending = []
            for s in job.steps:
                st = state[(job.id, s.id)]
                if st.status in ("waiting", "ready"):
                    amount = self._admission(s, now)
                    pending.append(StepDemand(s.id, s.model, amount, s.provider))
                    for k, v in amount.items():
                        need[k] = need.get(k, 0) + v
                    durations[s.id] = s.duration_estimate
                elif st.status == "running":
                    durations[s.id] = max(0, s.duration_estimate - (now - st.started[-1]))
            duration, _ = critical_path({s.id: s.depends_on for s in job.steps}, durations)
            return need, duration, tuple(pending)

        def track_risk(now):
            for jid in sorted(known, key=self.seq.__getitem__):
                job = self.jobs[jid]
                if not job.urgent or job.deadline is None:
                    continue
                steps = {}
                for step in job.steps:
                    st = state[jid, step.id]
                    if st.status not in {"waiting", "ready", "running"}:
                        continue
                    duration, samples = estimate_duration(
                        step.duration_estimate, list(duration_history[job.cls, step.model])
                    )
                    elapsed = now - st.started[-1] if st.status == "running" else 0
                    unavailable = job.client in client_down and st.status != "running"
                    next_t = st.next_t if st.status == "ready" and st.next_t and st.next_t > now else None
                    blocking = (
                        Reason.CLIENT_UNAVAILABLE
                        if unavailable
                        else st.reason
                        if st.status == "ready"
                        else None
                    )
                    steps[step.id] = Estimate(
                        step.depends_on,
                        max(0, duration - elapsed),
                        None if unavailable else next_t,
                        blocking,
                        samples,
                    )
                if not steps:
                    continue
                assessment = forecast(now, job.deadline, steps)
                signature = (
                    assessment["status"],
                    assessment["next_reconsider"],
                    assessment["blocking"],
                    assessment["expected_finish"],
                    assessment["samples_by_step"],
                )
                if last_risk.get(jid) != signature:
                    risk.append({"job": jid, "client": job.client, **assessment})
                    last_risk[jid] = signature

        iterations = 0
        t = 0
        while heap:
            iterations += 1
            if iterations > self.max_iterations:
                raise RuntimeError("simulation exceeded max_iterations; check for a wake-up loop")
            t = heap[0][0]
            if self.horizon is not None and t > self.horizon:
                break
            while heap and heap[0][0] == t:
                _, kind, _, payload = heapq.heappop(heap)
                if kind == CHANGE:
                    if payload.policy is not None:
                        policy = payload.policy
                    for rid, limit in payload.capacities.items():
                        ledger.specs[rid] = replace(ledger.specs[rid], capacity=limit)
                    last_logged.clear()
                elif kind == FINISH:
                    rec: AttemptRecord = payload  # type: ignore[assignment]
                    job = self.jobs[rec.job_id]
                    step = job.step(rec.step_id)
                    st = state[(rec.job_id, rec.step_id)]
                    out, _ = step.outcome(rec.attempt)
                    if rec.from_trace:
                        duration_history[job.cls, step.model].append(out.duration)
                    rec.usage = self._usage(step, out.usage, rec.start)
                    rec.status = str(out.status)
                    if "cost_micro" in out.usage:
                        rec.cost_source = "explicit"
                    elif self.tariff:
                        rec.cost_source = (
                            "tariff"
                            if "cost_micro" in rec.usage
                            else "partial"
                            if {"tokens_in", "tokens_out"} & out.usage.keys()
                            else "unknown"
                        )
                    ledger.settle(rec.attempt_id, rec.usage)
                    rec.settled = True
                    if st.status == "cancelled_running":
                        st.status = "closed"
                        st.reason = Reason.CANCELLED
                    elif out.status == Status.OK:
                        st.status = "done"
                        st.finished_at = t
                    else:
                        if out.status == Status.RATE_LIMITED:
                            until = t + (out.retry_after or 1)
                            key = pause_key(step.model, step.provider)
                            cap.pauses[key] = max(cap.pauses.get(key, 0), until)
                            push(until, PAUSE_END, key)
                        if st.attempts >= step.max_attempts:
                            st.status = "failed"
                            st.reason = Reason.MAX_ATTEMPTS
                            st.detail = f"{st.attempts} attempts, last status {out.status}"
                            decisions.append(
                                Decision(
                                    t,
                                    job.id,
                                    step.id,
                                    st.attempts,
                                    policy.label,
                                    Action.CLOSE,
                                    Reason.MAX_ATTEMPTS,
                                    detail=st.detail,
                                )
                            )
                        else:
                            st.status = "ready"
                            st.ready_since = t
                    refresh_ready(job, t)
                    close_job_if_finished(job, t)
                elif kind == PAUSE_END:
                    pass  # pauses are evaluated by time; the event only forces a decision round
                elif kind == ARRIVAL:
                    jid = payload  # type: ignore[assignment]
                    known.add(jid)
                    job_status[jid] = "active"
                    refresh_ready(self.jobs[jid], t)
                elif kind == WAKEUP:
                    wakeups.discard(t)
                else:
                    ev: ExternalEvent = payload  # type: ignore[assignment]
                    if ev.kind == "cancel" and ev.job_id in self.jobs:
                        job = self.jobs[ev.job_id]
                        for s in job.steps:
                            st = state[(job.id, s.id)]
                            if st.status in ("waiting", "ready"):
                                st.status = "closed"
                                st.reason = Reason.CANCELLED
                                decisions.append(
                                    Decision(
                                        t,
                                        job.id,
                                        s.id,
                                        st.attempts + 1,
                                        policy.label,
                                        Action.CLOSE,
                                        Reason.CANCELLED,
                                        detail="job cancelled; future steps closed",
                                    )
                                )
                            elif st.status == "running":
                                st.status = "cancelled_running"  # provider semantics: it still consumes
                        job_status[job.id] = "cancelled"
                    elif ev.kind == "client_down" and ev.client:
                        client_down.add(ev.client)
                    elif ev.kind == "client_up" and ev.client:
                        client_down.discard(ev.client)
                    elif ev.kind == "pause" and ev.scope and ev.until is not None:
                        cap.pauses[ev.scope] = max(cap.pauses.get(ev.scope, 0), ev.until)
                        push(ev.until, PAUSE_END, ev.scope)
                    elif ev.kind == "consume" and ev.usage:
                        # usage by applications Rubato does not schedule (shared quota)
                        aid = f"external:{t}:{counter}"
                        for spec in self.resources:
                            if spec.window != "concurrency" and spec.applies(
                                ev.client or "", ev.model, ev.provider
                            ):
                                amt = int(tokens_total(ev.usage).get(spec.unit, 0))
                                if amt:
                                    ledger.book(spec.id, t, amt, aid, False, ev.client or "external")
                        ledger.settle(aid, None)

            # --- decision round ------------------------------------------
            ready: list[ReadyItem] = []
            demand: list[Demand] = []
            known = {x for x in known if job_status.get(x) == "active"}  # finished jobs leave the round
            for jid in sorted(known, key=self.seq.__getitem__):
                job = self.jobs[jid]
                rem_need, rem_dur, pending = remaining(job, t)
                if pending:
                    waits = [
                        state[(jid, s.id)].ready_since
                        for s in job.steps
                        if state[(jid, s.id)].status == "ready"
                    ]
                    demand.append(
                        Demand(
                            jid,
                            job.urgent,
                            job.deadline,
                            rem_need,
                            min(waits) if waits else None,
                            rem_dur,
                            job.client,
                            pending,
                        )
                    )
                for s in job.steps:
                    st = state[(jid, s.id)]
                    if st.status != "ready":
                        continue
                    if job.client in client_down:
                        key = (jid, s.id)
                        sig = (Reason.CLIENT_UNAVAILABLE,)
                        if self.log_all or last_logged.get(key) != sig:
                            last_logged[key] = sig
                            decisions.append(
                                Decision(
                                    t,
                                    jid,
                                    s.id,
                                    st.attempts + 1,
                                    policy.label,
                                    Action.DEFER,
                                    Reason.CLIENT_UNAVAILABLE,
                                    detail=f"client {job.client} has no executor available",
                                )
                            )
                        continue
                    ready.append(
                        ReadyItem(
                            job_id=jid,
                            client=job.client,
                            cls=job.cls,
                            urgent=job.urgent,
                            priority=job.priority,
                            arrival=job.arrival,
                            deadline=job.deadline,
                            step_id=s.id,
                            attempt=st.attempts + 1,
                            ready_since=st.ready_since if st.ready_since is not None else t,
                            need=self._admission(s, t),
                            remaining_need=rem_need,
                            remaining_duration=rem_dur,
                            model=s.model,
                            seq=self.seq[jid],
                            provider=s.provider,
                        )
                    )
            max_queue = max(max_queue, len(ready))
            if not ready or (self.horizon is not None and t >= self.horizon):
                track_risk(t)
                continue  # at the horizon, finishing events count but nothing new starts
            t0 = time.perf_counter()
            round_decisions = decidir(t, ready, cap, policy, demand)
            latencies.append((time.perf_counter() - t0) * 1e6)
            ready_by_step = {(item.job_id, item.step_id): item for item in ready}
            next_times: list[int] = []
            for d in round_decisions:
                key = (d.job_id, d.step_id)
                st = state[key]
                job = self.jobs[d.job_id]
                step = job.step(d.step_id)
                if d.action == Action.AUTHORIZE:
                    if turn := (d.value or {}).get("turn"):
                        cap.turn = (turn["client"], turn["position"])
                    st.attempts += 1
                    st.status = "running"
                    st.reason, st.next_t = None, None
                    st.started.append(t)
                    out, from_trace = step.outcome(st.attempts)
                    aid = f"{job.id}/{step.id}/{st.attempts}"
                    admitted = self._admission(step, t)
                    for spec in cap.applicable(ready_by_step[key]):
                        ledger.book(
                            spec.id,
                            t,
                            spec.need_of(admitted),
                            aid,
                            urgent_for(t, ready_by_step[key], policy),
                            job.client,
                        )
                    rec = AttemptRecord(
                        aid,
                        job.id,
                        step.id,
                        st.attempts,
                        t,
                        t + out.duration,
                        "running",
                        {},
                        admitted,
                        from_trace,
                        self.tariff.currency if self.tariff else None,
                        "pending" if self.tariff or "cost_micro" in admitted else None,
                    )
                    attempts.append(rec)
                    push(t + out.duration, FINISH, rec)
                    decisions.append(d)
                    last_logged.pop(key, None)
                elif d.action == Action.CLOSE:
                    st.status = "closed"
                    st.reason = d.reason
                    st.detail = d.detail
                    decisions.append(d)
                    refresh_ready(job, t)
                    close_job_if_finished(job, t)
                    if job_status.get(job.id) == "failed":
                        job_status[job.id] = "closed"
                else:
                    st.reason, st.detail, st.next_t = d.reason, d.detail, d.next_t
                    sig = (d.reason, d.resource, d.next_t)
                    if self.log_all or last_logged.get(key) != sig:
                        last_logged[key] = sig
                        decisions.append(d)
                    if d.next_t is not None and d.next_t > t:
                        next_times.append(d.next_t)
            if next_times:
                nt = min(next_times)
                if nt not in wakeups and (self.horizon is None or nt < self.horizon):
                    wakeups.add(nt)
                    push(nt, WAKEUP, None)
            track_risk(t)

        results: list[JobResult] = []
        for jid, job in self.jobs.items():
            sts = [state[(jid, s.id)] for s in job.steps]
            starts = [x for s in sts for x in s.started]
            status = job_status.get(jid, "pending")
            if status == "active":
                status = "pending"
            fin = max((s.finished_at for s in sts if s.finished_at is not None), default=None)
            reason = next((str(s.reason) for s in sts if s.status not in ("done",) and s.reason), None)
            results.append(
                JobResult(
                    job, status, fin if status == "done" else None, min(starts) if starts else None, reason
                )
            )
        return SimResult(
            policy=policy.label,
            policy_config=policy.config(),
            jobs=results,
            decisions=decisions,
            attempts=attempts,
            resources=cap.specs,
            consumed={rid: ledger.consumed(rid) for rid in ledger.specs},
            end_t=self.horizon if self.horizon is not None else t,
            decision_latency_us=latencies,
            max_queue=max_queue,
            risk=risk,
            usage_log=sorted(
                (e.resource, e.t, e.amount, e.urgent, e.attempt_id)
                for es in ledger.entries.values()
                for e in es
                if e.state in ("confirmed", "unknown") and ledger.specs[e.resource].window != "concurrency"
            ),
        )


def simulate(jobs, resources, policy, events=(), horizon=None, tariff=None) -> SimResult:
    return Simulator(jobs, resources, policy, events, horizon, tariff=tariff).run()


__all__ = ["ExternalEvent", "SimResult", "Simulator", "simulate", "Outcome"]
