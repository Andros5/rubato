"""Run a scenario under several policies and persist every result (versioned, diffable)."""

from __future__ import annotations

import hashlib
import json
import platform
import re
import subprocess
import tempfile
from pathlib import Path

from . import __version__
from .metrics import summarize
from .scenario import apply, build_policies, day_origin, load_policies, load_scenarios
from .sim import Simulator
from .tariffs import Tariff, load_tariff
from .trace import Trace, dump, load

ENGINE_FILES = (
    "core.py",
    "model.py",
    "resources.py",
    "policies.py",
    "sim.py",
    "scenario.py",
    "metrics.py",
    "trace.py",
    "tariffs.py",
    "risk.py",
    "explore.py",
)


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
    trace_path,
    scenario: str,
    scenarios_path,
    policies_path,
    names: list[str],
    out,
    grid=True,
    tariff_path=None,
) -> dict:
    tr = load(trace_path)
    scen = load_scenarios(scenarios_path)
    if scenario not in scen:
        raise ValueError(f"scenario {scenario!r} not found (have {sorted(scen)})")
    prep = apply(tr, scenario, scen[scenario])
    pol_cfg = load_policies(policies_path)
    tariff = load_tariff(tariff_path) if tariff_path else None
    policies = build_policies(pol_cfg, names, grid=grid)
    out = Path(out)
    out.mkdir(parents=True, exist_ok=True)
    manifest = {
        "rubato": __version__,
        "engine_fingerprint": engine_fingerprint(),
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
        "origin": day_origin(tr)
        if tr.header.get("time_unit", "s") == "s"
        else min(j.arrival for j in tr.jobs),
        "resources": [vars(r) for r in prep.resources],
        "jobs": len(prep.jobs),
    }
    dump(tr, out / "inputs.trace.jsonl")
    manifest["schema"] = "rubato.run/v2"
    manifest["inputs"] = {
        "trace": hashlib.sha256((out / "inputs.trace.jsonl").read_bytes()).hexdigest(),
        "scenario": digest(prep.config),
        "tariff": digest(tariff.config()) if tariff else None,
        "policies": digest({label: pol.config() for label, pol in policies.items()}),
    }
    manifest["tariff"] = tariff.config() if tariff else None
    for label, pol in policies.items():
        res = Simulator(prep.jobs, prep.resources, pol, prep.events, prep.horizon, tariff=tariff).run()
        write_result(out, label, res, manifest)
    json_file(out / "manifest.json", manifest)
    return manifest


def digest(value) -> str:
    blob = json.dumps(value, sort_keys=True, ensure_ascii=False, separators=(",", ":"))
    return hashlib.sha256(blob.encode("utf-8")).hexdigest()


def engine_fingerprint() -> str:
    directory = Path(__file__).parent
    return digest([(name, (directory / name).read_text(encoding="utf-8")) for name in ENGINE_FILES])


def json_file(path: Path, value) -> None:
    with tempfile.NamedTemporaryFile(mode="w", encoding="utf-8", dir=path.parent, delete=False) as file:
        temp = Path(file.name)
        try:
            json.dump(value, file, indent=2, sort_keys=True)
        except BaseException:
            file.close()
            temp.unlink(missing_ok=True)
            raise
    try:
        temp.replace(path)
    finally:
        temp.unlink(missing_ok=True)


def run_folder(out: Path, label: str) -> Path:
    if not isinstance(label, str) or not re.fullmatch(r"[a-zA-Z0-9][a-zA-Z0-9_-]{0,95}", label):
        raise ValueError(f"invalid run label {label!r}")
    return out / label


def write_result(out: Path, label, res, manifest) -> dict:
    d = run_folder(out, label)
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
    _jsonl(
        d / "attempts.jsonl", ({**vars(a), "end": a.end if a.settled else res.end_t} for a in res.attempts)
    )
    _jsonl(d / "risk.jsonl", res.risk)
    _jsonl(
        d / "usage.jsonl",
        ({"resource": u[0], "t": u[1], "amount": u[2], "urgent": u[3]} for u in res.usage_log),
    )
    manifest["runs"][label] = {"policy": res.policy, "config": res.policy_config}
    return summary


def input_trace(path: Path, manifest: dict) -> Trace:
    filename = path / "inputs.trace.jsonl"
    if not filename.exists():
        filename = Path(manifest["trace"]["path"])
    tr = load(filename)
    if tr.fingerprint() != manifest["trace"]["fingerprint"]:
        raise ValueError("trace content does not match the saved run")
    return tr


def run_tariff(manifest: dict) -> Tariff | None:
    return Tariff.from_config(manifest["tariff"]) if manifest.get("tariff") else None


def read_run(path) -> dict:
    p = Path(path)
    manifest = json.loads((p / "manifest.json").read_text(encoding="utf-8"))
    runs = {}
    for label in manifest.get("order", manifest["runs"]):
        d = run_folder(p, label)
        if not manifest.get("schema") and not (d / "summary.json").is_file():
            # Early distributable examples kept two policies and the full grid's manifest.
            continue

        def rows(name, d=d):
            file = d / name
            if name in {"usage.jsonl", "attempts.jsonl", "risk.jsonl"} and not file.exists():
                return []
            return [json.loads(x) for x in file.read_text(encoding="utf-8").splitlines() if x.strip()]

        runs[label] = {
            "summary": json.loads((d / "summary.json").read_text(encoding="utf-8")),
            "decisions": rows("decisions.jsonl"),
            "jobs": rows("jobs.jsonl"),
            "usage": rows("usage.jsonl"),
            "attempts": rows("attempts.jsonl"),
            "risk": rows("risk.jsonl"),
        }
    if not runs:
        raise ValueError(f"{p}: no saved policy results")
    if not manifest.get("schema"):
        manifest["order"] = list(runs)
    return {"manifest": manifest, "runs": runs}
