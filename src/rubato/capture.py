"""Prospective capture: an optional, fail-safe event recorder for client applications.

Events (schema ``rubato.capture/v1``), one JSON object per line:

* ``job_enqueued``    job, client, class, urgent, priority, deadline (optional)
* ``step_ready``      job, step, depends_on
* ``attempt_started`` job, step, attempt, model, provider (optional), estimate
* ``attempt_finished`` job, step, attempt, status, usage, duration_ms

Every event has ``t`` (epoch seconds, float), ``corr`` (correlation id) and only
planning metadata: no prompts, documents, offers or credentials. Recording
failures never propagate to the host application (shadow-safe).

``to_trace`` turns a capture into a ``rubato.trace/v1`` trace whose arrivals are
**observed** (the enqueue instant was recorded when it happened).
"""

from __future__ import annotations

import json
import logging
import os
import threading
import time
from collections import defaultdict
from pathlib import Path
from typing import Any

from .model import Job, Outcome, Provenance, Status, Step
from .trace import SCHEMA, Trace

CAPTURE_SCHEMA = "rubato.capture/v1"
KINDS = ("job_enqueued", "step_ready", "attempt_started", "attempt_finished")
ALLOWED = {
    "job_enqueued": {"job", "client", "class", "urgent", "priority", "deadline"},
    "step_ready": {"job", "step", "depends_on"},
    "attempt_started": {"job", "step", "attempt", "model", "provider", "estimate"},
    "attempt_finished": {"job", "step", "attempt", "status", "usage", "duration_ms"},
}
log = logging.getLogger("rubato.capture")


class CaptureLogger:
    """Append-only JSONL recorder. ``enabled=False`` (or RUBATO_CAPTURE=off) is a no-op."""

    def __init__(self, path: str | Path | None, enabled: bool | None = None, clock=time.time):
        if enabled is None:
            enabled = os.environ.get("RUBATO_CAPTURE", "on").lower() not in ("0", "off", "false")
        self.path = Path(path) if path else None
        self.enabled = bool(enabled and self.path)
        self.clock = clock
        self._lock = threading.Lock()
        self.dropped = 0

    def emit(self, kind: str, corr: str, **fields: Any) -> None:
        if not self.enabled:
            return
        try:
            if kind not in ALLOWED:
                raise ValueError(f"unknown capture event {kind!r}")
            extra = set(fields) - ALLOWED[kind]
            if extra:
                raise ValueError(f"fields not allowed in {kind}: {sorted(extra)}")
            rec = {
                "schema": CAPTURE_SCHEMA,
                "kind": kind,
                "t": round(self.clock(), 3),
                "corr": corr,
                **fields,
            }
            line = json.dumps(rec, sort_keys=True, ensure_ascii=False)
            with self._lock:
                assert self.path is not None
                self.path.parent.mkdir(parents=True, exist_ok=True)
                with self.path.open("a", encoding="utf-8", newline="\n") as fh:
                    fh.write(line + "\n")
        except Exception as e:  # never break the host application
            self.dropped += 1
            log.warning("rubato capture dropped an event: %s", e)


def read_capture(path: str | Path) -> list[dict]:
    out = []
    for ln in Path(path).read_text(encoding="utf-8").splitlines():
        if ln.strip():
            rec = json.loads(ln)
            if rec.get("schema") == CAPTURE_SCHEMA:
                out.append(rec)
    return out


def to_trace(events: list[dict], estimates: dict[str, dict[str, int]] | None = None) -> Trace:
    """Rebuild jobs from captured events. Unfinished attempts are kept out and counted."""
    jobs: dict[str, dict] = {}
    steps: dict[str, dict[str, dict]] = defaultdict(dict)
    for e in sorted(events, key=lambda e: (e["t"], KINDS.index(e["kind"]))):
        j = e.get("job")
        if e["kind"] == "job_enqueued":
            jobs.setdefault(j, {**e})
        elif e["kind"] == "step_ready":
            steps[j].setdefault(e["step"], {"depends_on": e.get("depends_on") or [], "attempts": {}})
        elif e["kind"] == "attempt_started":
            st = steps[j].setdefault(e["step"], {"depends_on": [], "attempts": {}})
            st["attempts"].setdefault(e["attempt"], {}).update(
                start=e["t"], model=e.get("model"), provider=e.get("provider"), estimate=e.get("estimate")
            )
        elif e["kind"] == "attempt_finished":
            st = steps[j].setdefault(e["step"], {"depends_on": [], "attempts": {}})
            st["attempts"].setdefault(e["attempt"], {}).update(
                finish=e["t"],
                status=e.get("status", "ok"),
                usage=e.get("usage") or {},
                duration_ms=e.get("duration_ms"),
            )
    out: list[Job] = []
    unfinished = 0
    for jid, je in jobs.items():
        st_objs = []
        for sid, st in steps[jid].items():
            outs, model, provider, estimate = [], None, None, None
            for _, a in sorted(st["attempts"].items()):
                model = model or a.get("model")
                provider = provider or a.get("provider")
                estimate = estimate or a.get("estimate")
                if "finish" not in a:
                    unfinished += 1
                    continue
                dur = a.get("duration_ms")
                dur_s = (
                    int(round(dur / 1000))
                    if dur is not None
                    else int(round(a["finish"] - a.get("start", a["finish"])))
                )
                outs.append(Outcome(dur_s, {k: int(v) for k, v in a["usage"].items()}, Status(a["status"])))
            est = estimate or (estimates or {}).get(je.get("class", ""), {"requests": 1})
            st_objs.append(
                Step(
                    sid,
                    dict(est),
                    tuple(st["depends_on"]),
                    model,
                    None,
                    max(1, outs[0].duration if outs else 1),
                    max(1, len(outs)),
                    tuple(outs),
                    provider,
                )
            )
        if not st_objs:
            continue
        prov = {
            "arrival": Provenance.OBSERVED.value,
            "deadline": Provenance.OBSERVED.value
            if je.get("deadline") is not None
            else Provenance.MISSING.value,
        }
        out.append(
            Job(
                jid,
                je.get("client", "unknown"),
                je.get("class", "unknown"),
                int(je["t"]),
                tuple(st_objs),
                bool(je.get("urgent")),
                int(je.get("priority") or 0),
                int(je["deadline"]) if je.get("deadline") is not None else None,
                prov,
            )
        )
    header = {
        "type": "header",
        "schema": SCHEMA,
        "time_unit": "s",
        "created_by": "rubato-capture/1",
        "assumptions": ["arrival = observed job_enqueued instant"],
        "unfinished_attempts": unfinished,
    }
    return Trace(header=header, jobs=sorted(out, key=lambda j: (j.arrival, j.id)))
