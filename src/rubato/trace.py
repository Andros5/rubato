"""Trace format ``rubato.trace/v1`` (JSON Lines).

Line 1 is a header; the rest are ``resource``, ``job`` and ``event`` records.
Every job carries a ``provenance`` map from field path to one of
observed | derived | synthetic | hypothetical | missing. A ``null`` value is
"unknown"; ``0`` is an observed zero. Arrivals of different provenance are
never merged in reports.

Steps carry two kinds of numbers:
* ``estimate`` / ``maximum`` / ``duration_estimate``: what a policy may know before running.
* ``attempts``: the ground truth, revealed to the simulator only when each attempt finishes.
"""

from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from .model import Job, Outcome, Provenance, Status, Step, find_cycle
from .resources import WINDOW_KINDS, ResourceSpec
from .sim import ExternalEvent

SCHEMA = "rubato.trace/v1"
PROVENANCES = {p.value for p in Provenance}
STATUSES = {s.value for s in Status}


class TraceError(ValueError):
    """Invalid trace. The message names the line and the field."""


@dataclass
class Trace:
    header: dict[str, Any]
    resources: list[ResourceSpec] = field(default_factory=list)
    jobs: list[Job] = field(default_factory=list)
    events: list[ExternalEvent] = field(default_factory=list)

    def fingerprint(self) -> str:
        return hashlib.sha256(dumps(self).encode()).hexdigest()[:16]


# ----------------------------------------------------------------------------
# validation helpers


def _err(line: int, where: str, msg: str) -> TraceError:
    return TraceError(f"line {line}: {where}: {msg}")


def _req(d: dict, key: str, typ, line: int, where: str, nullable: bool = False):
    if key not in d:
        raise _err(line, where, f"missing field '{key}'")
    v = d[key]
    if v is None and nullable:
        return None
    if typ is int and (isinstance(v, bool) or not isinstance(v, int)):
        raise _err(line, where, f"field '{key}' must be an integer, got {v!r}")
    if typ is not int and not isinstance(v, typ):
        raise _err(line, where, f"field '{key}' must be {getattr(typ, '__name__', typ)}, got {v!r}")
    return v


def _amounts(v: Any, line: int, where: str, key: str, nullable: bool = False) -> dict[str, int] | None:
    if v is None and nullable:
        return None
    if not isinstance(v, dict) or not all(
        isinstance(k, str) and isinstance(x, int) and not isinstance(x, bool) and x >= 0 for k, x in v.items()
    ):
        raise _err(line, where, f"field '{key}' must be an object of non-negative integers, got {v!r}")
    return dict(v)


def _prov(v: Any, line: int, where: str) -> dict[str, str]:
    if v is None:
        return {}
    if not isinstance(v, dict):
        raise _err(line, where, "field 'provenance' must be an object")
    for k, p in v.items():
        if p not in PROVENANCES:
            raise _err(line, where, f"provenance of '{k}' is {p!r}; expected one of {sorted(PROVENANCES)}")
    return dict(v)


def _resource(d: dict, line: int) -> ResourceSpec:
    where = f"resource {d.get('id')!r}"
    window = _req(d, "window", str, line, where)
    if window not in WINDOW_KINDS:
        raise _err(line, where, f"window {window!r} not in {WINDOW_KINDS}")
    try:
        return ResourceSpec(
            id=_req(d, "id", str, line, where),
            unit=_req(d, "unit", str, line, where),
            capacity=_req(d, "capacity", int, line, where),
            window=window,
            length=d.get("length"),
            reset_tz=d.get("reset_tz"),
            offset=d.get("offset", 0),
            scope=d.get("scope") or {},
            strict=d.get("strict", True),
            version=d.get("version", 1),
        )
    except ValueError as e:
        raise _err(line, where, str(e)) from e


def _step(d: dict, line: int, jwhere: str) -> Step:
    if not isinstance(d, dict):
        raise _err(line, jwhere, "each step must be an object")
    sid = _req(d, "id", str, line, jwhere)
    where = f"{jwhere} step {sid!r}"
    outcomes = []
    for i, a in enumerate(d.get("attempts") or [], 1):
        aw = f"{where} attempt {i}"
        status = a.get("status", "ok")
        if status not in STATUSES:
            raise _err(line, aw, f"status {status!r}; expected one of {sorted(STATUSES)}")
        dur = a.get("duration")
        if dur is None:
            dur = d.get("duration_estimate", 1)
        if not isinstance(dur, int) or dur < 0:
            raise _err(line, aw, f"field 'duration' must be a non-negative integer, got {dur!r}")
        usage = {k: v for k, v in (a.get("usage") or {}).items() if v is not None}  # null = unknown
        outcomes.append(
            Outcome(dur, _amounts(usage, line, aw, "usage"), Status(status), a.get("retry_after"))
        )
    return Step(
        id=sid,
        estimate=_amounts(_req(d, "estimate", dict, line, where), line, where, "estimate"),
        depends_on=tuple(d.get("depends_on") or ()),
        model=d.get("model"),
        maximum=_amounts(d.get("maximum"), line, where, "maximum", nullable=True),
        duration_estimate=d.get("duration_estimate", 1),
        max_attempts=d.get("max_attempts", max(1, len(outcomes))),
        outcomes=tuple(outcomes),
    )


def _job(d: dict, line: int) -> Job:
    jid = _req(d, "id", str, line, "job")
    where = f"job {jid!r}"
    steps_raw = _req(d, "steps", list, line, where)
    if not steps_raw:
        raise _err(line, where, "a job needs at least one step")
    steps = tuple(_step(s, line, where) for s in steps_raw)
    ids = {s.id for s in steps}
    if len(ids) != len(steps):
        raise _err(line, where, "duplicate step ids")
    for s in steps:
        for dep in s.depends_on:
            if dep not in ids:
                raise _err(line, where, f"step {s.id!r} depends on unknown step {dep!r}")
    cycle = find_cycle({s.id: s.depends_on for s in steps})
    if cycle:
        raise _err(line, where, "dependency cycle " + " -> ".join(cycle))
    prov = _prov(d.get("provenance"), line, where)
    if "arrival" not in prov:
        raise _err(
            line, where, "provenance.arrival is required (observed, derived, synthetic or hypothetical)"
        )
    return Job(
        id=jid,
        client=_req(d, "client", str, line, where),
        cls=_req(d, "class", str, line, where),
        arrival=_req(d, "arrival", int, line, where),
        steps=steps,
        urgent=bool(d.get("urgent", False)),
        priority=d.get("priority", 0) or 0,
        deadline=_req(d, "deadline", int, line, where, nullable=True) if "deadline" in d else None,
        provenance=prov,
        known_at=d.get("known_at"),
    )


def _event(d: dict, line: int) -> ExternalEvent:
    kind = _req(d, "kind", str, line, "event")
    if kind not in ("cancel", "client_down", "client_up", "pause", "consume"):
        raise _err(line, "event", f"unknown kind {kind!r}")
    scope = d.get("scope")
    return ExternalEvent(
        t=_req(d, "t", int, line, f"event {kind}"),
        kind=kind,
        job_id=d.get("job"),
        client=d.get("client"),
        scope=tuple(scope) if scope else None,
        until=d.get("until"),
        usage=d.get("usage"),
        model=d.get("model"),
    )


def loads(text: str) -> Trace:
    lines = [(i, ln) for i, ln in enumerate(text.splitlines(), 1) if ln.strip()]
    if not lines:
        raise TraceError("empty trace: expected a header line")
    recs = []
    for i, ln in lines:
        try:
            rec = json.loads(ln)
        except json.JSONDecodeError as e:
            raise TraceError(f"line {i}: not valid JSON ({e.msg})") from e
        if not isinstance(rec, dict) or "type" not in rec:
            raise TraceError(f"line {i}: each line must be an object with a 'type' field")
        recs.append((i, rec))
    i0, header = recs[0]
    if header["type"] != "header":
        raise TraceError(f"line {i0}: first record must be the header")
    if header.get("schema") != SCHEMA:
        raise TraceError(f"line {i0}: schema {header.get('schema')!r} is not supported (expected {SCHEMA!r})")
    tr = Trace(header=header)
    seen: set[str] = set()
    for i, rec in recs[1:]:
        t = rec["type"]
        if t == "resource":
            tr.resources.append(_resource(rec, i))
        elif t == "job":
            j = _job(rec, i)
            if j.id in seen:
                raise TraceError(f"line {i}: job {j.id!r} appears twice")
            seen.add(j.id)
            tr.jobs.append(j)
        elif t == "event":
            tr.events.append(_event(rec, i))
        else:
            raise TraceError(f"line {i}: unknown record type {t!r}")
    return tr


def load(path: str | Path) -> Trace:
    return loads(Path(path).read_text(encoding="utf-8"))


# ----------------------------------------------------------------------------
# writing (canonical: same trace -> same bytes)


def resource_to_dict(r: ResourceSpec) -> dict:
    d: dict[str, Any] = {
        "type": "resource",
        "id": r.id,
        "unit": r.unit,
        "capacity": r.capacity,
        "window": r.window,
    }
    if r.length is not None:
        d["length"] = r.length
    if r.reset_tz:
        d["reset_tz"] = r.reset_tz
    if r.offset:
        d["offset"] = r.offset
    if r.scope:
        d["scope"] = dict(r.scope)
    if not r.strict:
        d["strict"] = False
    if r.version != 1:
        d["version"] = r.version
    return d


def step_to_dict(s: Step) -> dict:
    d: dict[str, Any] = {"id": s.id, "estimate": dict(s.estimate)}
    if s.depends_on:
        d["depends_on"] = list(s.depends_on)
    if s.model is not None:
        d["model"] = s.model
    if s.maximum:
        d["maximum"] = dict(s.maximum)
    d["duration_estimate"] = s.duration_estimate
    d["max_attempts"] = s.max_attempts
    if s.outcomes:
        d["attempts"] = [
            {"duration": o.duration, "usage": dict(o.usage), "status": str(o.status)}
            | ({"retry_after": o.retry_after} if o.retry_after is not None else {})
            for o in s.outcomes
        ]
    return d


def job_to_dict(j: Job) -> dict:
    d: dict[str, Any] = {
        "type": "job",
        "id": j.id,
        "client": j.client,
        "class": j.cls,
        "arrival": j.arrival,
        "deadline": j.deadline,
        "urgent": j.urgent,
        "priority": j.priority,
        "provenance": dict(j.provenance),
        "steps": [step_to_dict(s) for s in j.steps],
    }
    if j.known_at is not None:
        d["known_at"] = j.known_at
    return d


def event_to_dict(e: ExternalEvent) -> dict:
    d: dict[str, Any] = {"type": "event", "kind": e.kind, "t": e.t}
    for k, v in (
        ("job", e.job_id),
        ("client", e.client),
        ("until", e.until),
        ("usage", e.usage),
        ("model", e.model),
    ):
        if v is not None:
            d[k] = v
    if e.scope:
        d["scope"] = list(e.scope)
    return d


def _line(d: dict) -> str:
    return json.dumps(d, sort_keys=True, ensure_ascii=False, separators=(",", ":"))


def dumps(tr: Trace) -> str:
    out = [_line({"type": "header", **{k: v for k, v in tr.header.items() if k != "type"}})]
    out += [_line(resource_to_dict(r)) for r in tr.resources]
    out += [_line(job_to_dict(j)) for j in tr.jobs]
    out += [_line(event_to_dict(e)) for e in tr.events]
    return "\n".join(out) + "\n"


def dump(tr: Trace, path: str | Path) -> None:
    p = Path(path)
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(dumps(tr), encoding="utf-8", newline="\n")


def coverage(tr: Trace) -> dict:
    """Per field: how many jobs have each provenance. Arrivals are always split."""
    cov: dict[str, dict[str, int]] = {}
    for j in tr.jobs:
        for k, p in j.provenance.items():
            cov.setdefault(k, {}).setdefault(p, 0)
            cov[k][p] += 1
    return {"jobs": len(tr.jobs), "fields": {k: dict(sorted(v.items())) for k, v in sorted(cov.items())}}
