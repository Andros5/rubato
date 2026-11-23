"""Transactional admission service on PostgreSQL.

The scheduling pass calls the same ``core.decidir`` as the simulator. Inside one
transaction it locks every resource row in id order (stable order, no deadlocks),
rebuilds the ledger snapshot, decides, and books *all dimensions or none* for each
grant. Permits (grants) are stored for the owning client; the client confirms the
start before calling the provider and settles the usage afterwards.

States
  step:    waiting -> ready -> granted -> running -> done | ready (retry) | failed
           running -> unknown (crash after start) -> done | ready via reconcile
  attempt: granted -> started -> finished | unknown -> reconciled
           granted -> expired (grant TTL) | released (cancel)
  ledger:  reserved -> confirmed | unknown -> confirmed/released
"""

from __future__ import annotations

import hashlib
import json
import os
import secrets
import time
import uuid
from dataclasses import dataclass

import psycopg

from ..core import Capacity, Demand, decidir
from ..model import Action, ReadyItem, Reason, find_cycle, tokens_total
from ..policies import RadarPolicy, make_policy
from ..resources import Entry, Ledger, ResourceSpec

GRANT_TTL = int(os.environ.get("RUBATO_GRANT_TTL", "60"))
ATTEMPT_TIMEOUT = int(os.environ.get("RUBATO_ATTEMPT_TIMEOUT", "600"))
EXECUTOR_TTL = int(os.environ.get("RUBATO_EXECUTOR_TTL", "120"))
LOOKBACK = int(os.environ.get("RUBATO_LEDGER_LOOKBACK", str(3 * 86400)))
EXACT_UNITS = {"requests", "concurrent"}


class ApiError(Exception):
    def __init__(self, status: int, code: str, message: str, extra: dict | None = None):
        super().__init__(message)
        self.status, self.code, self.message, self.extra = status, code, message, extra or {}


def hash_token(token: str) -> str:
    return hashlib.sha256(token.encode()).hexdigest()


def request_hash(body) -> str:
    return hashlib.sha256(json.dumps(body, sort_keys=True, separators=(",", ":")).encode()).hexdigest()


# ---------------------------------------------------------------------------- clock


def now(conn: psycopg.Connection) -> int:
    t = int(time.time())
    if os.environ.get("RUBATO_TEST_CLOCK") == "1":
        r = conn.execute("SELECT offset_s FROM test_clock WHERE id=1").fetchone()
        t += int(r["offset_s"]) if r else 0
    return t


def advance_clock(conn, seconds: int) -> int:
    if os.environ.get("RUBATO_TEST_CLOCK") != "1":
        raise ApiError(403, "clock_disabled", "test clock is disabled (set RUBATO_TEST_CLOCK=1)")
    conn.execute("UPDATE test_clock SET offset_s = offset_s + %s WHERE id=1", (seconds,))
    return now(conn)


# ---------------------------------------------------------------------------- clients & config


def bootstrap_clients(conn, spec: str) -> list[str]:
    """``spec`` = "id:token[:role[:slots]],..." (from RUBATO_CLIENTS). Idempotent."""
    created = []
    for part in [p for p in spec.split(",") if p.strip()]:
        bits = part.strip().split(":")
        cid, token = bits[0], bits[1]
        role = bits[2] if len(bits) > 2 and bits[2] else "client"
        slots = int(bits[3]) if len(bits) > 3 else 1
        conn.execute(
            "INSERT INTO clients (id, token_hash, role, slots, created_at) VALUES (%s,%s,%s,%s,%s) "
            "ON CONFLICT (id) DO UPDATE SET token_hash=EXCLUDED.token_hash, role=EXCLUDED.role, slots=EXCLUDED.slots",
            (cid, hash_token(token), role, slots, int(time.time())),
        )
        created.append(cid)
    return created


def authenticate(conn, token: str | None) -> dict:
    if not token:
        raise ApiError(401, "unauthenticated", "missing bearer token")
    r = conn.execute("SELECT * FROM clients WHERE token_hash=%s", (hash_token(token),)).fetchone()
    if not r:
        raise ApiError(401, "unauthenticated", "unknown token")
    return r


def require_admin(client: dict) -> None:
    if client["role"] != "admin":
        raise ApiError(403, "forbidden", "shared limits and policy are managed by the administrator")


def put_resources(conn, specs: list[dict], by: str) -> list[ResourceSpec]:
    parsed = [ResourceSpec(**{**s, "scope": s.get("scope") or {}}) for s in specs]
    ids = [p.id for p in parsed]
    lock(conn)
    for p in parsed:
        conn.execute(
            "INSERT INTO resources (id, unit, capacity, win, length, reset_tz, win_offset, scope, strict, version) "
            "VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s) ON CONFLICT (id) DO UPDATE SET unit=EXCLUDED.unit, "
            "capacity=EXCLUDED.capacity, win=EXCLUDED.win, length=EXCLUDED.length, reset_tz=EXCLUDED.reset_tz, "
            "win_offset=EXCLUDED.win_offset, scope=EXCLUDED.scope, strict=EXCLUDED.strict, "
            "version=resources.version + 1",
            (
                p.id,
                p.unit,
                p.capacity,
                p.window,
                p.length,
                p.reset_tz,
                p.offset,
                json.dumps(p.scope),
                p.strict,
                p.version,
            ),
        )
    unused = conn.execute(
        "SELECT r.id FROM resources r WHERE NOT (r.id = ANY(%s)) AND NOT EXISTS "
        "(SELECT 1 FROM ledger l WHERE l.resource_id=r.id)",
        (ids,),
    ).fetchall()
    for r in unused:
        conn.execute("DELETE FROM resources WHERE id=%s", (r["id"],))
    conn.execute(
        "INSERT INTO config_versions (kind, body, created_at, created_by) VALUES ('resources', %s, %s, %s)",
        (json.dumps(specs), now(conn), by),
    )
    return parsed


def load_resources(conn) -> list[ResourceSpec]:
    rows = conn.execute("SELECT * FROM resources ORDER BY id").fetchall()
    return [
        ResourceSpec(
            r["id"],
            r["unit"],
            int(r["capacity"]),
            r["win"],
            r["length"],
            r["reset_tz"],
            int(r["win_offset"]),
            r["scope"] or {},
            r["strict"],
            r["version"],
        )
        for r in rows
    ]


def set_policy(conn, cfg: dict, by: str) -> dict:
    params = dict(cfg)
    make_policy(params.pop("name"), **params)  # validate
    row = conn.execute(
        "INSERT INTO config_versions (kind, body, created_at, created_by) VALUES ('policy', %s, %s, %s) RETURNING id",
        (json.dumps(cfg), now(conn), by),
    ).fetchone()
    return {"version": row["id"], **cfg}


def load_policy(conn):
    r = conn.execute(
        "SELECT id, body FROM config_versions WHERE kind='policy' ORDER BY id DESC LIMIT 1"
    ).fetchone()
    if not r:
        return RadarPolicy(), None  # F4 verdict: the reference is the default
    params = dict(r["body"])
    return make_policy(params.pop("name"), **params), r["id"]


def event(conn, t, kind, client_id=None, job_id=None, step_id=None, attempt_id=None, payload=None, corr=None):
    conn.execute(
        "INSERT INTO events (t, kind, client_id, job_id, step_id, attempt_id, corr, payload) VALUES (%s,%s,%s,%s,%s,%s,%s,%s)",
        (t, kind, client_id, job_id, step_id, attempt_id, corr, json.dumps(payload or {})),
    )


# ---------------------------------------------------------------------------- idempotency


def idem_lookup(conn, client_id: str, key: str | None, op: str, body) -> tuple[int, dict] | None:
    if not key:
        return None
    r = conn.execute(
        "SELECT request_hash, status_code, response FROM idempotency WHERE client_id=%s AND key=%s AND op=%s",
        (client_id, key, op),
    ).fetchone()
    if not r:
        return None
    if r["request_hash"] != request_hash(body):
        raise ApiError(409, "idempotency_conflict", f"key {key!r} was already used with different parameters")
    return r["status_code"], r["response"]


def idem_store(conn, client_id: str, key: str | None, op: str, body, status: int, response: dict) -> None:
    if key:
        conn.execute(
            "INSERT INTO idempotency (client_id, key, op, request_hash, status_code, response, created_at) "
            "VALUES (%s,%s,%s,%s,%s,%s,%s) ON CONFLICT DO NOTHING",
            (client_id, key, op, request_hash(body), status, json.dumps(response), now(conn)),
        )


# ---------------------------------------------------------------------------- jobs


def _amounts(v, what) -> dict[str, int]:
    if v is None:
        return {}
    if not isinstance(v, dict) or not all(
        isinstance(x, int) and not isinstance(x, bool) and x >= 0 for x in v.values()
    ):
        raise ApiError(422, "invalid", f"{what} must be an object of non-negative integers")
    return dict(v)


def _meta(meta) -> dict:
    """Planning metadata only: short scalar values, no free text."""
    if not isinstance(meta, dict):
        return {}
    return {
        str(k)[:40]: v
        for k, v in list(meta.items())[:10]
        if isinstance(v, (int, float, bool)) or (isinstance(v, str) and len(v) <= 40)
    }


def _int(v, what: str, lo: int = 0, hi: int = 2**62, nullable: bool = False) -> int | None:
    if v is None and nullable:
        return None
    if isinstance(v, bool) or not isinstance(v, int) or not lo <= v <= hi:
        raise ApiError(422, "invalid", f"{what} must be an integer between {lo} and {hi}")
    return v


def _validate_job(body: dict) -> None:
    """Reject malformed registrations with a 422 before touching the database."""
    if not isinstance(body, dict):
        raise ApiError(422, "invalid", "the job must be a JSON object")
    cjid = body.get("id")
    if not isinstance(cjid, str) or not cjid or len(cjid) > 200:
        raise ApiError(422, "invalid", "job 'id' (stable client id, 1-200 characters) is required")
    if not isinstance(body.get("class", "default"), str):
        raise ApiError(422, "invalid", "'class' must be a string")
    if not isinstance(body.get("urgent", False), bool):
        raise ApiError(422, "invalid", "'urgent' must be true or false")
    _int(body.get("priority", 0), "'priority'", -1000, 1000)
    _int(body.get("deadline"), "'deadline'", nullable=True)
    steps = body.get("steps")
    if not isinstance(steps, list) or not steps or len(steps) > 100:
        raise ApiError(422, "invalid", "a job needs between 1 and 100 steps")
    if not all(isinstance(s, dict) for s in steps):
        raise ApiError(422, "invalid", "each step must be an object")
    sids = [s.get("id") for s in steps]
    if not all(isinstance(x, str) and x for x in sids) or len(set(sids)) != len(sids):
        raise ApiError(422, "invalid", "step ids must be unique non-empty strings")
    for s in steps:
        deps = s.get("depends_on") or []
        if not isinstance(deps, list) or not all(isinstance(d, str) for d in deps):
            raise ApiError(422, "invalid", f"step {s['id']!r}: 'depends_on' must be a list of step ids")
        for d in deps:
            if d not in sids:
                raise ApiError(422, "invalid", f"step {s['id']!r} depends on unknown step {d!r}")
        if s.get("model") is not None and not isinstance(s["model"], str):
            raise ApiError(422, "invalid", f"step {s['id']!r}: 'model' must be a string")
        _int(s.get("duration_estimate", 1), f"step {s['id']!r}: 'duration_estimate'", 0, 86400 * 7)
        _int(s.get("max_attempts", 1), f"step {s['id']!r}: 'max_attempts'", 1, 50)
    cycle = find_cycle({s["id"]: s.get("depends_on") or [] for s in steps})
    if cycle:
        raise ApiError(422, "invalid", "dependency cycle " + " -> ".join(cycle))


def register_job(conn, client: dict, body: dict) -> dict:
    _validate_job(body)
    t = now(conn)
    cjid = body["id"]
    steps = body["steps"]
    allowed = client["models"] or []
    jid = f"{client['id']}:{cjid}"
    existing = conn.execute("SELECT id FROM jobs WHERE id=%s", (jid,)).fetchone()
    if existing:
        raise ApiError(409, "exists", f"job {cjid!r} already registered", {"job": jid})
    specs = load_resources(conn)
    warnings = []
    conn.execute(
        "INSERT INTO jobs (id, client_id, client_job_id, cls, urgent, priority, arrival, deadline, created_at, meta) "
        "VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s)",
        (
            jid,
            client["id"],
            cjid,
            body.get("class", "default"),
            bool(body.get("urgent", False)),
            int(body.get("priority", 0)),
            t,
            body.get("deadline"),
            t,
            json.dumps(_meta(body.get("meta"))),
        ),
    )
    for s in steps:
        model = s.get("model")
        if allowed and model not in allowed:
            raise ApiError(422, "invalid", f"model {model!r} is not declared for client {client['id']}")
        est = tokens_total(_amounts(s.get("estimate"), "estimate"))
        mx = _amounts(s.get("maximum"), "maximum") or None
        cap_mode = "strict"
        for spec in specs:
            if spec.window == "concurrency" or not spec.strict or not spec.applies(client["id"], model):
                continue
            if spec.unit not in EXACT_UNITS and not (mx and spec.unit in tokens_total(mx)):
                cap_mode = "orientative"
                warnings.append(
                    f"step {s['id']}: no enforceable maximum for {spec.unit}; {spec.id} is orientative"
                )
        conn.execute(
            "INSERT INTO steps (job_id, step_id, depends_on, model, estimate, maximum, cap_mode, duration_estimate, max_attempts) "
            "VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s)",
            (
                jid,
                s["id"],
                list(s.get("depends_on") or []),
                model,
                json.dumps(est),
                json.dumps(mx) if mx else None,
                cap_mode,
                int(s.get("duration_estimate", 1)),
                int(s.get("max_attempts", 1)),
            ),
        )
    _refresh_ready(conn, jid, t)
    event(
        conn,
        t,
        "job_registered",
        client["id"],
        jid,
        payload={"class": body.get("class"), "urgent": body.get("urgent")},
    )
    return {"job": jid, "arrival": t, "warnings": warnings}


def _refresh_ready(conn, jid: str, t: int) -> None:
    rows = conn.execute("SELECT step_id, depends_on, state FROM steps WHERE job_id=%s", (jid,)).fetchall()
    st = {r["step_id"]: r["state"] for r in rows}
    for r in rows:
        if r["state"] != "waiting":
            continue
        deps = [st[d] for d in r["depends_on"]]
        if any(d in ("failed", "closed") for d in deps):
            conn.execute(
                "UPDATE steps SET state='closed', last_reason=%s WHERE job_id=%s AND step_id=%s",
                (Reason.DEPENDENCY_FAILED.value, jid, r["step_id"]),
            )
            _decision(
                conn,
                t,
                jid,
                r["step_id"],
                0,
                "service",
                Action.CLOSE,
                Reason.DEPENDENCY_FAILED,
                "a step it depends on did not complete",
            )
            st[r["step_id"]] = "closed"
        elif all(d == "done" for d in deps):
            conn.execute(
                "UPDATE steps SET state='ready', ready_since=%s WHERE job_id=%s AND step_id=%s",
                (t, jid, r["step_id"]),
            )
    _refresh_job(conn, jid)


def _refresh_job(conn, jid: str) -> None:
    states = [r["state"] for r in conn.execute("SELECT state FROM steps WHERE job_id=%s", (jid,)).fetchall()]
    job = conn.execute("SELECT state FROM jobs WHERE id=%s", (jid,)).fetchone()
    if job["state"] != "active":
        return
    if all(s == "done" for s in states):
        conn.execute("UPDATE jobs SET state='done' WHERE id=%s", (jid,))
    elif not any(s in ("waiting", "ready", "granted", "running", "unknown") for s in states):
        conn.execute("UPDATE jobs SET state='failed' WHERE id=%s", (jid,))


def _decision(
    conn, t, jid, sid, attempt, policy, action, reason, detail="", resource=None, value=None, next_t=None
):
    conn.execute(
        "INSERT INTO decisions (t, job_id, step_id, attempt, policy, action, reason, detail, resource, value, next_t) "
        "VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s)",
        (
            t,
            jid,
            sid,
            attempt,
            policy,
            str(action),
            str(reason),
            detail,
            resource,
            json.dumps(value) if value is not None else None,
            next_t,
        ),
    )


def get_job(conn, client: dict, jid: str) -> dict:
    job = conn.execute("SELECT * FROM jobs WHERE id=%s", (jid,)).fetchone()
    if not job or (job["client_id"] != client["id"] and client["role"] != "admin"):
        raise ApiError(404, "not_found", f"job {jid!r} not found")
    steps = conn.execute(
        "SELECT step_id, state, attempts, ready_since, next_reconsider, last_reason, cap_mode FROM steps "
        "WHERE job_id=%s ORDER BY step_id",
        (jid,),
    ).fetchall()
    attempts = conn.execute(
        "SELECT id, step_id, n, state, granted_at, started_at, finished_at, status, usage FROM attempts "
        "WHERE job_id=%s ORDER BY granted_at, n",
        (jid,),
    ).fetchall()
    return {
        **{
            k: job[k]
            for k in ("id", "client_id", "cls", "urgent", "priority", "arrival", "deadline", "state")
        },
        "steps": steps,
        "attempts": attempts,
    }


def cancel_job(conn, client: dict, jid: str) -> dict:
    lock(conn)
    t = now(conn)
    job = conn.execute("SELECT * FROM jobs WHERE id=%s FOR UPDATE", (jid,)).fetchone()
    if not job or (job["client_id"] != client["id"] and client["role"] != "admin"):
        raise ApiError(404, "not_found", f"job {jid!r} not found")
    if job["state"] != "active":
        return {"job": jid, "state": job["state"]}
    for a in conn.execute("SELECT id FROM attempts WHERE job_id=%s AND state='granted'", (jid,)).fetchall():
        conn.execute("UPDATE attempts SET state='released' WHERE id=%s", (a["id"],))
        conn.execute("UPDATE ledger SET state='released', updated_at=%s WHERE attempt_id=%s", (t, a["id"]))
    for s in conn.execute(
        "SELECT step_id, attempts FROM steps WHERE job_id=%s AND state IN ('waiting','ready','granted')",
        (jid,),
    ).fetchall():
        conn.execute(
            "UPDATE steps SET state='closed', last_reason='cancelled' WHERE job_id=%s AND step_id=%s",
            (jid, s["step_id"]),
        )
        _decision(
            conn,
            t,
            jid,
            s["step_id"],
            s["attempts"] + 1,
            "service",
            Action.CLOSE,
            Reason.CANCELLED,
            "job cancelled; future steps closed",
        )
    conn.execute("UPDATE jobs SET state='cancelled' WHERE id=%s", (jid,))
    event(conn, t, "job_cancelled", client["id"], jid)
    return {"job": jid, "state": "cancelled"}


# ---------------------------------------------------------------------------- scheduling


def lock(conn) -> None:
    """Every mutating transaction takes the resource locks first, always in id order.

    This serializes admissions and settlements (no lost updates, no deadlock cycles
    between steps/attempts rows). Throughput is bounded by one transaction at a time,
    which is ample for v1 (decisions take milliseconds).
    """
    conn.execute("SELECT pg_advisory_xact_lock(7342002)")  # also serializes when no resource exists yet
    conn.execute("SELECT id FROM resources ORDER BY id FOR UPDATE")


def _snapshot(conn, t: int) -> tuple[list[ResourceSpec], Capacity]:
    specs = load_resources(conn)
    ledger = Ledger(specs)
    rows = conn.execute(
        "SELECT l.* FROM ledger l JOIN resources r ON r.id=l.resource_id WHERE l.state <> 'released' "
        # total windows and occupied concurrency slots count whatever their age
        "AND (r.win IN ('total', 'concurrency') OR l.t > %s)",
        (t - LOOKBACK,),
    ).fetchall()
    for r in rows:
        ledger.add(
            Entry(
                r["resource_id"],
                int(r["t"]),
                int(r["amount"]),
                r["attempt_id"],
                r["urgent"],
                r["client_id"],
                r["state"],
            )
        )
    pauses = {
        (p["scope_kind"], p["scope_value"]): int(p["until"])
        for p in conn.execute("SELECT * FROM pauses WHERE until > %s", (t,)).fetchall()
    }
    return specs, Capacity(ledger, pauses)


def expire_and_detect(conn, t: int) -> dict:
    """Release grants that were never started; mark started attempts without settlement as unknown."""
    expired = conn.execute(
        "UPDATE attempts SET state='expired' WHERE state='granted' AND grant_expires_at <= %s RETURNING id, job_id, step_id",
        (t,),
    ).fetchall()
    for a in expired:
        conn.execute("UPDATE ledger SET state='released', updated_at=%s WHERE attempt_id=%s", (t, a["id"]))
        conn.execute(
            "UPDATE steps SET state='ready', ready_since=%s WHERE job_id=%s AND step_id=%s AND state='granted'",
            (t, a["job_id"], a["step_id"]),
        )
        event(conn, t, "grant_expired", job_id=a["job_id"], step_id=a["step_id"], attempt_id=a["id"])
    unknown = conn.execute(
        "UPDATE attempts SET state='unknown' WHERE state='started' AND started_at + %s <= %s "
        "RETURNING id, job_id, step_id",
        (ATTEMPT_TIMEOUT, t),
    ).fetchall()
    for a in unknown:
        conn.execute(
            "UPDATE ledger SET state='unknown', updated_at=%s WHERE attempt_id=%s AND state='reserved'",
            (t, a["id"]),
        )
        conn.execute(
            "UPDATE steps SET state='unknown' WHERE job_id=%s AND step_id=%s", (a["job_id"], a["step_id"])
        )
        event(conn, t, "attempt_unknown", job_id=a["job_id"], step_id=a["step_id"], attempt_id=a["id"])
    return {"expired": len(expired), "unknown": len(unknown)}


def schedule_pass(conn, t: int | None = None, focus: tuple[str, str, str] | None = None) -> list[dict]:
    """One admission round for all available clients. Caller commits.

    ``focus=(client, job, step)``: for that client only the named step may receive a
    permit in this round (a filtered claim must not occupy the client's slot with
    another step it is not going to run now).
    """
    t = now(conn) if t is None else t
    lock(conn)
    expire_and_detect(conn, t)
    return _round(conn, t, focus=focus)


def _round(
    conn,
    t: int,
    only: tuple[str, str] | None = None,
    apply: bool = True,
    focus: tuple[str, str, str] | None = None,
):
    """Build the snapshot, call ``decidir`` and (if ``apply``) persist grants and reasons.

    With ``only`` set and ``apply=False`` it returns the would-be decision for one step
    (shadow mode), considering that step even if its client declared no executor.
    """
    specs, cap = _snapshot(conn, t)
    policy, pol_version = load_policy(conn)
    label = policy.label + (f"/cfg{pol_version}" if pol_version else "")
    free: dict[str, int] = {}
    for c in conn.execute(
        "SELECT c.id, c.slots, (SELECT count(*) FROM attempts a WHERE a.client_id=c.id AND a.state IN ('granted','started')) AS busy "
        "FROM clients c WHERE c.available_until >= %s",
        (t,),
    ).fetchall():
        free[c["id"]] = max(0, c["slots"] - int(c["busy"]))
    rows = conn.execute(
        "SELECT s.*, j.client_id, j.cls, j.urgent, j.priority, j.arrival, j.deadline, j.seq "
        "FROM steps s JOIN jobs j ON j.id=s.job_id WHERE j.state='active' ORDER BY j.seq, s.step_id"
    ).fetchall()
    by_job: dict[str, list[dict]] = {}
    for r in rows:
        by_job.setdefault(r["job_id"], []).append(r)
    ready: list[ReadyItem] = []
    demand: list[Demand] = []
    step_rows: dict[tuple[str, str], dict] = {}
    for jid, srows in by_job.items():
        rem: dict[str, int] = {}
        dur = 0
        for s in srows:
            if s["state"] in ("waiting", "ready"):
                for k, v in _admission(s).items():
                    rem[k] = rem.get(k, 0) + v
                dur += int(s["duration_estimate"])
        j0 = srows[0]
        if rem:
            demand.append(Demand(jid, j0["urgent"], j0["deadline"], rem))
        for s in srows:
            target = only is not None and (jid, s["step_id"]) == only
            if s["state"] != "ready" or (free.get(s["client_id"], 0) <= 0 and not target):
                continue
            if focus and s["client_id"] == focus[0] and (jid, s["step_id"]) != (focus[1], focus[2]):
                continue
            step_rows[(jid, s["step_id"])] = s
            ready.append(
                ReadyItem(
                    job_id=jid,
                    client=s["client_id"],
                    cls=s["cls"],
                    urgent=s["urgent"],
                    priority=s["priority"],
                    arrival=int(s["arrival"]),
                    deadline=s["deadline"],
                    step_id=s["step_id"],
                    attempt=s["attempts"] + 1,
                    ready_since=int(s["ready_since"] or t),
                    need=_admission(s),
                    remaining_need=rem,
                    remaining_duration=dur,
                    model=s["model"],
                    seq=int(s["seq"]),
                )
            )
    if not ready:
        return [] if apply else None
    decisions = decidir(t, ready, cap, policy, demand)
    if not apply:
        return next((d for d in decisions if (d.job_id, d.step_id) == only), None)
    grants = []
    for d in decisions:
        s = step_rows[(d.job_id, d.step_id)]
        key = (d.job_id, d.step_id)
        if d.action == Action.AUTHORIZE:
            if free.get(s["client_id"], 0) <= 0:
                continue  # authorized but the owner has no free executor this round
            free[s["client_id"]] -= 1
            grants.append(_grant(conn, t, s, d, label, specs))
        elif d.action == Action.CLOSE:
            conn.execute(
                "UPDATE steps SET state='closed', last_reason=%s WHERE job_id=%s AND step_id=%s",
                (str(d.reason), *key),
            )
            _decision(
                conn,
                t,
                d.job_id,
                d.step_id,
                d.attempt,
                label,
                d.action,
                d.reason,
                d.detail,
                d.resource,
                d.value,
            )
            _refresh_ready(conn, d.job_id, t)
        else:
            if s["last_reason"] != str(d.reason) or s["next_reconsider"] != d.next_t:
                _decision(
                    conn,
                    t,
                    d.job_id,
                    d.step_id,
                    d.attempt,
                    label,
                    d.action,
                    d.reason,
                    d.detail,
                    d.resource,
                    d.value,
                    d.next_t,
                )
            conn.execute(
                "UPDATE steps SET last_reason=%s, next_reconsider=%s WHERE job_id=%s AND step_id=%s",
                (str(d.reason), d.next_t, *key),
            )
    return grants


def _admission(s: dict) -> dict[str, int]:
    amt = dict(s["estimate"])
    if s["maximum"]:
        amt.update(s["maximum"])
    return tokens_total(amt)


def _grant(conn, t, s, d, label, specs) -> dict:
    n = (
        conn.execute(
            "SELECT count(*) AS c FROM attempts WHERE job_id=%s AND step_id=%s", (s["job_id"], s["step_id"])
        ).fetchone()["c"]
        + 1
    )
    aid = str(uuid.uuid4())
    admitted = _admission(s)
    conn.execute(
        "INSERT INTO attempts (id, job_id, step_id, n, client_id, state, admitted, granted_at, grant_expires_at) "
        "VALUES (%s,%s,%s,%s,%s,'granted',%s,%s,%s)",
        (aid, s["job_id"], s["step_id"], n, s["client_id"], json.dumps(admitted), t, t + GRANT_TTL),
    )
    for spec in specs:
        if spec.applies(s["client_id"], s["model"]):
            amount = spec.need_of(admitted)
            if amount:
                conn.execute(
                    "INSERT INTO ledger (resource_id, t, amount, attempt_id, urgent, client_id, state, updated_at) "
                    "VALUES (%s,%s,%s,%s,%s,%s,'reserved',%s)",
                    (spec.id, t, amount, aid, s["urgent"], s["client_id"], t),
                )
    conn.execute(
        "UPDATE steps SET state='granted', last_reason=%s, next_reconsider=NULL WHERE job_id=%s AND step_id=%s",
        (str(d.reason), s["job_id"], s["step_id"]),
    )
    _decision(
        conn, t, d.job_id, d.step_id, d.attempt, label, d.action, d.reason, d.detail, d.resource, d.value
    )
    event(conn, t, "granted", s["client_id"], s["job_id"], s["step_id"], aid, {"admitted": admitted})
    return {"attempt": aid, "job": s["job_id"], "step": s["step_id"]}


def claim(
    conn,
    client: dict,
    slots: int | None = None,
    job: str | None = None,
    step: str | None = None,
    shadow: bool = False,
) -> dict:
    if slots is not None:
        _int(slots, "'slots'", 0, 1000)
    lock(conn)
    t = now(conn)
    if shadow:
        if not (job and step):
            raise ApiError(422, "invalid", "a shadow claim names the job and step it observes")
        st = conn.execute(
            "SELECT s.state FROM steps s JOIN jobs j ON j.id=s.job_id WHERE s.job_id=%s AND s.step_id=%s "
            "AND j.client_id=%s",
            (job, step, client["id"]),
        ).fetchone()
        if not st:
            raise ApiError(404, "not_found", f"step {job}/{step} not found")
        expire_and_detect(conn, t)
        d = _round(conn, t, only=(job, step), apply=False) if st["state"] == "ready" else None
        would = (
            {
                "action": str(d.action),
                "reason": str(d.reason),
                "detail": d.detail,
                "next_t": d.next_t,
                "resource": d.resource,
            }
            if d
            else {"action": "none", "reason": f"step is {st['state']}"}
        )
        event(conn, t, "shadow_decision", client["id"], job, step, payload=would)
        return {"permit": None, "shadow": would}
    if slots is not None:
        conn.execute("UPDATE clients SET slots=%s WHERE id=%s", (slots, client["id"]))
    conn.execute("UPDATE clients SET available_until=%s WHERE id=%s", (t + EXECUTOR_TTL, client["id"]))
    if job and step:
        # A worker asking for one specific step: permits the background planner granted to
        # this client for other steps were never handed to anyone; give their slot back.
        spec_grants = conn.execute(
            "UPDATE attempts SET state='released' WHERE client_id=%s AND state='granted' AND NOT delivered "
            "AND NOT (job_id=%s AND step_id=%s) RETURNING id, job_id, step_id",
            (client["id"], job, step),
        ).fetchall()
        for a in spec_grants:
            conn.execute(
                "UPDATE ledger SET state='released', updated_at=%s WHERE attempt_id=%s", (t, a["id"])
            )
            conn.execute(
                "UPDATE steps SET state='ready' WHERE job_id=%s AND step_id=%s AND state='granted'",
                (a["job_id"], a["step_id"]),
            )
    schedule_pass(conn, t, focus=(client["id"], job, step) if job and step else None)
    a = conn.execute(
        "SELECT a.*, s.model, s.maximum, s.estimate FROM attempts a JOIN steps s ON s.job_id=a.job_id AND s.step_id=a.step_id "
        "WHERE a.client_id=%s AND a.state='granted' AND NOT a.delivered AND a.grant_expires_at > %s "
        "AND (%s::text IS NULL OR a.job_id=%s) AND (%s::text IS NULL OR a.step_id=%s) "
        "ORDER BY a.granted_at, a.id LIMIT 1 FOR UPDATE OF a SKIP LOCKED",
        (client["id"], t, job, job, step, step),
    ).fetchone()
    if a:
        conn.execute("UPDATE attempts SET delivered=true WHERE id=%s", (a["id"],))
        return {
            "permit": {
                "attempt": a["id"],
                "job": a["job_id"],
                "client_job_id": a["job_id"].split(":", 1)[1],
                "step": a["step_id"],
                "n": a["n"],
                "model": a["model"],
                "admitted": a["admitted"],
                "maximum": a["maximum"],
                "start_before": a["grant_expires_at"],
            }
        }
    nxt = conn.execute(
        "SELECT min(s.next_reconsider) AS n FROM steps s JOIN jobs j ON j.id=s.job_id "
        "WHERE j.client_id=%s AND s.state='ready' AND s.next_reconsider IS NOT NULL "
        "AND (%s::text IS NULL OR s.job_id=%s) AND (%s::text IS NULL OR s.step_id=%s)",
        (client["id"], job, job, step, step),
    ).fetchone()["n"]
    retry = max(1, min(int(nxt) - t, 300)) if nxt else 5
    why = None
    if job and step:
        why = conn.execute(
            "SELECT state, last_reason FROM steps WHERE job_id=%s AND step_id=%s", (job, step)
        ).fetchone()
    return {"permit": None, "retry_after": retry, "next_reconsider": nxt, "step": why}


def observe(conn, client: dict, body: dict) -> dict:
    """Shadow mode: the client already called the provider on its own; book the real usage
    (so balances follow reality) and advance the step, without any permit."""
    lock(conn)
    t = now(conn)
    jid, sid = body.get("job"), body.get("step")
    s = conn.execute(
        "SELECT s.*, j.client_id, j.urgent FROM steps s JOIN jobs j ON j.id=s.job_id "
        "WHERE s.job_id=%s AND s.step_id=%s FOR UPDATE OF s",
        (jid, sid),
    ).fetchone()
    if not s or s["client_id"] != client["id"]:
        raise ApiError(404, "not_found", f"step {jid}/{sid} not found")
    status = body.get("status", "ok")
    usage = tokens_total(_amounts(body.get("usage"), "usage"))
    n = (
        conn.execute(
            "SELECT count(*) AS c FROM attempts WHERE job_id=%s AND step_id=%s", (jid, sid)
        ).fetchone()["c"]
        + 1
    )
    aid = f"obs-{uuid.uuid4()}"
    conn.execute(
        "INSERT INTO attempts (id, job_id, step_id, n, client_id, state, admitted, granted_at, grant_expires_at, "
        "started_at, finished_at, status, usage, receipt, delivered) "
        "VALUES (%s,%s,%s,%s,%s,'finished',%s,%s,%s,%s,%s,%s,%s,%s,true)",
        (
            aid,
            jid,
            sid,
            n,
            client["id"],
            json.dumps(usage),
            t,
            t,
            body.get("started_at", t),
            t,
            status,
            json.dumps(usage),
            body.get("receipt"),
        ),
    )
    for spec in load_resources(conn):
        if spec.window != "concurrency" and spec.applies(client["id"], s["model"]):
            amt = spec.need_of(usage)
            if amt:
                conn.execute(
                    "INSERT INTO ledger (resource_id, t, amount, attempt_id, urgent, client_id, state, updated_at) "
                    "VALUES (%s,%s,%s,%s,%s,%s,'confirmed',%s)",
                    (spec.id, t, amt, aid, s["urgent"], client["id"], t),
                )
    conn.execute(
        "UPDATE steps SET attempts=attempts+1, state='running' WHERE job_id=%s AND step_id=%s", (jid, sid)
    )
    _after_outcome(conn, t, {"job_id": jid, "step_id": sid}, status, body.get("retry_after"))
    event(conn, t, "observed", client["id"], jid, sid, aid, {"status": status, "usage": usage})
    return {"attempt": aid, "state": "finished", "observed": True}


# ---------------------------------------------------------------------------- attempts


def _attempt_for(conn, client, aid) -> dict:
    a = conn.execute("SELECT * FROM attempts WHERE id=%s FOR UPDATE", (aid,)).fetchone()
    if not a or (a["client_id"] != client["id"] and client["role"] != "admin"):
        raise ApiError(404, "not_found", f"attempt {aid!r} not found")
    return a


def start(conn, client, aid) -> dict:
    lock(conn)
    t = now(conn)
    a = _attempt_for(conn, client, aid)
    if a["state"] in ("started", "finished", "unknown", "reconciled"):
        return {"attempt": aid, "state": a["state"], "started_at": a["started_at"]}
    if a["state"] != "granted" or a["grant_expires_at"] <= t:
        if a["state"] == "granted":
            expire_and_detect(conn, t)
        raise ApiError(
            409,
            "permit_expired",
            "the permit expired or was released; do not call the provider",
            {"state": "expired" if a["state"] == "granted" else a["state"]},
        )
    conn.execute("UPDATE attempts SET state='started', started_at=%s WHERE id=%s", (t, aid))
    conn.execute(
        "UPDATE steps SET state='running', attempts=attempts+1 WHERE job_id=%s AND step_id=%s",
        (a["job_id"], a["step_id"]),
    )
    event(conn, t, "attempt_started", client["id"], a["job_id"], a["step_id"], aid)
    return {"attempt": aid, "state": "started", "started_at": t}


def _settle(conn, t, a, usage: dict | None, state: str) -> None:
    rows = conn.execute(
        "SELECT l.id, l.resource_id, r.unit, r.win FROM ledger l JOIN resources r ON r.id=l.resource_id "
        "WHERE l.attempt_id=%s AND l.state <> 'released'",
        (a["id"],),
    ).fetchall()
    u = tokens_total(usage or {})
    for r in rows:
        if r["win"] == "concurrency":
            conn.execute("UPDATE ledger SET state='released', updated_at=%s WHERE id=%s", (t, r["id"]))
        elif r["unit"] in u:
            conn.execute(
                "UPDATE ledger SET amount=%s, state=%s, updated_at=%s WHERE id=%s",
                (int(u[r["unit"]]), state, t, r["id"]),
            )
        else:
            conn.execute("UPDATE ledger SET state=%s, updated_at=%s WHERE id=%s", (state, t, r["id"]))


def finish(conn, client, aid, body: dict) -> dict:
    lock(conn)
    t = now(conn)
    a = _attempt_for(conn, client, aid)
    h = request_hash(body)
    if a["state"] in ("finished", "reconciled"):
        if a["finish_request"] != h:
            raise ApiError(409, "conflict", "attempt already settled with a different result")
        return _attempt_view(conn, aid)
    if a["state"] not in ("started", "unknown"):
        raise ApiError(409, "not_started", f"attempt is {a['state']}; call start before the provider")
    status = body.get("status", "ok")
    if status not in ("ok", "invalid", "rate_limited", "error"):
        raise ApiError(422, "invalid", "status must be ok, invalid, rate_limited or error")
    usage = _amounts(body.get("usage"), "usage")
    _settle(conn, t, a, usage, "confirmed")
    conn.execute(
        "UPDATE attempts SET state='finished', finished_at=%s, status=%s, usage=%s, receipt=%s, finish_request=%s WHERE id=%s",
        (t, status, json.dumps(usage), body.get("receipt"), h, aid),
    )
    _after_outcome(conn, t, a, status, body.get("retry_after"))
    event(
        conn,
        t,
        "attempt_finished",
        client["id"],
        a["job_id"],
        a["step_id"],
        aid,
        {"status": status, "usage": usage},
    )
    return _attempt_view(conn, aid)


def _after_outcome(conn, t, a, status, retry_after=None) -> None:
    s = conn.execute(
        "SELECT * FROM steps WHERE job_id=%s AND step_id=%s FOR UPDATE", (a["job_id"], a["step_id"])
    ).fetchone()
    if status == "ok":
        conn.execute(
            "UPDATE steps SET state='done' WHERE job_id=%s AND step_id=%s", (a["job_id"], a["step_id"])
        )
    else:
        if status == "rate_limited":
            until = t + int(retry_after or 1)
            conn.execute(
                "INSERT INTO pauses (scope_kind, scope_value, until, reason) VALUES ('model', %s, %s, '429') "
                "ON CONFLICT (scope_kind, scope_value) DO UPDATE SET until=GREATEST(pauses.until, EXCLUDED.until)",
                (s["model"] or "", until),
            )
        if s["attempts"] >= s["max_attempts"]:
            conn.execute(
                "UPDATE steps SET state='failed', last_reason='max_attempts' WHERE job_id=%s AND step_id=%s",
                (a["job_id"], a["step_id"]),
            )
            _decision(
                conn,
                t,
                a["job_id"],
                a["step_id"],
                s["attempts"],
                "service",
                Action.CLOSE,
                Reason.MAX_ATTEMPTS,
                f"{s['attempts']} attempts, last status {status}",
            )
        else:
            conn.execute(
                "UPDATE steps SET state='ready', ready_since=%s WHERE job_id=%s AND step_id=%s",
                (t, a["job_id"], a["step_id"]),
            )
    _refresh_ready(conn, a["job_id"], t)


def reconcile(conn, client, aid, body: dict) -> dict:
    """Resolve an uncertain attempt with evidence (provider receipt or explicit decision)."""
    lock(conn)
    t = now(conn)
    a = _attempt_for(conn, client, aid)
    h = request_hash(body)
    if a["state"] == "reconciled":
        if a["finish_request"] != h:
            raise ApiError(409, "conflict", "attempt already reconciled differently")
        return _attempt_view(conn, aid)
    if a["state"] not in ("unknown", "started"):
        raise ApiError(409, "not_uncertain", f"attempt is {a['state']}; nothing to reconcile")
    consumed = bool(body.get("consumed"))
    evidence = body.get("receipt") or body.get("evidence") or "none"
    if consumed:
        status = body.get("status", "ok")
        usage = _amounts(body.get("usage"), "usage") or None
        _settle(conn, t, a, usage, "confirmed")  # without usage the conservative admitted amount stays
        conn.execute(
            "UPDATE attempts SET state='reconciled', finished_at=%s, status=%s, usage=%s, receipt=%s, "
            "finish_request=%s WHERE id=%s",
            (t, status, json.dumps(usage or {}), body.get("receipt"), h, aid),
        )
        _after_outcome(conn, t, a, status)
    else:
        conn.execute("UPDATE ledger SET state='released', updated_at=%s WHERE attempt_id=%s", (t, aid))
        conn.execute(
            "UPDATE attempts SET state='reconciled', finished_at=%s, status='not_consumed', finish_request=%s "
            "WHERE id=%s",
            (t, h, aid),
        )
        conn.execute(
            "UPDATE steps SET state='ready', ready_since=%s, attempts=greatest(attempts-1,0) "
            "WHERE job_id=%s AND step_id=%s",
            (t, a["job_id"], a["step_id"]),
        )
    event(
        conn,
        t,
        "attempt_reconciled",
        client["id"],
        a["job_id"],
        a["step_id"],
        aid,
        {"consumed": consumed, "evidence": evidence},
    )
    return _attempt_view(conn, aid)


def _attempt_view(conn, aid) -> dict:
    a = conn.execute(
        "SELECT id, job_id, step_id, n, state, status, usage, started_at, finished_at FROM attempts WHERE id=%s",
        (aid,),
    ).fetchone()
    return {
        "attempt": a["id"],
        "job": a["job_id"],
        "step": a["step_id"],
        "n": a["n"],
        "state": a["state"],
        "status": a["status"],
        "usage": a["usage"],
        "started_at": a["started_at"],
        "finished_at": a["finished_at"],
    }


# ---------------------------------------------------------------------------- views


def decisions(conn, client, job: str | None = None, limit: int = 200) -> list[dict]:
    q = "SELECT d.* FROM decisions d JOIN jobs j ON j.id=d.job_id WHERE (%s OR j.client_id=%s)"
    args: list = [client["role"] == "admin", client["id"]]
    if job:
        q += " AND d.job_id=%s"
        args.append(job)
    q += " ORDER BY d.id DESC LIMIT %s"
    args.append(limit)
    return list(reversed(conn.execute(q, args).fetchall()))


def usage(conn, client) -> dict:
    t = now(conn)
    specs, cap = _snapshot(conn, t)
    res = [
        {
            "id": s.id,
            "unit": s.unit,
            "window": s.window,
            "capacity": s.capacity,
            "used": cap.ledger.used(s.id, t),
            "available": cap.ledger.available(s.id, t),
            "unknown": sum(e.amount for e in cap.ledger.entries[s.id] if e.state == "unknown"),
        }
        for s in specs
    ]
    pending = conn.execute(
        "SELECT s.job_id, s.step_id, s.state, s.last_reason, s.next_reconsider FROM steps s JOIN jobs j ON j.id=s.job_id "
        "WHERE j.state='active' AND s.state IN ('waiting','ready','granted','running','unknown') AND (%s OR j.client_id=%s) "
        "ORDER BY j.seq, s.step_id",
        (client["role"] == "admin", client["id"]),
    ).fetchall()
    return {"t": t, "resources": res, "pending": pending}


def new_token() -> str:
    return secrets.token_urlsafe(24)


@dataclass
class Health:
    ok: bool
    schema: str | None


# ---------------------------------------------------------------------------- modes and overview

MODES = ("off", "shadow", "enforce")


def get_mode(conn, client: dict) -> dict:
    r = conn.execute("SELECT mode, mode_changed_at FROM clients WHERE id=%s", (client["id"],)).fetchone()
    return {"client": client["id"], "mode": r["mode"], "changed_at": r["mode_changed_at"]}


def set_mode(conn, admin: dict, cid: str, mode: str) -> dict:
    if mode not in MODES:
        raise ApiError(422, "invalid", f"mode must be one of {MODES}")
    t = now(conn)
    row = conn.execute(
        "UPDATE clients SET mode=%s, mode_changed_at=%s WHERE id=%s AND role='client' RETURNING id",
        (mode, t, cid),
    ).fetchone()
    if not row:
        raise ApiError(404, "not_found", f"client {cid!r} not found")
    event(conn, t, "mode_changed", cid, payload={"mode": mode, "by": admin["id"]})
    return {"client": cid, "mode": mode, "changed_at": t}


def overview(conn, decisions_limit: int = 60) -> dict:
    """Everything the live panel shows, in one read-only snapshot."""
    t = now(conn)
    specs, cap = _snapshot(conn, t)
    resources = []
    for s in specs:
        unknown = sum(e.amount for e in cap.ledger.entries[s.id] if e.state == "unknown")
        nxt = None
        if s.window == "calendar":
            nxt = s.next_reset(t)
        resources.append(
            {
                "id": s.id,
                "unit": s.unit,
                "window": s.window,
                "capacity": s.capacity,
                "scope": s.scope,
                "used": cap.ledger.used(s.id, t),
                "available": cap.ledger.available(s.id, t),
                "used_urgent": cap.ledger.used(s.id, t, urgent=True),
                "unknown": unknown,
                "resets_at": nxt,
            }
        )
    clients = conn.execute(
        "SELECT id, mode, mode_changed_at, slots, available_until FROM clients WHERE role='client' ORDER BY id"
    ).fetchall()
    queue = conn.execute(
        "SELECT j.client_id, s.state, count(*) AS n, min(s.ready_since) AS oldest FROM steps s "
        "JOIN jobs j ON j.id=s.job_id WHERE j.state='active' GROUP BY 1, 2"
    ).fetchall()
    reasons = conn.execute(
        "SELECT j.client_id, s.last_reason, count(*) AS n, min(s.next_reconsider) AS next FROM steps s "
        "JOIN jobs j ON j.id=s.job_id WHERE j.state='active' AND s.state='ready' AND s.last_reason IS NOT NULL "
        "GROUP BY 1, 2 ORDER BY 3 DESC"
    ).fetchall()
    waiting = conn.execute(
        "SELECT s.job_id, s.step_id, j.client_id, j.cls, j.urgent, j.deadline, s.ready_since, s.last_reason, "
        "s.next_reconsider FROM steps s JOIN jobs j ON j.id=s.job_id WHERE j.state='active' AND s.state='ready' "
        "ORDER BY j.urgent DESC, s.ready_since LIMIT 50"
    ).fetchall()
    recent = conn.execute(
        "SELECT d.id, d.t, d.job_id, d.step_id, d.attempt, d.policy, d.action, d.reason, d.detail, d.next_t "
        "FROM decisions d ORDER BY d.id DESC LIMIT %s",
        (decisions_limit,),
    ).fetchall()
    unknown = conn.execute(
        "SELECT id, job_id, step_id, n, client_id, admitted, started_at FROM attempts WHERE state='unknown' "
        "ORDER BY started_at"
    ).fetchall()
    pauses = conn.execute("SELECT * FROM pauses WHERE until > %s ORDER BY until", (t,)).fetchall()
    policy, version = load_policy(conn)
    totals = conn.execute(
        "SELECT state, count(*) AS n FROM jobs WHERE created_at > %s GROUP BY 1", (t - 86400,)
    ).fetchall()
    return {
        "t": t,
        "resources": resources,
        "clients": clients,
        "queue": queue,
        "reasons": reasons,
        "waiting": waiting,
        "decisions": recent,
        "unknown_attempts": unknown,
        "pauses": pauses,
        "policy": {"label": policy.label, "version": version, "config": policy.config()},
        "jobs_last_24h": {r["state"]: r["n"] for r in totals},
    }
