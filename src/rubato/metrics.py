"""Metrics reported separately per objective (never a single weighted score)."""

from __future__ import annotations

from collections import Counter, defaultdict

from .model import Action
from .sim import SimResult


def pct(vals: list[float], q: float) -> float | None:
    if not vals:
        return None
    s = sorted(vals)
    k = max(0, min(len(s) - 1, int(round(q * (len(s) - 1)))))
    return s[k]


def summarize(res: SimResult) -> dict:
    urgent = [r for r in res.jobs if r.job.urgent]
    background = [r for r in res.jobs if not r.job.urgent]
    by_prov: dict[str, Counter] = defaultdict(Counter)
    for r in res.jobs:
        by_prov[r.job.arrival_provenance][r.status] += 1

    def block(rs):
        started = [r.first_start - r.job.arrival for r in rs if r.first_start is not None]
        pending = [res.end_t - r.job.arrival for r in rs if r.status == "pending"]
        return {
            "received": len(rs),
            "done": sum(r.status == "done" for r in rs),
            "done_on_time": sum(r.on_time for r in rs),
            "done_late": sum(r.status == "done" and not r.on_time for r in rs),
            "pending": sum(r.status == "pending" for r in rs),
            "failed": sum(r.status in ("failed", "closed") for r in rs),
            "cancelled": sum(r.status == "cancelled" for r in rs),
            "wait_p50": pct(started, 0.5),
            "wait_p95": pct(started, 0.95),
            "oldest_pending_age": max(pending) if pending else None,
        }

    per_class = defaultdict(list)
    for r in background:
        per_class[r.job.cls].append(r)
    deferrals = Counter(str(d.reason) for d in res.decisions if d.action == Action.DEFER)
    closes = Counter(str(d.reason) for d in res.decisions if d.action == Action.CLOSE)
    lat = res.decision_latency_us
    return {
        "policy": res.policy,
        "urgent": {
            **block(urgent),
            "on_time_ratio": f"{sum(r.on_time for r in urgent)}/{len(urgent)}",
        },
        "background": {**block(background), "by_class": {k: block(v) for k, v in sorted(per_class.items())}},
        "by_arrival_provenance": {k: dict(sorted(v.items())) for k, v in sorted(by_prov.items())},
        "consumption": {
            "consumed": dict(res.consumed),
            "attempts": len(res.attempts),
            "attempt_status": dict(sorted(Counter(a.status for a in res.attempts).items())),
            "observed_usage_attempts": sum(a.from_trace for a in res.attempts),
            "estimated_usage_attempts": sum(not a.from_trace for a in res.attempts),
        },
        "deferrals_by_reason": dict(sorted(deferrals.items())),
        "closes_by_reason": dict(sorted(closes.items())),
        "planner": {
            "decision_rounds": len(lat),
            "latency_us_p50": round(pct(lat, 0.5) or 0, 1),
            "latency_us_p95": round(pct(lat, 0.95) or 0, 1),
            "max_ready_queue": res.max_queue,
        },
    }
