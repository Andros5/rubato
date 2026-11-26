"""Replay a saved scenario with one effective change per alternative."""

from __future__ import annotations

import json
import signal
from pathlib import Path

from . import __version__
from .policies import make_policy
from .report import build_report
from .runs import (
    digest,
    engine_fingerprint,
    input_trace,
    json_file,
    read_run,
    run_folder,
    run_tariff,
    write_result,
)
from .scenario import apply, day_origin
from .sim import RuleChange, Simulator
from .trace import dump

SCHEMA = "rubato.explore/v1"
GOALS = {
    "min_urgent_on_time": ("urgent", "done_on_time", 1),
    "min_background_done": ("background", "done", 1),
    "max_background_wait_p95_s": ("background", "wait_p95", -1),
}


def _integer(value) -> bool:
    return type(value) is int and 0 <= value <= 1_000_000_000


def _goals(goals: dict):
    for name, target in goals.items():
        if name == "clients":
            if not isinstance(target, dict):
                raise ValueError("goals.clients must map client IDs to objectives")
            for client, client_goals in target.items():
                if not isinstance(client_goals, dict):
                    raise ValueError(f"invalid objectives for client {client}")
                for _, field, limit in _goals(client_goals):
                    yield client, field, limit
            continue
        if name not in GOALS or not _integer(target):
            raise ValueError(f"invalid objective {name!r}")
        yield None, name, target


def _value(summary, client, goal):
    group, key, _ = GOALS[goal]
    if client is not None:
        summary = summary.get("by_client", {}).get(client, {})
    block = summary.get(group, {})
    if group == "urgent" and not block.get("received"):
        return None
    return block.get(key)


def _dominates(a, b, objectives):
    diffs = []
    for client, goal, _ in objectives:
        x, y = _value(a, client, goal), _value(b, client, goal)
        if x is not None and y is not None:
            diffs.append((x - y) * GOALS[goal][2])
    return all(d >= 0 for d in diffs) and any(d > 0 for d in diffs)


def _policy(config):
    args = dict(config)
    return make_policy(args.pop("name"), **args)


def _affected(before, after):
    baseline = {j["job"]: j for j in before}
    changes = []
    for j in after:
        old = baseline.get(j["job"])
        keys = ("on_time", "status", "first_start", "finished_at")
        if old is None or any(old[k] != j[k] for k in keys):
            score = (j["on_time"], j["status"] == "done")
            old_score = (old["on_time"], old["status"] == "done") if old else None
            effect = (
                "added"
                if old is None
                else "gain"
                if score > old_score
                else "loss"
                if score < old_score
                else "shift"
            )
            changes.append(
                {
                    "job": j["job"],
                    "client": j["client"],
                    "effect": effect,
                    "before": {k: old[k] for k in keys} if old else None,
                    "after": {k: j[k] for k in keys},
                }
            )
    return changes


def explore(base: Path, spec: dict, out: Path, cancelled=lambda: False) -> dict:
    if base.resolve() == out.resolve():
        raise ValueError("the exploration needs a separate output directory")
    saved = json.loads((base / "manifest.json").read_text(encoding="utf-8"))
    if saved.get("exploration"):
        raise ValueError("select the starting scenario run for a new exploration")
    if spec.get("schema") != SCHEMA:
        raise ValueError(f"expected schema {SCHEMA}")
    ref, candidate = spec["reference"], spec["candidate"]
    if ref not in saved["runs"] or candidate not in saved["runs"]:
        raise ValueError("reference and candidate must exist in the saved run")
    offset = spec["at_offset_s"]
    if not _integer(offset):
        raise ValueError("at_offset_s must be a non-negative integer")
    vary = spec["vary"]
    allowed = {"reserve", "capacity", "release_offset"}
    kind, values = vary["kind"], vary["values"]
    if (
        kind not in allowed
        or not isinstance(values, list)
        or not 1 <= len(values) <= 64
        or not all(map(_integer, values))
    ):
        raise ValueError("specify reserve, capacity or release_offset and 1–64 non-negative integer values")
    objectives = list(_goals(spec.get("goals", {})))
    ranking = objectives or [(None, g, 0) for g in GOALS]
    tr = input_trace(base, saved)
    scenario = dict(saved["scenario"])
    name = scenario.pop("name")
    prep = apply(tr, name, scenario)
    origin = day_origin(tr) if tr.header.get("time_unit", "s") == "s" else min(j.arrival for j in tr.jobs)
    effective_at = origin + offset
    if prep.horizon is not None and effective_at >= prep.horizon:
        raise ValueError("change is outside the simulation horizon")
    baseline = _policy(saved["runs"][ref]["config"])
    candidate_config = saved["runs"][candidate]["config"]
    tariff = run_tariff(saved)
    rid = vary.get("resource")
    original = next((r.capacity for r in prep.resources if r.id == rid), None)
    if kind in ("reserve", "capacity") and original is None:
        raise ValueError(f"unknown resource {rid!r}")
    if kind != "capacity" and candidate_config.get("name") not in ("reserva", "fair"):
        raise ValueError("reserve changes require a reserve policy")

    engine = engine_fingerprint()
    key = digest(
        [
            __version__,
            engine,
            tr.fingerprint(),
            scenario,
            saved.get("tariff"),
            baseline.config(),
            candidate_config,
            spec,
        ]
    )
    out.mkdir(parents=True, exist_ok=True)
    state_file = out / "exploration.json"
    cached = json.loads(state_file.read_text(encoding="utf-8")) if state_file.exists() else {}
    if cached.get("key") != key:
        cached = {}
    dump(tr, out / "inputs.trace.jsonl")
    manifest = {
        **saved,
        "schema": "rubato.run/v2",
        "rubato": __version__,
        "engine_fingerprint": engine,
        "runs": {},
        "order": [],
        "exploration": {
            "schema": SCHEMA,
            "key": key,
            "spec": spec,
            "effective_at": effective_at,
            "reference_config": baseline.config(),
            "candidate_config": candidate_config,
        },
    }
    finished = {}
    summaries = {}
    saved_runs = cached.get("runs", {})

    def run(label, initial, change):
        if label in saved_runs:
            manifest["runs"][label] = saved_runs[label]
            result = json.loads((run_folder(out, label) / "summary.json").read_text(encoding="utf-8"))
        else:
            simulated = Simulator(
                prep.jobs, prep.resources, initial, prep.events, prep.horizon, tariff=tariff, change=change
            ).run()
            result = write_result(out, label, simulated, manifest)
        manifest["order"].append(label)
        summaries[label] = result
        json_file(state_file, {"key": key, "runs": manifest["runs"]})

    run(ref, baseline, None)
    for value in sorted(set(values)):
        if cancelled():
            break
        label = f"{candidate}-{kind}-{value}"
        params = dict(candidate_config)
        kwargs = {}
        initial = baseline
        if kind == "capacity":
            kwargs["capacities"] = {rid: original + value}
        else:
            if kind == "reserve":
                params["reserve"] = {**params.get("reserve", {}), rid: value}
            else:
                params["release_offset"] = value
            kwargs["policy"] = _policy(params)
        run(label, initial, RuleChange(effective_at, **kwargs))
        failures = []
        for client, goal, target in objectives:
            actual = _value(summaries[label], client, goal)
            delta = None if actual is None else (actual - target) * GOALS[goal][2]
            if delta is None or delta < 0:
                failures.append(
                    {
                        "client": client,
                        "objective": goal,
                        "target": target,
                        "actual": actual,
                        "shortfall": None if delta is None else -delta,
                    }
                )
        blocks = {}
        for reason, count in summaries[label]["deferrals_by_reason"].items():
            blocks[reason] = count
        finished[label] = {
            "value": value,
            "meets_goals": not failures,
            "failures": failures,
            "blocking_reasons": blocks,
            "change": {"capacity_increase": value} if kind == "capacity" else {},
        }

    result = manifest["exploration"]
    options = list(finished)
    result.update(
        {
            "complete": len(options) == len(set(values)),
            "recommended": next((label for label in options if finished[label]["meets_goals"]), None),
            "non_dominated": [
                label
                for label in options
                if not any(
                    _dominates(summaries[other], summaries[label], ranking)
                    for other in options
                    if other != label
                )
            ],
            "closest": sorted(
                options, key=lambda label: (len(finished[label]["failures"]), finished[label]["value"])
            )[:3],
            "alternatives": finished,
        }
    )
    if (
        prep.horizon is not None
        and 1 <= prep.horizon <= 12
        and not prep.events
        and tariff is None
        and 0 < sum(len(j.steps) for j in prep.jobs) <= 4
        and not any(r.reset_tz for r in prep.resources)
        and all(
            (j.known_at is None or j.known_at == j.arrival)
            and all(s.max_attempts == 1 and s.outcome(1)[0].duration >= 1 for s in j.steps)
            for j in prep.jobs
        )
    ):
        from .optimize import enumerate_best

        optimum = enumerate_best(prep.jobs, prep.resources, prep.horizon)
        result["offline_optimum"] = {
            "score": optimum.score,
            "solver": optimum.solver,
            "status": "optimal",
            "assumptions": "original limits, known outcomes, one attempt per step, no pauses or external events",
        }
    json_file(out / "manifest.json", manifest)
    data = read_run(out)
    for label in options:
        run_data = data["runs"][label]
        finished[label]["affected"] = _affected(data["runs"][ref]["jobs"], run_data["jobs"])
        if kind == "capacity":
            finished[label]["binding_resources"] = sorted(
                {d["resource"] for d in run_data["decisions"] if d.get("resource") and d["action"] == "defer"}
            )
    json_file(out / "manifest.json", manifest)
    data["manifest"] = manifest
    build_report(data, out / "informe.html")
    return result


def run_explore(base: Path, spec: Path, out: Path) -> int:
    stop = False

    def interrupt(*_):
        nonlocal stop
        stop = True

    previous = signal.signal(signal.SIGINT, interrupt)
    try:
        result = explore(base, json.loads(spec.read_text(encoding="utf-8-sig")), out, lambda: stop)
    finally:
        signal.signal(signal.SIGINT, previous)
    print(
        json.dumps({k: result[k] for k in ("complete", "recommended", "non_dominated")}, ensure_ascii=False)
    )
    return 0 if result["complete"] else 130
