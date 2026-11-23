"""Scenarios: versioned overlays applied to a trace before simulating.

Every overlay keeps provenance honest: injected jobs are ``hypothetical``, cloned
observed jobs become ``hypothetical``, and each scenario carries its assumptions.
Reordering historical calls does not show what the provider would have answered
at another instant; every scenario states that.
"""

from __future__ import annotations

import json
import math
from dataclasses import dataclass, replace
from pathlib import Path

from .model import Job, Outcome, Provenance, Step
from .policies import make_policy
from .resources import ResourceSpec
from .sim import ExternalEvent
from .trace import Trace

SCENARIOS_SCHEMA = "rubato.scenarios/v1"
POLICIES_SCHEMA = "rubato.policies/v1"
BASE_ASSUMPTION = (
    "re-simulating a trace reorders recorded work; it does not show what the provider would have "
    "answered at another instant (outcomes are replayed per attempt)"
)
HYP = Provenance.HYPOTHETICAL.value


@dataclass
class Prepared:
    name: str
    jobs: list[Job]
    resources: list[ResourceSpec]
    events: list[ExternalEvent]
    horizon: int | None
    assumptions: list[str]
    config: dict


def day_origin(tr: Trace) -> int:
    """UTC midnight of the first arrival (or the generator's declared day)."""
    if "params" in tr.header and "day_start" in tr.header["params"]:
        return int(tr.header["params"]["day_start"])
    first = min(j.arrival for j in tr.jobs)
    return first - first % 86400


def _urgent_job(spec: dict, origin: int, model: str | None, k: int) -> Job:
    at = origin + int(spec["hour_utc"] * 3600)
    cv = spec.get("cv", {"requests": 1, "tokens_in": 14000, "tokens_out": 2500})
    letter = spec.get("letter", {"requests": 1, "tokens_in": 12000, "tokens_out": 1800})
    return Job(
        id=spec.get("id", f"urgent-{k:02d}"),
        client=spec.get("client", "radar"),
        cls=spec.get("class", "application"),
        arrival=at,
        steps=(
            Step("cv", dict(cv), model=model, duration_estimate=spec.get("cv_duration", 40), max_attempts=2),
            Step("letter", dict(letter), ("cv",), model, None, spec.get("letter_duration", 35), 2),
        ),
        urgent=True,
        priority=spec.get("priority", 10),
        deadline=at + int(spec.get("deadline_minutes", 60) * 60),
        provenance={"arrival": HYP, "deadline": HYP, "estimate": HYP, "attempts": "missing"},
    )


def _scale_usage(step: Step, f: float) -> Step:
    outs = tuple(
        Outcome(
            o.duration,
            {k: (v if k == "requests" else math.ceil(v * f)) for k, v in o.usage.items()},
            o.status,
            o.retry_after,
        )
        for o in step.outcomes
    )
    return replace(step, outcomes=outs)


def apply(tr: Trace, name: str, spec: dict) -> Prepared:
    origin = day_origin(tr)
    jobs = list(tr.jobs)
    resources = list(tr.resources)
    events = list(tr.events)
    assumptions = [BASE_ASSUMPTION, *tr.header.get("assumptions", []), *spec.get("assumptions", [])]
    if spec.get("drop_urgent"):
        jobs = [j for j in jobs if not j.urgent]
    for k in range(int(spec.get("burst_factor", 1)) - 1):
        shift = int(spec.get("burst_shift_minutes", 30) * 60) * (k + 1)
        for j in [j for j in tr.jobs if not j.urgent]:
            prov = {**j.provenance}
            if prov.get("arrival") == Provenance.OBSERVED.value:
                prov["arrival"] = HYP
            jobs.append(replace(j, id=f"{j.id}~b{k + 1}", arrival=j.arrival + shift, provenance=prov))
    if "usage_factor" in spec:
        f = float(spec["usage_factor"])
        jobs = [replace(j, steps=tuple(_scale_usage(s, f) for s in j.steps)) for j in jobs]
    model = next((s.model for j in tr.jobs for s in j.steps if s.model), None)
    for k, u in enumerate(spec.get("inject_urgent", [])):
        jobs.append(_urgent_job(u, origin, u.get("model", model), k))
    for rid, f in (spec.get("capacity_factor") or {}).items():
        resources = [replace(r, capacity=int(r.capacity * f)) if r.id == rid else r for r in resources]
    for e in spec.get("external", []):
        usage = {k: int(e[k]) for k in ("requests", "tokens_in", "tokens_out") if k in e}
        events.append(
            ExternalEvent(
                origin + int(e["hour_utc"] * 3600),
                "consume",
                client=e.get("client", "other"),
                usage=usage,
                model=e.get("model", model),
            )
        )
    horizon = origin + int(spec["horizon_hours"] * 3600) if "horizon_hours" in spec else None
    jobs.sort(key=lambda j: (j.arrival, j.id))
    return Prepared(name, jobs, resources, events, horizon, assumptions, spec)


def load_scenarios(path: str | Path) -> dict[str, dict]:
    data = json.loads(Path(path).read_text(encoding="utf-8"))
    if data.get("version") != SCENARIOS_SCHEMA:
        raise ValueError(f"{path}: expected version {SCENARIOS_SCHEMA}")
    return data["scenarios"]


def load_policies(path: str | Path) -> dict:
    data = json.loads(Path(path).read_text(encoding="utf-8"))
    if data.get("version") != POLICIES_SCHEMA:
        raise ValueError(f"{path}: expected version {POLICIES_SCHEMA}")
    return data


def build_policies(cfg: dict, names: list[str], grid: bool = True) -> dict[str, object]:
    """Instantiate the named policies plus, if requested, the precomputed reserve grid."""
    out: dict[str, object] = {}
    for n in names:
        if n not in cfg["policies"]:
            raise ValueError(f"policy {n!r} not in policies file (have {sorted(cfg['policies'])})")
        params = dict(cfg["policies"][n])
        out[n] = make_policy(params.pop("name"), **params)
        if grid and n in cfg.get("grid", {}):
            g = cfg["grid"][n]
            for rid, values in g.get("reserve", {}).items():
                for v in values:
                    p2 = dict(cfg["policies"][n])
                    base = p2.pop("name")
                    p2["reserve"] = {**p2.get("reserve", {}), rid: v}
                    safe = "".join(ch for ch in rid if ch.isalnum())
                    out[f"{n}-{safe}{v}"] = make_policy(base, **p2)
    return out
