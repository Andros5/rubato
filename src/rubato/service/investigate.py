"""Server-side read models for investigation and cost reporting."""

from __future__ import annotations

import json

from ..core import Capacity, decidir
from ..policies import make_policy
from ..resources import Entry, Ledger, ResourceSpec
from ..risk import Estimate, estimate_duration, forecast
from ..tariffs import Tariff
from . import recording, store


def _client(client, owner):
    return owner if client["role"] == "admin" else client["id"]


def _cursor(raw, max_value: int) -> tuple[int, int]:
    if not raw:
        return max_value, max_value + 1
    try:
        fence, last = map(int, raw.split("."))
        if not 0 <= last <= fence <= max_value:
            raise ValueError
    except (ValueError, OverflowError) as exc:
        raise store.ApiError(422, "invalid_cursor", "invalid pagination cursor") from exc
    return fence, last


def _page(rows, fence, limit, key):
    return {
        "items": rows[:limit],
        "next_cursor": f"{fence}.{rows[limit - 1][key]}" if len(rows) > limit else None,
    }


def jobs(
    conn,
    client,
    *,
    owner=None,
    q=None,
    state=None,
    reason=None,
    resource=None,
    filter_by=None,
    since=None,
    cursor=None,
    limit=30,
):
    high = conn.execute("SELECT coalesce(max(seq),0) AS n FROM jobs").fetchone()["n"]
    fence, last = _cursor(cursor, high)
    owner = _client(client, owner)
    t = store.now(conn)
    rows = conn.execute(
        "SELECT j.id, j.seq, j.client_id, j.cls, j.arrival, j.deadline, j.urgent, j.state "
        "FROM jobs j WHERE j.seq<=%(fence)s AND j.seq<%(last)s "
        "AND (%(owner)s::text IS NULL OR j.client_id=%(owner)s) "
        "AND (%(state)s::text IS NULL OR j.state=%(state)s) "
        "AND (%(q)s::text IS NULL OR strpos(lower(j.id),lower(%(q)s))>0 OR EXISTS "
        "(SELECT 1 FROM attempts a WHERE a.job_id=j.id AND a.id=%(q)s)) "
        "AND (%(reason)s::text IS NULL AND %(resource)s::text IS NULL OR EXISTS "
        "(SELECT 1 FROM decisions d WHERE d.job_id=j.id AND (%(reason)s::text IS NULL OR d.reason=%(reason)s) "
        "AND (%(resource)s::text IS NULL OR d.resource=%(resource)s) AND (%(since)s::bigint IS NULL OR d.t>=%(since)s))) "
        "AND (%(filter_by)s::text IS NULL "
        "OR (%(filter_by)s='unknown' AND EXISTS (SELECT 1 FROM attempts a WHERE a.job_id=j.id AND a.state='unknown')) "
        "OR (%(filter_by)s='waiting' AND j.state='active' AND EXISTS (SELECT 1 FROM steps s WHERE s.job_id=j.id AND s.state='ready')) "
        "OR (%(filter_by)s='missed' AND j.urgent AND j.deadline BETWEEN coalesce(%(since)s,0) AND %(t)s "
        "AND (j.state<>'done' OR EXISTS (SELECT 1 FROM attempts a WHERE a.job_id=j.id AND a.finished_at>j.deadline)))) "
        "ORDER BY j.seq DESC LIMIT %(limit)s",
        dict(
            fence=fence,
            last=last,
            owner=owner,
            q=q,
            state=state,
            reason=reason,
            resource=resource,
            filter_by=filter_by,
            since=since,
            t=t,
            limit=limit + 1,
        ),
    ).fetchall()
    return _page(rows, fence, limit, "seq")


def decisions(
    conn, client, *, owner=None, job=None, action=None, reason=None, deferred=False, cursor=None, limit=30
):
    high = conn.execute("SELECT coalesce(max(id),0) AS n FROM decisions").fetchone()["n"]
    fence, last = _cursor(cursor, high)
    rows = conn.execute(
        "SELECT d.*, j.client_id FROM decisions d JOIN jobs j ON j.id=d.job_id "
        "WHERE d.id<=%(fence)s AND d.id<%(last)s AND (%(owner)s::text IS NULL OR j.client_id=%(owner)s) "
        "AND (%(job)s::text IS NULL OR d.job_id=%(job)s) AND (%(action)s::text IS NULL OR d.action=%(action)s) "
        "AND (%(reason)s::text IS NULL OR d.reason=%(reason)s) "
        "AND (NOT %(deferred)s OR d.action IN ('defer','close')) ORDER BY d.id DESC LIMIT %(limit)s",
        dict(
            fence=fence,
            last=last,
            owner=_client(client, owner),
            job=job,
            action=action,
            reason=reason,
            deferred=deferred,
            limit=limit + 1,
        ),
    ).fetchall()
    return _page(rows, fence, limit, "id")


def incidents(conn, client, *, since, cursor=None, limit=30, owner=None, reason=None):
    high = conn.execute("SELECT coalesce(max(id),0) AS n FROM decisions").fetchone()["n"]
    fence, last = _cursor(cursor, high)
    rows = conn.execute(
        "SELECT d.reason,j.client_id,d.resource,count(*) AS rounds,count(DISTINCT d.job_id) AS jobs, "
        "min(d.t) AS opened,max(d.t) AS last_seen,max(d.id) AS id, "
        "bool_or(j.state='active' AND s.state IN ('waiting','ready','unknown') AND s.last_reason=d.reason) AS open, "
        "CASE WHEN NOT bool_or(j.state='active' AND s.state IN ('waiting','ready','unknown') AND s.last_reason=d.reason) "
        "THEN max((SELECT max(a.finished_at) FROM attempts a WHERE a.job_id=d.job_id AND a.step_id=d.step_id "
        "AND a.finished_at>=d.t)) END AS resolved_at "
        "FROM decisions d JOIN jobs j ON j.id=d.job_id JOIN steps s ON s.job_id=d.job_id AND s.step_id=d.step_id "
        "WHERE d.id<=%(fence)s AND d.t>=%(since)s AND d.action IN ('defer','close') "
        "AND (%(owner)s::text IS NULL OR j.client_id=%(owner)s) AND (%(reason)s::text IS NULL OR d.reason=%(reason)s) "
        "GROUP BY d.reason,j.client_id,d.resource HAVING max(d.id)<%(last)s ORDER BY max(d.id) DESC LIMIT %(limit)s",
        dict(
            fence=fence, last=last, since=since, limit=limit + 1, owner=_client(client, owner), reason=reason
        ),
    ).fetchall()
    return _page(rows, fence, limit, "id")


def historical_durations(conn, cls, models, t):
    return {
        row["model"]: row["durations"]
        for row in conn.execute(
            "SELECT model,array_agg(duration) AS durations FROM ("
            "SELECT s.model,a.finished_at-a.started_at AS duration,"
            "row_number() OVER (PARTITION BY s.model ORDER BY a.finished_at DESC,a.turn_seq DESC) AS rn "
            "FROM attempts a JOIN jobs j ON j.id=a.job_id JOIN steps s ON s.job_id=a.job_id AND s.step_id=a.step_id "
            "WHERE j.cls=%s AND (s.model=ANY(%s::text[]) OR s.model IS NULL) "
            "AND a.state IN ('finished','reconciled') AND a.finished_at<=%s AND a.started_at IS NOT NULL "
            "AND a.finished_at>=a.started_at) history WHERE rn<=100 GROUP BY model",
            (cls, [m for m in models if m is not None], t),
        ).fetchall()
    }


def job_risk(now, job, steps, starts, executor=True, history=None):
    if not job.get("deadline") or job["state"] != "active":
        return None
    pending = {}
    for s in steps:
        if s["state"] in ("done", "closed", "failed"):
            continue
        sid = s["step_id"]
        active = s["state"] == "running" and sid in starts
        elapsed = now - starts[sid] if active else 0
        reason = s["last_reason"] if s["state"] == "ready" else None
        review = s["next_reconsider"] if s["next_reconsider"] and s["next_reconsider"] > now else None
        if s["state"] == "unknown":
            reason, review = "reconciliation_pending", None
        elif not executor and s["state"] == "ready":
            reason, review = "client_unavailable", None
        duration, count = estimate_duration(s["duration_estimate"], (history or {}).get(s["model"], []))
        pending[sid] = Estimate(tuple(s["depends_on"]), max(0, duration - elapsed), review, reason, count)
    return forecast(now, job["deadline"], pending) if pending else None


def risk_queue(conn, t, limit=25):
    queued = conn.execute(
        "SELECT j.*,c.available_until,c.slots FROM jobs j JOIN clients c ON c.id=j.client_id "
        "WHERE j.urgent AND j.state='active' AND j.deadline IS NOT NULL ORDER BY j.deadline,j.seq LIMIT %s",
        (limit,),
    ).fetchall()
    results = []
    for job in queued:
        steps = conn.execute("SELECT * FROM steps WHERE job_id=%s", (job["id"],)).fetchall()
        started = conn.execute(
            "SELECT step_id,started_at FROM attempts WHERE job_id=%s AND state='started'", (job["id"],)
        ).fetchall()
        starts = {a["step_id"]: a["started_at"] for a in started}
        available = (
            job["available_until"] is not None and job["available_until"] >= t and job["slots"] > len(starts)
        )
        history = historical_durations(conn, job["cls"], {s["model"] for s in steps}, t)
        risk = job_risk(t, job, steps, starts, available, history)
        if risk:
            results.append({"job": job["id"], "client": job["client_id"], **risk})
    return results


def digest(conn, client, seconds):
    t = store.now(conn)
    since = t - seconds
    missed = conn.execute(
        "SELECT count(*) AS n FROM jobs j WHERE j.urgent AND j.deadline BETWEEN %s AND %s AND "
        "(j.state<>'done' OR EXISTS (SELECT 1 FROM attempts a WHERE a.job_id=j.id AND a.finished_at>j.deadline))",
        (since, t),
    ).fetchone()["n"]
    main = conn.execute(
        "SELECT d.reason,count(DISTINCT job_id) AS jobs,count(*) AS rounds FROM decisions d "
        "WHERE d.t >= %s AND d.action IN ('defer','close') GROUP BY d.reason ORDER BY jobs DESC,d.reason LIMIT 1",
        (since,),
    ).fetchone()
    revisions = conn.execute(
        "SELECT id AS revision,based_on,reason,created_at FROM config_versions "
        "WHERE kind='policy' AND created_at >= %s ORDER BY id",
        (since,),
    ).fetchall()
    unknown = conn.execute("SELECT count(*) AS n FROM attempts WHERE state='unknown'").fetchone()["n"]
    observed = conn.execute(
        "SELECT coalesce(sum(extract(epoch FROM least(completed_at,to_timestamp(%s)) - "
        "greatest(started_at,to_timestamp(%s)))),0)::bigint AS n FROM scheduler_windows "
        "WHERE started_at<=to_timestamp(%s) AND completed_at>=to_timestamp(%s)",
        (t, since, t, since),
    ).fetchone()["n"]
    monetary = spend(conn, client)["items"]
    return {
        "since": since,
        "as_of": t,
        "missed_urgent_jobs": missed,
        "top_issue": main,
        "unknown_attempts": unknown,
        "policy_changes": revisions,
        "coverage": {"observed_s": observed, "requested_s": seconds, "partial": observed < seconds},
        "uncertain_cost_by_currency": {
            code: sum(r["uncertain"] for r in monetary if r["currency"] == code)
            for code in {r["currency"] for r in monetary}
        },
        "risk": [r for r in risk_queue(conn, t) if r["status"] in ("risk", "unknown")],
    }


def create_checkpoint(conn, record_seconds=None):
    store.lock(conn)
    if record_seconds is not None and (active := recording.active_recording(conn)):
        raise store.ApiError(
            409, "recording_active", "a capture is already running", {"checkpoint": active["id"]}
        )
    t = store.now(conn)
    specs, capacity = store._snapshot(conn, t)
    policy, revision = store.load_policy(conn)
    active = conn.execute(
        "SELECT id,client_id,cls,urgent,priority,arrival,deadline,seq FROM jobs "
        "WHERE state='active' ORDER BY seq LIMIT 1001"
    ).fetchall()
    if len(active) > 1000:
        raise store.ApiError(422, "checkpoint_size", "the queue exceeds the 1000-job checkpoint limit")
    ids = [r["id"] for r in active]
    clients = conn.execute(
        "SELECT id, slots, available_until, mode FROM clients WHERE role='client' ORDER BY id"
    ).fetchall()
    steps = conn.execute(
        "SELECT * FROM steps WHERE job_id=ANY(%s) ORDER BY job_id, step_id", (ids,)
    ).fetchall()
    attempts = conn.execute(
        "SELECT id,job_id,step_id,n,client_id,state,admitted,granted_at,grant_expires_at,started_at,delivered,tariff_quote "
        "FROM attempts WHERE state IN ('granted','started','unknown') ORDER BY turn_seq"
    ).fetchall()
    tariff = store.active_tariff(conn)
    body = {
        "schema": "rubato.incident/v1" if record_seconds is not None else "rubato.checkpoint/v1",
        "resources": [vars(s) for s in specs],
        "ledger": [vars(entry) for entries in capacity.ledger.entries.values() for entry in entries],
        "pauses": [
            {"scope": kind, "value": key, "until": end} for (kind, key), end in capacity.pauses.items()
        ],
        "jobs": active,
        "steps": steps,
        "clients": clients,
        "attempts": attempts,
        "policy": {"revision": revision, "config": policy.config()},
        "fair_turn": store._fair_turn(conn, revision, policy.weights) if policy.name == "fair" else None,
        "tariff": tariff.config() if tariff else None,
        "limitations": ["provider outcomes and later events can be incomplete"],
    }
    encoded = json.dumps(body)
    if len(encoded) > 8_000_000:
        raise store.ApiError(422, "checkpoint_size", "the checkpoint exceeds eight million characters")
    return conn.execute(
        "INSERT INTO replay_checkpoints (captured_at,through_event,through_decision,through_config,body,record_until) "
        "SELECT %s,coalesce((SELECT max(id) FROM events),0),coalesce((SELECT max(id) FROM decisions),0), "
        "coalesce((SELECT max(id) FROM config_versions),0),%s, "
        "clock_timestamp() + make_interval(secs => %s) RETURNING id,captured_at,record_until",
        (t, encoded, record_seconds),
    ).fetchone()


def stop_checkpoint(conn, checkpoint):
    store.lock(conn)
    row = conn.execute(
        "UPDATE replay_checkpoints SET stopped_at=coalesce(stopped_at,least(record_until,clock_timestamp())), "
        "stop_reason=coalesce(stop_reason,CASE WHEN record_until <= clock_timestamp() THEN 'elapsed' ELSE 'manual' END) "
        "WHERE id=%s AND record_until IS NOT NULL RETURNING id,stopped_at,stop_reason",
        (checkpoint,),
    ).fetchone()
    if row is None:
        raise store.ApiError(404, "not_found", "incident recording was not found")
    return row


def recent_checkpoints(conn, include):
    return conn.execute(
        "WITH wanted(id) AS ("
        "(SELECT id FROM replay_checkpoints ORDER BY id DESC LIMIT 12) UNION "
        "(SELECT id FROM replay_checkpoints WHERE stopped_at IS NULL AND record_until > transaction_timestamp() "
        "ORDER BY id DESC LIMIT 1) UNION SELECT %s::bigint) "
        "SELECT c.id,c.captured_at,c.record_until,c.stopped_at,c.round_count, "
        "CASE WHEN c.record_until IS NULL THEN 'snapshot' "
        "WHEN c.stopped_at IS NOT NULL THEN c.stop_reason "
        "WHEN c.record_until > transaction_timestamp() THEN 'recording' ELSE 'elapsed' END AS state "
        "FROM wanted JOIN replay_checkpoints c USING (id) ORDER BY c.id DESC",
        (include,),
    ).fetchall()


def read_checkpoint(conn, checkpoint):
    row = conn.execute(
        "SELECT *,least(record_until,stopped_at,transaction_timestamp()) AS cutoff, "
        "record_until > transaction_timestamp() AND stopped_at IS NULL AS recording "
        "FROM replay_checkpoints WHERE id=%s",
        (checkpoint,),
    ).fetchone()
    if not row:
        raise store.ApiError(404, "not_found", "checkpoint was not found")
    cutoff, ongoing = row.pop("cutoff"), row.pop("recording")
    bounded = row["record_until"] is not None
    until = " AND recorded_at <= %s" if bounded else ""
    event_args = (row["through_event"], cutoff) if bounded else (row["through_event"],)
    decision_args = (row["through_decision"], cutoff) if bounded else (row["through_decision"],)
    events = conn.execute(
        "SELECT id,t,kind,client_id,job_id,step_id,attempt_id,"
        "jsonb_strip_nulls(jsonb_build_object('status',payload->'status','usage',payload->'usage',"
        "'consumed',payload->'consumed','admitted',payload->'admitted')) AS outcome "
        "FROM events WHERE id>%s" + until + " ORDER BY id LIMIT 1001",
        event_args,
    ).fetchall()
    decisions = conn.execute(
        "SELECT * FROM decisions WHERE id>%s" + until + " ORDER BY id LIMIT 1001", decision_args
    ).fetchall()
    config_filter = "id > %s AND recorded_at <= %s" if bounded else "created_at >= %s"
    config_args = (row["through_config"], cutoff) if bounded else (row["captured_at"],)
    configs = conn.execute(
        "SELECT id,kind,body,created_at FROM config_versions WHERE "
        + config_filter
        + " ORDER BY id LIMIT 1001",
        config_args,
    ).fetchall()
    rounds = (
        conn.execute(
            "SELECT ordinal,t,body FROM replay_rounds WHERE checkpoint_id=%s ORDER BY ordinal", (checkpoint,)
        ).fetchall()
        if bounded
        else []
    )
    truncated = any(len(items) > 1000 for items in (events, decisions, configs))
    limited = row["stop_reason"] == "capacity_limit"
    status = "recording" if ongoing else "partial" if not bounded or truncated or limited else "complete"
    return {
        **row,
        "coverage": {
            "initial_state": "captured",
            "subsequent_events": "recorded rounds and audit trail" if bounded else "audit trail only",
            "status": status,
            "truncated": truncated or limited,
            "through": cutoff if bounded else None,
            "rounds": len(rounds),
        },
        "rounds": rounds,
        "events": events[:1000],
        "decisions": decisions[:1000],
        "configuration": configs[:1000],
    }


def preview_checkpoint(checkpoint: dict, candidate: dict, job_id: str | None) -> dict:
    """Replay the first round at capture time. Later provider outcomes have no role here."""
    body = checkpoint["body"]
    t = checkpoint["captured_at"]
    ledger = Ledger([ResourceSpec(**r) for r in body["resources"]])
    for entry in body["ledger"]:
        ledger.add(Entry(**entry))
    tariff = Tariff.from_config(body["tariff"]) if body["tariff"] else None
    executors = {
        c["id"]: c["slots"]
        for c in body["clients"]
        if c["available_until"] is not None and c["available_until"] >= t
    }
    running = {}
    for a in body["attempts"]:
        if a["state"] in {"granted", "started"} and a["client_id"] in executors:
            executors[a["client_id"]] = max(0, executors[a["client_id"]] - 1)
        if a["state"] == "started":
            running[a["job_id"], a["step_id"]] = a["started_at"]
    pauses = {(r["scope"], r["value"]): r["until"] for r in body["pauses"]}
    jobs_by_id = {j["id"]: j for j in body["jobs"]}
    active = []
    for step in body["steps"]:
        job = jobs_by_id[step["job_id"]]
        meta = {k: job[k] for k in ("client_id", "cls", "urgent", "priority", "arrival", "deadline", "seq")}
        active.append({**step, **meta, "started_at": running.get((job["id"], step["step_id"]))})
    rows = sorted(active, key=lambda step: (step["seq"], step["step_id"]))
    ready, demand, _ = store._planning_rows(rows, t, tariff, executors)

    def compare(config, turn):
        p = make_policy(config["name"], **{k: v for k, v in config.items() if k != "name"})
        snapshot = Capacity(ledger, pauses, executors, tuple(turn) if turn else None)
        decisions = decidir(t, ready, snapshot, p, demand)
        return {
            "label": p.label,
            "authorized": sum(d.action == "authorize" for d in decisions),
            "decisions": [d.to_dict() for d in decisions if job_id is None or d.job_id == job_id],
        }

    if job_id is not None and not any(j["id"] == job_id for j in body["jobs"]):
        raise store.ApiError(404, "not_in_checkpoint", "this job was not active when the state was captured")
    reference = body["policy"]["config"]
    return {
        "at": t,
        "coverage": "one decision round at checkpoint time",
        "reference": compare(reference, body["fair_turn"]),
        "candidate": compare(
            candidate, body["fair_turn"] if reference.get("resource") == candidate.get("resource") else None
        ),
    }


def spend(conn, client, *, owner=None, model=None, cls=None, since=None):
    rows = conn.execute(
        "WITH charges AS ("
        "SELECT j.client_id,coalesce(a.tariff_quote->>'currency','unknown') AS currency,"
        "coalesce(sum((a.usage->>'cost_micro')::numeric) FILTER (WHERE a.state IN ('finished','reconciled')),0) AS confirmed,"
        "coalesce(sum((a.admitted->>'cost_micro')::numeric) FILTER (WHERE a.state IN ('granted','started')),0) AS reserved,"
        "coalesce(sum((a.admitted->>'cost_micro')::numeric) FILTER (WHERE a.state='unknown' OR "
        "(a.state IN ('finished','reconciled') AND a.cost_source IN ('partial','unknown'))),0) AS uncertain,"
        "count(*) FILTER (WHERE a.state IN ('finished','reconciled') AND a.cost_source IN ('partial','unknown')) AS unknown_attempts,"
        "count(DISTINCT j.id) FILTER (WHERE j.state='done') AS completed_jobs "
        "FROM attempts a JOIN jobs j ON j.id=a.job_id JOIN steps s ON s.job_id=a.job_id AND s.step_id=a.step_id "
        "WHERE (%(owner)s::text IS NULL OR j.client_id=%(owner)s) AND (%(model)s::text IS NULL OR s.model=%(model)s) "
        "AND (%(cls)s::text IS NULL OR j.cls=%(cls)s) AND (%(since)s::bigint IS NULL OR a.granted_at>=%(since)s) "
        "AND a.state NOT IN ('released','expired') AND (a.tariff_quote IS NOT NULL OR a.usage ? 'cost_micro') "
        "GROUP BY j.client_id,currency) "
        "SELECT *,round(confirmed/nullif(completed_jobs,0)) AS cost_per_completed_job_micro "
        "FROM charges ORDER BY currency,client_id",
        dict(owner=_client(client, owner), model=model, cls=cls, since=since),
    ).fetchall()
    return {
        "unit": "cost_micro",
        "basis": "attempt admission window; distinct completed jobs in the selected cohort",
        "items": [{k: int(v) if hasattr(v, "as_tuple") else v for k, v in row.items()} for row in rows],
    }
