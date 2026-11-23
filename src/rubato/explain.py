"""Human explanations: one decision, one job, and the cost of changing a rule.

The cost of a rule change is never inferred from the decision log of one run: the
scenario is re-simulated under the other policy and the outcomes are compared job by job.
"""

from __future__ import annotations

from .metrics import summarize
from .model import Action
from .sim import SimResult


def fmt_t(t: int | None, unit: str = "s", origin: int = 0) -> str:
    if t is None:
        return "on the next event"
    if unit == "turn":
        return f"t={t}"
    from datetime import UTC, datetime

    return datetime.fromtimestamp(t, UTC).strftime("%H:%M:%S UTC")


def explain_job(res: SimResult, job_id: str, unit: str = "s") -> str:
    r = res.by_id()[job_id]
    j = r.job
    lines = [
        f"{j.id} ({j.client}/{j.cls}{', urgent' if j.urgent else ''}) arrived {fmt_t(j.arrival, unit)}"
        f" [{j.arrival_provenance}]"
        + (f", deadline {fmt_t(j.deadline, unit)}" if j.deadline is not None else ""),
    ]
    for d in res.decisions:
        if d.job_id != job_id:
            continue
        verb = {"authorize": "AUTHORIZED", "defer": "DEFERRED", "close": "CLOSED"}[str(d.action)]
        msg = f"  {fmt_t(d.t, unit)} {d.step_id}#{d.attempt} {verb} [{d.reason}]"
        if d.detail:
            msg += f" {d.detail}"
        if d.action == Action.DEFER:
            msg += f"; reconsider {fmt_t(d.next_t, unit)}"
        lines.append(msg)
    state = r.status + (" on time" if r.on_time else (" late" if r.status == "done" else ""))
    lines.append(
        f"  result: {state}" + (f" at {fmt_t(r.finished_at, unit)}" if r.finished_at is not None else "")
    )
    return "\n".join(lines)


def what_if(base: SimResult, alt: SimResult) -> dict:
    """Job-by-job difference between two runs of the same scenario."""
    a, b = base.by_id(), alt.by_id()
    gains, losses, delays = [], [], []
    for jid, ra in a.items():
        rb = b[jid]
        sa = (ra.on_time, ra.status == "done")
        sb = (rb.on_time, rb.status == "done")
        if sb > sa:
            gains.append(jid)
        elif sb < sa:
            losses.append(jid)
        elif ra.finished_at is not None and rb.finished_at is not None and rb.finished_at != ra.finished_at:
            delays.append((jid, rb.finished_at - ra.finished_at))
    ma, mb = summarize(base), summarize(alt)

    def delta(path):
        x, y = ma, mb
        for p in path:
            x, y = x[p], y[p]
        return {"base": x, "alt": y}

    return {
        "base": base.policy,
        "alt": alt.policy,
        "gain": gains,
        "lose": losses,
        "finish_shift": delays,
        "urgent_on_time": delta(["urgent", "done_on_time"]),
        "background_on_time": delta(["background", "done_on_time"]),
        "background_done": delta(["background", "done"]),
        "background_wait_p95": delta(["background", "wait_p95"]),
    }


def winners(summaries: dict[str, dict]) -> dict[str, list[str]]:
    """Who is best on each objective. Ties list every policy; nothing is averaged."""
    objectives = {
        "urgent_on_time": lambda m: m["urgent"]["done_on_time"],
        "background_on_time": lambda m: m["background"]["done_on_time"],
        "background_done": lambda m: m["background"]["done"],
        "background_wait_p95 (lower)": lambda m: -(m["background"]["wait_p95"] or 0),
    }
    out = {}
    for name, f in objectives.items():
        vals = {p: f(m) for p, m in summaries.items()}
        best = max(vals.values())
        out[name] = sorted(p for p, v in vals.items() if v == best)
    return out
