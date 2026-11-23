"""Run a scenario under several policies and persist every result (versioned, diffable)."""

from __future__ import annotations

import json
import platform
import subprocess
from pathlib import Path

from . import __version__
from .metrics import summarize
from .scenario import apply, build_policies, load_policies, load_scenarios
from .sim import Simulator
from .trace import load


def _git_commit() -> str | None:
    try:
        return subprocess.run(
            ["git", "rev-parse", "--short", "HEAD"],
            capture_output=True,
            text=True,
            check=True,
            cwd=Path(__file__).resolve().parent,
        ).stdout.strip()
    except Exception:
        return None


def _jsonl(path: Path, rows) -> None:
    with path.open("w", encoding="utf-8", newline="\n") as fh:
        for r in rows:
            fh.write(json.dumps(r, sort_keys=True, ensure_ascii=False) + "\n")


def run_scenario(
    trace_path, scenario: str, scenarios_path, policies_path, names: list[str], out, grid=True
) -> dict:
    tr = load(trace_path)
    scen = load_scenarios(scenarios_path)
    if scenario not in scen:
        raise ValueError(f"scenario {scenario!r} not found (have {sorted(scen)})")
    prep = apply(tr, scenario, scen[scenario])
    pol_cfg = load_policies(policies_path)
    policies = build_policies(pol_cfg, names, grid=grid)
    out = Path(out)
    out.mkdir(parents=True, exist_ok=True)
    manifest = {
        "rubato": __version__,
        "commit": _git_commit(),
        "python": platform.python_version(),
        "trace": {
            "path": str(trace_path),
            "fingerprint": tr.fingerprint(),
            "created_by": tr.header.get("created_by"),
            "seed": tr.header.get("seed"),
            "time_unit": tr.header.get("time_unit", "s"),
        },
        "scenario": {"name": scenario, **prep.config},
        "assumptions": prep.assumptions,
        "policies_file": {"path": str(policies_path), "frozen_at": pol_cfg.get("frozen_at")},
        "runs": {},
        "order": list(policies),
        "horizon": prep.horizon,
        "resources": [vars(r) for r in prep.resources],
        "jobs": len(prep.jobs),
    }
    for label, pol in policies.items():
        res = Simulator(prep.jobs, prep.resources, pol, prep.events, prep.horizon, tariff=None).run()
        d = out / label
        d.mkdir(exist_ok=True)
        summary = summarize(res)
        (d / "summary.json").write_text(json.dumps(summary, indent=2, sort_keys=True), encoding="utf-8")
        _jsonl(d / "decisions.jsonl", (x.to_dict() for x in res.decisions))
        _jsonl(
            d / "jobs.jsonl",
            (
                {
                    "job": r.job.id,
                    "client": r.job.client,
                    "class": r.job.cls,
                    "urgent": r.job.urgent,
                    "arrival": r.job.arrival,
                    "arrival_provenance": r.job.arrival_provenance,
                    "deadline": r.job.deadline,
                    "first_start": r.first_start,
                    "finished_at": r.finished_at,
                    "status": r.status,
                    "on_time": r.on_time,
                    "reason": r.reason,
                }
                for r in res.jobs
            ),
        )
        _jsonl(d / "attempts.jsonl", (vars(a) for a in res.attempts))
        _jsonl(
            d / "usage.jsonl",
            ({"resource": u[0], "t": u[1], "amount": u[2], "urgent": u[3]} for u in res.usage_log),
        )
        manifest["runs"][label] = {"policy": res.policy, "config": res.policy_config}
    (out / "manifest.json").write_text(
        json.dumps(manifest, indent=2, sort_keys=True, default=str), encoding="utf-8"
    )
    return manifest


def read_run(path) -> dict:
    p = Path(path)
    manifest = json.loads((p / "manifest.json").read_text(encoding="utf-8"))
    runs = {}
    for label in manifest.get("order", manifest["runs"]):
        d = p / label

        def rows(name, d=d):
            return [json.loads(x) for x in (d / name).read_text(encoding="utf-8").splitlines() if x.strip()]

        runs[label] = {
            "summary": json.loads((d / "summary.json").read_text(encoding="utf-8")),
            "decisions": rows("decisions.jsonl"),
            "jobs": rows("jobs.jsonl"),
            "usage": rows("usage.jsonl"),
            "attempts": rows("attempts.jsonl"),
        }
    return {"manifest": manifest, "runs": runs}
