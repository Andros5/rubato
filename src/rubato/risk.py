"""Deadline forecasts based on the information the planner has at the time."""

from __future__ import annotations

from dataclasses import dataclass
from statistics import median_high

from .model import critical_path


@dataclass(frozen=True)
class Estimate:
    deps: tuple[str, ...]
    duration: int
    available_at: int | None = None
    blocked_by: str | None = None
    samples: int = 0


def estimate_duration(declared: int, observed: list[int], minimum=20) -> tuple[int, int]:
    return (median_high(observed), len(observed)) if len(observed) >= minimum else (declared, 0)


def forecast(now: int, deadline: int, steps: dict[str, Estimate], margin: int = 0) -> dict:
    work = sum(step.duration for step in steps.values())
    path_time, path = critical_path(
        {sid: step.deps for sid, step in steps.items()},
        {sid: step.duration for sid, step in steps.items()},
    )
    offsets = {}
    unknown = []

    def finish(sid):
        if sid not in steps:
            return now
        if sid in offsets:
            return offsets[sid]
        step = steps[sid]
        earliest = max((finish(d) for d in step.deps), default=now)
        if step.blocked_by and step.available_at is None:
            unknown.append({"step": sid, "reason": step.blocked_by})
        offsets[sid] = (
            max(earliest, step.available_at if step.available_at is not None else now) + step.duration
        )
        return offsets[sid]

    earliest = max((finish(sid) for sid in steps), default=now)
    slack = deadline - earliest
    return {
        "at": now,
        "deadline": deadline,
        "work_s": work,
        "critical_path_s": path_time,
        "critical_path": path,
        "earliest_finish": earliest,
        "expected_finish": None if unknown else earliest,
        "next_reconsider": min(
            (s.available_at for s in steps.values() if s.available_at is not None and s.available_at > now),
            default=None,
        ),
        "margin_s": None if unknown else slack,
        "status": "risk" if slack <= margin else "unknown" if unknown else "on_track",
        "blocking": [
            {"step": sid, "reason": s.blocked_by, "until": s.available_at}
            for sid, s in steps.items()
            if s.blocked_by
        ],
        "basis": "declared durations or prior median by class and model; independent branches may run in parallel",
        "samples_by_step": {sid: s.samples for sid, s in steps.items() if s.samples},
    }


def evaluate_alerts(run: dict, margin: int) -> dict:
    jobs = [j for j in run["jobs"] if j["urgent"] and j["deadline"] is not None]
    by_job = {}
    for risk in run["risk"]:
        by_job.setdefault(risk["job"], []).append(risk)
    counts = {
        "eligible": len(jobs),
        "evaluated": 0,
        "misses": 0,
        "true_alerts": 0,
        "false_alerts": 0,
        "missed_alerts": 0,
        "unevaluable": 0,
    }
    lead = []
    for job in jobs:
        before = [
            r
            for r in by_job.get(job["job"], [])
            if r["at"] < job["deadline"] and (r["expected_finish"] is not None or r["status"] == "risk")
        ]
        if not before:
            counts["unevaluable"] += 1
            continue
        counts["evaluated"] += 1
        missed = not job["on_time"]
        counts["misses"] += missed
        hits = [r for r in before if r["status"] == "risk" or r["margin_s"] <= margin]
        if hits and missed:
            counts["true_alerts"] += 1
            lead.append(job["deadline"] - min(r["at"] for r in hits))
        elif hits:
            counts["false_alerts"] += 1
        elif missed:
            counts["missed_alerts"] += 1
    return {**counts, "lead_s": sorted(lead)}


def calibrate(design: dict, test: dict, candidates=(0, 30, 60, 120, 300)) -> dict:
    by_margin = {m: evaluate_alerts(design, m) for m in candidates}
    margin = max(
        candidates,
        key=lambda m: (
            by_margin[m]["true_alerts"] - by_margin[m]["false_alerts"],
            by_margin[m]["true_alerts"],
            -m,
        ),
    )
    return {"margin_s": margin, "design": by_margin, "test": evaluate_alerts(test, margin)}
