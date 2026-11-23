"""Evaluation by date blocks: design / tuning / test, with per-day absolute numbers.

Protocol (PLAN F4):
1. Policies and the pilot thresholds are frozen in files before evaluating.
2. The reserve size is chosen on the *tuning* block only, with a fixed rule
   (most urgent on time; ties -> lowest background p95 wait; ties -> smallest reserve).
3. The chosen variant and the reference run once on the *test* block.
4. Every number is published per day with its denominator; synthetic, hypothetical
   and observed inputs stay labelled. Nothing is averaged across objectives.
"""

from __future__ import annotations

import json
from dataclasses import replace
from datetime import UTC, date, datetime
from pathlib import Path
from zoneinfo import ZoneInfo

from .metrics import summarize
from .model import Provenance
from .resources import ResourceSpec
from .scenario import BASE_ASSUMPTION, build_policies, load_policies
from .sim import Simulator
from .trace import load

PROFILE_SCHEMA = "rubato.profile/v1"


def load_profile(path) -> dict:
    data = json.loads(Path(path).read_text(encoding="utf-8"))
    if data.get("version") != PROFILE_SCHEMA:
        raise ValueError(f"{path}: expected {PROFILE_SCHEMA}")
    data["specs"] = [ResourceSpec(**{**r, "scope": r.get("scope") or {}}) for r in data["resources"]]
    return data


def _day(t: int, tz: str) -> str:
    return datetime.fromtimestamp(t, UTC).astimezone(ZoneInfo(tz)).date().isoformat()


def _block_jobs(jobs, d0: str, d1: str, tz: str):
    return [j for j in jobs if d0 <= _day(j.arrival, tz) <= d1]


def per_day(res, tz: str) -> dict[str, dict]:
    out: dict[str, dict] = {}
    for r in res.jobs:
        d = out.setdefault(
            _day(r.job.arrival, tz),
            {
                "urgent_received": 0,
                "urgent_on_time": 0,
                "urgent_pending": 0,
                "background_received": 0,
                "background_done": 0,
                "background_pending": 0,
                "waits": [],
            },
        )
        if r.job.urgent:
            d["urgent_received"] += 1
            d["urgent_on_time"] += r.on_time
            d["urgent_pending"] += r.status == "pending"
        else:
            d["background_received"] += 1
            d["background_done"] += r.status == "done"
            d["background_pending"] += r.status == "pending"
            if r.first_start is not None:
                d["waits"].append(r.first_start - r.job.arrival)
    for d in out.values():
        w = sorted(d.pop("waits"))
        d["background_wait_p95_s"] = w[int(round(0.95 * (len(w) - 1)))] if w else None
    return dict(sorted(out.items()))


def evaluate(
    trace_path,
    profile_path,
    policies_path,
    blocks: dict[str, tuple[str, str]],
    deadline_minutes: int,
    tz: str = "America/Los_Angeles",
    pilot: dict | None = None,
) -> dict:
    tr = load(trace_path)
    prof = load_profile(profile_path)
    pol_cfg = load_policies(policies_path)
    hyp = Provenance.HYPOTHETICAL.value
    jobs = [
        replace(j, deadline=j.arrival + deadline_minutes * 60, provenance={**j.provenance, "deadline": hyp})
        if j.urgent
        else j
        for j in tr.jobs
    ]
    result: dict = {
        "trace_fingerprint": tr.fingerprint(),
        "profile": prof["id"],
        "policies_frozen_at": pol_cfg.get("frozen_at"),
        "deadline_minutes_hypothetical": deadline_minutes,
        "day_zone": tz,
        "assumptions": [
            BASE_ASSUMPTION,
            *tr.header.get("assumptions", []),
            *prof.get("assumptions", []),
            f"urgent deadlines are hypothetical: arrival + {deadline_minutes} min",
        ],
        "blocks": {},
    }
    names = list(pol_cfg["policies"])
    for block, (d0, d1) in blocks.items():
        bj = _block_jobs(jobs, d0, d1, tz)
        grid = block == "tuning"
        pols = build_policies(pol_cfg, names, grid=grid)
        if block == "test" and "chosen" in result:
            pols = {k: v for k, v in pols.items() if k == "radar"}
            pols.update(build_policies(pol_cfg, ["reserva"], grid=True))
            pols = {k: v for k, v in pols.items() if k in ("radar", result["chosen"])}
        runs = {}
        for label, pol in pols.items():
            res = Simulator(bj, prof["specs"], pol).run()
            s = summarize(res)
            runs[label] = {
                "policy": res.policy,
                "config": res.policy_config,
                "urgent": {
                    k: s["urgent"][k] for k in ("received", "done_on_time", "done_late", "pending", "failed")
                },
                "background": {
                    k: s["background"][k]
                    for k in ("received", "done", "pending", "failed", "wait_p50", "wait_p95")
                },
                "by_arrival_provenance": s["by_arrival_provenance"],
                "per_day": per_day(res, tz),
                "planner_latency_us_p95": s["planner"]["latency_us_p95"],
            }
        result["blocks"][block] = {"days": [d0, d1], "jobs": len(bj), "runs": runs}
        if block == "tuning":

            def key(lbl, runs=runs):
                r = runs[lbl]
                return (
                    -r["urgent"]["done_on_time"],
                    r["background"]["wait_p95"] or 0,
                    sum(r["config"].get("reserve", {}).values()),
                )

            cands = [k for k in runs if k.startswith("reserva")]
            result["chosen"] = min(cands, key=key)
            result["selection_rule"] = (
                "max urgent on time; then min background p95 wait; then smallest reserve"
            )
    if pilot:
        result["pilot"] = pilot
        test = result["blocks"].get("test", {}).get("runs", {})
        if "radar" in test and result.get("chosen") in test:
            ref, cand = test["radar"], test[result["chosen"]]
            gain = cand["urgent"]["done_on_time"] - ref["urgent"]["done_on_time"]
            extra_wait = (cand["background"]["wait_p95"] or 0) - (ref["background"]["wait_p95"] or 0)
            ok = gain >= pilot["min_urgent_gain"] and extra_wait <= pilot["max_background_p95_extra_s"]
            result["verdict"] = {
                "urgent_gain": gain,
                "background_p95_extra_s": extra_wait,
                "candidate_wins": ok,
                "default_policy": result["chosen"] if ok else "radar",
                "note": "small test block: the verdict is indicative, not a statistical result",
            }
    return result


def to_markdown(r: dict) -> str:
    lines = [
        f"# Evaluación privada · traza {r['trace_fingerprint']} · perfil {r['profile']}",
        "",
        f"Políticas congeladas: {r['policies_frozen_at']}. Plazo urgente hipotético: {r['deadline_minutes_hypothetical']} min.",
        "",
    ]
    for b, blk in r["blocks"].items():
        lines += [
            f"## {b} ({blk['days'][0]} – {blk['days'][1]}, {blk['jobs']} trabajos)",
            "",
            "| política | urgentes a tiempo | fondo terminado | fondo pendiente | espera p95 fondo (s) |",
            "|---|---:|---:|---:|---:|",
        ]
        for lbl, run in blk["runs"].items():
            u, g = run["urgent"], run["background"]
            lines.append(
                f"| {lbl} | {u['done_on_time']}/{u['received']} | {g['done']}/{g['received']} | {g['pending']} | {g['wait_p95']} |"
            )
        lines += ["", "Por día (urgentes a tiempo/recibidos · fondo terminado/recibido · p95 espera s):", ""]
        days = sorted({d for run in blk["runs"].values() for d in run["per_day"]})
        lines.append("| día | " + " | ".join(blk["runs"]) + " |")
        lines.append("|---|" + "---|" * len(blk["runs"]))
        for d in days:
            cells = []
            for run in blk["runs"].values():
                x = run["per_day"].get(d)
                cells.append(
                    "—"
                    if not x
                    else f"{x['urgent_on_time']}/{x['urgent_received']} · {x['background_done']}/{x['background_received']} · {x['background_wait_p95_s']}"
                )
            lines.append(f"| {d} | " + " | ".join(cells) + " |")
        lines.append("")
    if "chosen" in r:
        lines += [f"Variante elegida en ajuste: **{r['chosen']}** ({r['selection_rule']}).", ""]
    if "verdict" in r:
        v = r["verdict"]
        lines += [
            f"Veredicto en prueba: ganancia urgente {v['urgent_gain']}, espera p95 extra {v['background_p95_extra_s']} s → "
            f"política por defecto **{v['default_policy']}**. {v['note']}.",
            "",
        ]
    lines += ["Supuestos:", *[f"- {a}" for a in r["assumptions"]], ""]
    return "\n".join(lines)


def default_blocks() -> dict[str, tuple[str, str]]:
    return {
        "design": ("2026-11-09", "2026-11-15"),
        "tuning": ("2026-11-16", "2026-11-18"),
        "test": ("2026-11-19", "2026-11-21"),
    }


__all__ = ["evaluate", "to_markdown", "default_blocks", "load_profile", "date"]
