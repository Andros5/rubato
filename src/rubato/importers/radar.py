"""Read-only importer for Swiss Job Radar history (private data, never committed).

Sources (opened with ``mode=ro``):
* ``google-ai.db`` table ``calls``: one row per provider call. ``created`` is written
  when the call is *reserved*, so it is an upper bound of when the work became ready.
* ``radar.db`` table ``tasks``: queue rows. ``scheduled_at`` can be overwritten on
  reschedule, so it never proves the original arrival either.

Only allowed fields are copied: class (task), model, provider, normalized status,
times, durations and token counts. Free text (``detail``, ``thinking``,
``batch_summary``), payloads and raw references are dropped; references become
opaque HMAC ids keyed by a local salt stored next to the private export.

Job formation (documented heuristic, provenance ``derived``):
* ``job:N`` / ``batch:X`` with task ``match``: calls ordered by ``created``; an
  *episode* ends at the first successful call. Earlier failed calls are retries of
  the same step; a later call opens a new episode (a re-analysis), i.e. a new job.
* ``application:X``: the first episode of each task (cv, letter, ask, documents)
  forms one urgent job with independent steps; later episodes are regenerations
  and become single-step jobs of class ``application_regen``.
* rows without a usable reference cannot form a job and are discarded with a reason.
"""

from __future__ import annotations

import hashlib
import hmac
import json
import math
import secrets
import sqlite3
import statistics
from collections import Counter, defaultdict
from dataclasses import dataclass
from pathlib import Path

from ..model import Job, Outcome, Provenance, Status, Step
from ..trace import SCHEMA, Trace, coverage, dumps

IMPORTER = "rubato-import-radar/1"

CALLS_REQUIRED = {"id", "day", "model", "task", "status", "created", "input_tokens", "output_tokens", "ref"}
CALLS_OPTIONAL = {"duration_ms", "provider", "thoughts_tokens"}
TASKS_REQUIRED = {"id", "kind", "payload", "state", "scheduled_at", "priority"}

OK_STATUSES = {"ok", "ok_sanitized", "ok_partial"}


def normalize_status(raw: str | None) -> Status:
    if raw in OK_STATUSES:
        return Status.OK
    if raw == "invalid_output":
        return Status.INVALID
    if raw == "http_429":
        return Status.RATE_LIMITED
    return Status.ERROR


class SchemaError(RuntimeError):
    pass


@dataclass
class ImportResult:
    trace: Trace
    report: dict


def _ro(path: Path) -> sqlite3.Connection:
    if not path.exists():
        raise FileNotFoundError(path)
    con = sqlite3.connect(f"file:{path.as_posix()}?mode=ro", uri=True)
    con.row_factory = sqlite3.Row
    return con


def _columns(con: sqlite3.Connection, table: str) -> list[str]:
    return [r[1] for r in con.execute(f"PRAGMA table_info({table})")]


def check_schema(con: sqlite3.Connection, table: str, required: set[str]) -> str:
    cols = _columns(con, table)
    if not cols:
        raise SchemaError(f"table {table!r} not found: incompatible schema")
    missing = sorted(required - set(cols))
    if missing:
        raise SchemaError(f"table {table!r} lacks columns {missing}: incompatible schema")
    return hashlib.sha256(",".join(sorted(cols)).encode()).hexdigest()[:12]


def load_salt(out_dir: Path) -> bytes:
    p = out_dir / ".salt"
    if p.exists():
        return bytes.fromhex(p.read_text().strip())
    out_dir.mkdir(parents=True, exist_ok=True)
    salt = secrets.token_bytes(16)
    p.write_text(salt.hex())
    return salt


def opaque(salt: bytes, ref: str) -> str:
    return hmac.new(salt, ref.encode(), hashlib.sha256).hexdigest()[:12]


def import_radar(
    calls_db: Path, tasks_db: Path | None, day_from: str, day_to: str, salt: bytes
) -> ImportResult:
    con = _ro(Path(calls_db))
    calls_schema = check_schema(con, "calls", CALLS_REQUIRED)
    cols = set(_columns(con, "calls"))
    opt = [c for c in sorted(CALLS_OPTIONAL) if c in cols]
    sel = ", ".join(sorted(CALLS_REQUIRED) + opt)
    rows = con.execute(
        f"SELECT {sel} FROM calls WHERE day BETWEEN ? AND ? ORDER BY created, id", (day_from, day_to)
    ).fetchall()
    con.close()

    priorities: dict[str, int] = {}
    tasks_schema = None
    tasks_note = "radar.db not provided"
    if tasks_db is not None and Path(tasks_db).exists():
        tcon = _ro(Path(tasks_db))
        tasks_schema = check_schema(tcon, "tasks", TASKS_REQUIRED)
        for r in tcon.execute(
            "SELECT kind, payload, priority FROM tasks WHERE kind IN ('analyze_job','write_doc')"
        ):
            try:
                payload = json.loads(r["payload"])
            except (TypeError, ValueError):
                continue
            if r["kind"] == "analyze_job" and "job_id" in payload:
                priorities[f"job:{payload['job_id']}"] = max(
                    priorities.get(f"job:{payload['job_id']}", 0), r["priority"] or 0
                )
            if r["kind"] == "write_doc" and "application_id" in payload:
                key = f"application:{payload['application_id']}"
                priorities[key] = max(priorities.get(key, 0), r["priority"] or 0)
        tcon.close()
        tasks_note = "priority linked from tasks.payload (job_id / application_id)"

    read = len(rows)
    discarded: Counter[str] = Counter()
    groups: dict[tuple[str, str], list[sqlite3.Row]] = defaultdict(list)
    for r in rows:
        ref = r["ref"]
        if ref is None:
            discarded["no_ref"] += 1
            continue
        kind, _, ident = ref.partition(":")
        if not ident:
            discarded["empty_ref"] += 1
        elif kind == "test":
            discarded["test_ref"] += 1
        elif kind in ("job", "batch") and r["task"] == "match":
            groups[(ref, "match")].append(r)
        elif kind == "application":
            groups[(ref, r["task"])].append(r)
        else:
            discarded[f"unsupported_ref:{kind}/{r['task']}"] += 1

    def episodes(calls: list[sqlite3.Row]) -> list[list[sqlite3.Row]]:
        eps, cur = [], []
        for c in calls:
            cur.append(c)
            if c["status"] in OK_STATUSES:
                eps.append(cur)
                cur = []
        if cur:
            eps.append(cur)
        return eps

    # class medians from included successful calls -> estimates (derived)
    def med(vals: list[int], default: int) -> int:
        return int(statistics.median(vals)) if vals else default

    samples: dict[str, dict[str, list[int]]] = defaultdict(lambda: defaultdict(list))
    for (_, task), calls in groups.items():
        for c in calls:
            if c["status"] in OK_STATUSES:
                if c["input_tokens"] is not None:
                    samples[task]["tokens_in"].append(c["input_tokens"])
                if c["output_tokens"] is not None:
                    samples[task]["tokens_out"].append(c["output_tokens"])
                if "duration_ms" in c.keys() and c["duration_ms"]:
                    samples[task]["duration"].append(math.ceil(c["duration_ms"] / 1000))
    est = {
        task: {
            "tokens_in": med(s["tokens_in"], 0),
            "tokens_out": med(s["tokens_out"], 0),
            "duration": max(1, med(s["duration"], 1)),
        }
        for task, s in samples.items()
    }

    missing_counts: Counter[str] = Counter()

    def make_step(task: str, calls: list[sqlite3.Row]) -> Step:
        e = est.get(task, {"tokens_in": 0, "tokens_out": 0, "duration": 1})
        outcomes = []
        for c in calls:
            usage = {"requests": 1}
            for src, dst in (("input_tokens", "tokens_in"), ("output_tokens", "tokens_out")):
                if c[src] is None:
                    missing_counts[f"attempt.{dst}"] += 1
                else:
                    usage[dst] = int(c[src])
                    if c[src] == 0:
                        missing_counts[f"attempt.{dst}.zero"] += 1
            dms = c["duration_ms"] if "duration_ms" in c.keys() else None
            if dms is None:
                missing_counts["attempt.duration"] += 1
                dur = e["duration"]
            else:
                dur = max(0, math.ceil(dms / 1000))
            outcomes.append(Outcome(dur, usage, normalize_status(c["status"])))
        models = [c["model"] for c in calls if c["model"]]
        return Step(
            id=task,
            estimate={"requests": 1, "tokens_in": e["tokens_in"], "tokens_out": e["tokens_out"]},
            model=models[0] if models else None,
            duration_estimate=e["duration"],
            max_attempts=len(outcomes),
            outcomes=tuple(outcomes),
        )

    jobs: list[Job] = []
    prov_base = {
        "arrival": Provenance.DERIVED.value,
        "deadline": Provenance.MISSING.value,
        "estimate": Provenance.DERIVED.value,
        "attempts": Provenance.OBSERVED.value,
    }
    included = 0
    app_steps: dict[str, list[tuple[str, list[sqlite3.Row]]]] = defaultdict(list)
    for (ref, task), calls in sorted(groups.items(), key=lambda kv: (kv[1][0]["created"], kv[0])):
        eps = episodes(calls)
        included += len(calls)
        oid = opaque(salt, ref)
        if ref.startswith("application:"):
            app_steps[ref].append((task, eps[0]))
            for k, ep in enumerate(eps[1:], 1):
                jobs.append(
                    _job(
                        f"app-{oid}-{task}-r{k}",
                        "application_regen",
                        True,
                        ref,
                        [make_step(task, ep)],
                        ep,
                        priorities,
                        prov_base,
                    )
                )
            continue
        cls = "match" if ref.startswith("job:") else "match_batch"
        for k, ep in enumerate(eps):
            jobs.append(
                _job(f"{cls}-{oid}-e{k}", cls, False, ref, [make_step(task, ep)], ep, priorities, prov_base)
            )
    for ref, parts in app_steps.items():
        oid = opaque(salt, ref)
        steps = [make_step(task, ep) for task, ep in sorted(parts)]
        all_calls = [c for _, ep in parts for c in ep]
        jobs.append(_job(f"app-{oid}", "application", True, ref, steps, all_calls, priorities, prov_base))
    jobs.sort(key=lambda j: (j.arrival, j.id))

    header = {
        "type": "header",
        "schema": SCHEMA,
        "time_unit": "s",
        "created_by": IMPORTER,
        "source": {
            "kind": "swiss-job-radar",
            "calls_schema": calls_schema,
            "tasks_schema": tasks_schema,
            "day_from": day_from,
            "day_to": day_to,
        },
        "assumptions": [
            "arrival = created of the first call of the job (derived; upper bound of readiness)",
            "deadlines are unknown (missing); scenarios may add hypothetical ones",
            "estimate = median of successful calls of the same class in this export (derived)",
            "episode ends at the first successful call; later calls form new jobs",
            "usage counts requests=1 per call; tokens_out excludes thoughts tokens",
            tasks_note,
        ],
    }
    tr = Trace(header=header, jobs=jobs)
    text = dumps(tr)
    report = {
        "importer": IMPORTER,
        "range": [day_from, day_to],
        "read": read,
        "included": included,
        "discarded": dict(sorted(discarded.items())),
        "reconciled": read == included + sum(discarded.values()),
        "jobs": len(jobs),
        "jobs_by_class": dict(sorted(Counter(j.cls for j in jobs).items())),
        "attempts": sum(len(s.outcomes) for j in jobs for s in j.steps),
        "status": dict(
            sorted(Counter(str(o.status) for j in jobs for s in j.steps for o in s.outcomes).items())
        ),
        "missing": dict(sorted(missing_counts.items())),
        "control": {
            "calls": read,
            "match": sum(1 for r in rows if r["task"] == "match"),
            "positive_duration": sum(
                1 for r in rows if "duration_ms" in r.keys() and (r["duration_ms"] or 0) > 0
            ),
            "positive_tokens_in": sum(1 for r in rows if (r["input_tokens"] or 0) > 0),
        },
        "coverage": coverage(tr),
        "fingerprint": hashlib.sha256(text.encode()).hexdigest()[:16],
    }
    return ImportResult(tr, report)


def _job(jid, cls, urgent, ref, steps, calls, priorities, prov_base) -> Job:
    prov = dict(prov_base)
    prov["priority"] = Provenance.OBSERVED.value if ref in priorities else Provenance.MISSING.value
    if urgent:
        prov["urgent"] = Provenance.DERIVED.value  # by class rule
    return Job(
        id=jid,
        client="radar",
        cls=cls,
        arrival=int(min(c["created"] for c in calls)),
        steps=tuple(steps),
        urgent=urgent,
        priority=priorities.get(ref, 0),
        deadline=None,
        provenance=prov,
    )
