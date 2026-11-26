"""Shadow-phase report: how often Rubato would have decided differently, and at what cost.

Reads the JSONL a client adapter writes in ``shadow`` mode (the radar writes
``data/integrations/rubato-shadow.jsonl``): one line per provider call with the local
decision (always ``authorize``: the application ran the call), Rubato's would-be
decision and the latency Rubato added. This is the evidence PLAN F6 asks for before
moving a client to ``enforce``.
"""

from __future__ import annotations

import json
from collections import Counter
from datetime import UTC, datetime
from pathlib import Path

TRIAL_V1 = {
    "version": "rubato.shadow-trial/v1",
    "min_days": 3,
    "min_calls": 30,
    "min_coverage": 0.95,
    "max_p95_ms": 1000.0,
}


def trial_verdict(*, calls, days, responses, latency_p95, template=None):
    limits = {**TRIAL_V1, **(template or {})}
    coverage = responses / calls if calls else None
    reasons = []
    if calls < limits["min_calls"]:
        reasons.append("insufficient_calls")
    if days < limits["min_days"]:
        reasons.append("insufficient_days")
    if coverage is None or latency_p95 is None:
        reasons.append("missing_evidence")
    if coverage is not None and coverage < limits["min_coverage"]:
        reasons.append("insufficient_coverage")
    if latency_p95 is not None and latency_p95 >= limits["max_p95_ms"]:
        reasons.append("latency_exceeds_limit")
    return {"ready": not reasons, "reasons": reasons, "coverage": coverage, "template": limits}


def _pct(vals: list[float], q: float) -> float | None:
    if not vals:
        return None
    s = sorted(vals)
    return s[min(len(s) - 1, int(round(q * (len(s) - 1))))]


def shadow_report(path: str | Path, template=None) -> dict:
    rows, bad = [], 0
    for ln in Path(path).read_text(encoding="utf-8").splitlines():
        if not ln.strip():
            continue
        try:
            row = json.loads(ln)
            if isinstance(row, dict):
                rows.append(row)
            else:
                bad += 1
        except json.JSONDecodeError:
            bad += 1
    calls = len(rows)
    answered = [r for r in rows if isinstance(r.get("rubato"), dict)]
    agree = [r for r in answered if r["rubato"].get("action") == r.get("local", "authorize")]
    diverge = [r for r in answered if r not in agree]
    lat = [float(r["latency_ms"]) for r in rows if isinstance(r.get("latency_ms"), (int, float))]
    days = Counter(datetime.fromtimestamp(r["t"], UTC).date().isoformat() for r in rows if "t" in r)
    by_task: dict[str, Counter] = {}
    for r in answered:
        c = by_task.setdefault(r.get("task", "?"), Counter())
        c["calls"] += 1
        c["agree" if r in agree else "diverge"] += 1
    assessments = Counter(
        r.get("divergence_effect")
        if r.get("divergence_effect") in {"expected", "harmful"}
        else "unclassified"
        for r in diverge
    )
    return {
        "file": str(path),
        "calls": calls,
        "unreadable_lines": bad,
        "days": dict(sorted(days.items())),
        "rubato_unavailable": calls - len(answered),
        "agree": len(agree),
        "diverge": len(diverge),
        "agreement_ratio": round(len(agree) / len(answered), 3) if answered else None,
        "divergence_reasons": dict(Counter(r["rubato"].get("reason") for r in diverge).most_common()),
        "divergence_assessment": dict(assessments),
        "by_task": {k: dict(v) for k, v in sorted(by_task.items())},
        "added_latency_ms": {"p50": _pct(lat, 0.5), "p95": _pct(lat, 0.95), "max": max(lat) if lat else None},
        "ready_for_enforce": bool(answered) and len(days) >= 3 and calls - len(answered) == 0,
        "trial_verdict": trial_verdict(
            calls=calls + bad,
            days=len(days),
            responses=len(answered),
            latency_p95=_pct(lat, 0.95),
            template=template,
        ),
        "note": "a divergence is a call Rubato would have deferred or closed; its effect is only a simulation",
    }


def to_text(r: dict) -> str:
    lines = [
        f"Shadow report · {r['file']}",
        f"  calls {r['calls']} over {len(r['days'])} day(s) {list(r['days'])}",
        f"  Rubato answered {r['calls'] - r['rubato_unavailable']}, unavailable {r['rubato_unavailable']}",
        f"  agree {r['agree']}, diverge {r['diverge']} (agreement {r['agreement_ratio']})",
    ]
    for reason, n in r["divergence_reasons"].items():
        lines.append(f"    would have deferred/closed: {reason} × {n}")
    for task, c in r["by_task"].items():
        lines.append(f"  {task}: {c}")
    lat = r["added_latency_ms"]
    lines.append(f"  added latency ms p50 {lat['p50']} p95 {lat['p95']} max {lat['max']}")
    verdict = r["trial_verdict"]
    lines.append(
        f"  trial {verdict['template']['version']}: "
        + ("ready" if verdict["ready"] else ", ".join(verdict["reasons"]))
    )
    return "\n".join(lines)
