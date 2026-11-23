"""Shadow-phase report from an adapter log."""

import json

from rubato.cli import main
from rubato.shadow import shadow_report

DAY = 86400


def write(tmp_path, rows):
    p = tmp_path / "rubato-shadow.jsonl"
    p.write_text("\n".join(json.dumps(r) for r in rows) + "\nnot json\n", encoding="utf-8")
    return p


def row(t, task, action, reason, ms):
    return {
        "t": t,
        "job": f"j{t}",
        "task": task,
        "local": "authorize",
        "rubato": None if action is None else {"action": action, "reason": reason},
        "latency_ms": ms,
    }


def test_counts_agreement_divergence_latency_and_readiness(tmp_path, capsys):
    t0 = 1_795_651_200
    rows = [
        row(t0, "match", "authorize", "fifo_order", 10),
        row(t0 + 5, "match", "defer", "deadline_reserve", 30),
        row(t0 + DAY, "cv", "authorize", "urgent_first", 12),
        row(t0 + 2 * DAY, "match", "defer", "rate_limited", 20),
    ]
    r = shadow_report(write(tmp_path, rows))
    assert (r["calls"], r["agree"], r["diverge"], r["unreadable_lines"]) == (4, 2, 2, 1)
    assert r["divergence_reasons"] == {"deadline_reserve": 1, "rate_limited": 1}
    assert r["added_latency_ms"]["max"] == 30 and r["ready_for_enforce"] is True
    assert main(["shadow-report", str(tmp_path / "rubato-shadow.jsonl")]) == 0
    assert "would have deferred/closed: deadline_reserve × 1" in capsys.readouterr().out


def test_not_ready_when_rubato_was_unreachable_or_too_few_days(tmp_path):
    r = shadow_report(write(tmp_path, [row(1_795_651_200, "match", None, None, 5000)]))
    assert r["rubato_unavailable"] == 1 and r["ready_for_enforce"] is False and r["agreement_ratio"] is None


def test_status_explains_an_unreachable_service(capsys):
    assert main(["status", "--api", "http://127.0.0.1:9"]) == 2
    assert "not reachable" in capsys.readouterr().out
