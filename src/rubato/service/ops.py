"""Operational metrics (Prometheus text format). No per-job labels."""

from __future__ import annotations

import threading
from collections import defaultdict

_lock = threading.Lock()
_requests: dict[tuple[str, str, int], int] = defaultdict(int)
_latency_sum: dict[str, float] = defaultdict(float)
_latency_count: dict[str, int] = defaultdict(int)


def _route(path: str) -> str:
    parts = path.strip("/").split("/")
    if len(parts) >= 3 and parts[1] in ("jobs", "attempts"):
        parts[2] = "{id}"
    return "/" + "/".join(parts)


def _esc(v) -> str:
    """Escape a Prometheus label value: backslash, double quote and newline."""
    return str(v).replace("\\", "\\\\").replace('"', '\\"').replace("\n", "\\n")


def observe_request(path: str, method: str, status: int, seconds: float) -> None:
    r = _route(path)
    with _lock:
        _requests[(r, method, status)] += 1
        _latency_sum[r] += seconds
        _latency_count[r] += 1


def render_metrics(conn) -> str:
    lines: list[str] = []

    def g(name, help_, rows):
        lines.append(f"# HELP {name} {help_}")
        lines.append(f"# TYPE {name} gauge")
        for labels, v in rows:
            lab = ",".join(f'{k}="{_esc(val)}"' for k, val in labels.items())
            lines.append(f"{name}{{{lab}}} {v}" if lab else f"{name} {v}")

    from .store import now

    t = now(conn)
    q = conn.execute(
        "SELECT j.client_id, s.state, count(*) AS n, min(s.ready_since) AS oldest FROM steps s JOIN jobs j ON j.id=s.job_id "
        "WHERE j.state='active' GROUP BY 1, 2"
    ).fetchall()
    g(
        "rubato_steps",
        "Steps of active jobs by client and state",
        [({"client": r["client_id"], "state": r["state"]}, r["n"]) for r in q],
    )
    g(
        "rubato_queue_oldest_ready_age_seconds",
        "Age of the oldest ready step",
        [
            ({"client": r["client_id"]}, t - int(r["oldest"]))
            for r in q
            if r["state"] == "ready" and r["oldest"]
        ],
    )
    a = conn.execute("SELECT state, count(*) AS n FROM attempts GROUP BY 1").fetchall()
    g(
        "rubato_attempts",
        "Attempts by state (unknown = pending reconciliation)",
        [({"state": r["state"]}, r["n"]) for r in a],
    )
    w = conn.execute(
        "SELECT avg(started_at - granted_at) AS w FROM attempts WHERE started_at IS NOT NULL"
    ).fetchone()
    g(
        "rubato_grant_to_start_seconds_avg",
        "Average wait from permit to start",
        [({}, round(float(w["w"] or 0), 3))],
    )
    d = conn.execute("SELECT action, reason, count(*) AS n FROM decisions GROUP BY 1, 2").fetchall()
    g(
        "rubato_decisions",
        "Decisions by action and reason",
        [({"action": r["action"], "reason": r["reason"]}, r["n"]) for r in d],
    )
    led = conn.execute(
        "SELECT resource_id, state, coalesce(sum(amount),0) AS n FROM ledger WHERE state <> 'released' GROUP BY 1, 2"
    ).fetchall()
    g(
        "rubato_ledger_amount",
        "Booked amount by resource and state (reserved/confirmed/unknown)",
        [({"resource": r["resource_id"], "state": r["state"]}, r["n"]) for r in led],
    )
    with _lock:
        lines.append("# HELP rubato_http_requests Requests by route, method and status")
        lines.append("# TYPE rubato_http_requests counter")
        for (r, m, s), n in sorted(_requests.items()):
            lines.append(f'rubato_http_requests{{route="{r}",method="{m}",status="{s}"}} {n}')
        lines.append("# HELP rubato_http_latency_seconds_sum Sum of request latency by route")
        lines.append("# TYPE rubato_http_latency_seconds_sum counter")
        for r, v in sorted(_latency_sum.items()):
            lines.append(f'rubato_http_latency_seconds_sum{{route="{r}"}} {v:.6f}')
            lines.append(f'rubato_http_latency_seconds_count{{route="{r}"}} {_latency_count[r]}')
    return "\n".join(lines) + "\n"
