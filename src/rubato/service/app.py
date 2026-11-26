"""HTTP API (FastAPI). Run: ``uvicorn rubato.service.app:app``."""

from __future__ import annotations

import json
import logging
import os
import threading
import time
import uuid
from contextlib import asynccontextmanager

import psycopg
from fastapi import Body, FastAPI, Header, Query, Request
from fastapi.responses import HTMLResponse, JSONResponse, PlainTextResponse
from psycopg.rows import dict_row
from psycopg_pool import ConnectionPool

from . import db, investigate, recording, scheduler, store
from .panel import PANEL_HTML
from .store import ApiError

log = logging.getLogger("rubato.api")
POOL: ConnectionPool | None = None


def _json_log(level: int, msg: str, **fields) -> None:
    log.log(level, json.dumps({"msg": msg, **fields}, default=str))


def _embedded_planner(stop: threading.Event) -> None:
    """The planner thread must outlive unexpected errors: as a separate process, the
    container would restart it; inside the API nobody would."""
    while not stop.is_set():
        try:
            scheduler.run(stop)
        except Exception as e:
            _json_log(logging.ERROR, "embedded scheduler failed, restarting", error=repr(e)[:200])
            stop.wait(5)


@asynccontextmanager
async def lifespan(app: FastAPI):
    global POOL
    logging.basicConfig(level=os.environ.get("RUBATO_LOG_LEVEL", "INFO"), format="%(message)s")
    POOL = ConnectionPool(
        db.dsn(),
        kwargs={"row_factory": dict_row},
        min_size=1,
        max_size=int(os.environ.get("RUBATO_POOL", "10")),
        open=True,
    )
    if os.environ.get("RUBATO_MIGRATE", "1") == "1":
        with POOL.connection() as conn:
            applied = db.migrate(conn)
            if os.environ.get("RUBATO_CLIENTS"):
                with conn.transaction():
                    store.bootstrap_clients(conn, os.environ["RUBATO_CLIENTS"])
            _json_log(logging.INFO, "startup", migrations=applied)
            weak = store.example_token_clients(conn)
            conn.commit()
            if weak:
                _json_log(
                    logging.WARNING,
                    "example tokens in use; set RUBATO_CLIENTS before exposing the service",
                    clients=weak,
                )
    planner = None
    if os.environ.get("RUBATO_EMBEDDED_SCHEDULER") == "1":  # one process instead of api + scheduler
        stop = threading.Event()
        planner = threading.Thread(
            target=_embedded_planner, args=(stop,), name="rubato-scheduler", daemon=True
        )
        planner.start()
    yield
    if planner:
        stop.set()
        planner.join(timeout=10)
    POOL.close()


app = FastAPI(title="Rubato", version="1.0", lifespan=lifespan)


@app.exception_handler(ApiError)
async def api_error(request: Request, exc: ApiError):
    return JSONResponse({"error": exc.code, "message": exc.message, **exc.extra}, status_code=exc.status)


@app.middleware("http")
async def correlation(request: Request, call_next):
    corr = request.headers.get("X-Correlation-Id") or str(uuid.uuid4())
    t0 = time.perf_counter()
    resp = await call_next(request)
    resp.headers["X-Correlation-Id"] = corr
    _json_log(
        logging.INFO,
        "request",
        corr=corr,
        method=request.method,
        path=request.url.path,
        status=resp.status_code,
        ms=round((time.perf_counter() - t0) * 1000, 2),
    )
    from . import ops

    ops.observe_request(request.url.path, request.method, resp.status_code, time.perf_counter() - t0)
    return resp


def tx(fn, *args, retries: int = 3):
    """Run ``fn(conn, *args)`` in one transaction; retry on deadlock/serialization failures."""
    assert POOL is not None
    for k in range(retries):
        try:
            with POOL.connection() as conn:
                with conn.transaction():
                    return fn(conn, *args)
        except (psycopg.errors.DeadlockDetected, psycopg.errors.SerializationFailure):
            if k == retries - 1:
                raise
            time.sleep(0.05 * (k + 1))


def _client(conn, authorization: str | None) -> dict:
    token = authorization.split(" ", 1)[1] if authorization and " " in authorization else authorization
    return store.authenticate(conn, token)


def _job_id(client: dict, jid: str) -> str:
    """Client ids may contain ':' or '/'. Only an id already carrying the caller's own
    prefix (or any prefixed id, for the administrator) is taken as a full id."""
    if jid.startswith(f"{client['id']}:"):
        return jid
    if client["role"] == "admin" and ":" in jid:
        return jid
    return f"{client['id']}:{jid}"


def _idempotent(op: str, key: str | None, body, auth, fn, status_ok: int = 200):
    def run(conn):
        client = _client(conn, auth)
        hit = store.idem_lookup(conn, client["id"], key, op, body)
        if hit:
            return hit
        try:
            res = fn(conn, client)
            status = status_ok
        except ApiError as e:
            if e.status >= 500:
                raise
            res, status = {"error": e.code, "message": e.message, **e.extra}, e.status
        store.idem_store(conn, client["id"], key, op, body, status, res)
        return status, res

    status, res = tx(run)
    return JSONResponse(res, status_code=status)


# ---------------------------------------------------------------------------- health


@app.get("/healthz")
def healthz():
    return {"ok": True}


@app.get("/readyz")
def readyz():
    try:
        assert POOL is not None
        with POOL.connection() as conn:
            conn.execute("SELECT 1")
            v = db.schema_version(conn)
            planner = conn.execute(
                "SELECT extract(epoch FROM completed_at)::float8 AS completed_at, interval_s, "
                "clock_timestamp() - completed_at <= make_interval(secs => greatest(5, 3 * interval_s)) AS recent "
                "FROM scheduler_status"
            ).fetchone()
        return {
            "ok": True,
            "schema": v,
            "environment": os.environ.get("RUBATO_ENVIRONMENT", "habitual"),
            "scheduler": planner,
        }
    except Exception as e:  # pragma: no cover - exercised in ops tests
        return JSONResponse({"ok": False, "error": str(e)[:200]}, status_code=503)


@app.get("/metrics")
def metrics():
    from . import ops

    assert POOL is not None
    with POOL.connection() as conn:
        text = ops.render_metrics(conn)
    return PlainTextResponse(text, media_type="text/plain; version=0.0.4")


# ---------------------------------------------------------------------------- jobs


@app.post("/v1/jobs", status_code=201)
def post_job(
    body: dict = Body(...),
    authorization: str | None = Header(None),
    idempotency_key: str | None = Header(None),
):
    return _idempotent(
        "jobs",
        idempotency_key or f"job:{body.get('id')}",
        body,
        authorization,
        lambda conn, c: store.register_job(conn, c, body),
        status_ok=201,
    )


@app.get("/v1/jobs/{jid:path}")
def get_job(jid: str, authorization: str | None = Header(None)):
    def run(conn):
        c = _client(conn, authorization)
        return store.get_job(conn, c, _job_id(c, jid))

    return tx(run)


@app.post("/v1/jobs/{jid:path}/cancel")
def cancel(jid: str, authorization: str | None = Header(None)):
    def run(conn):
        c = _client(conn, authorization)
        return store.cancel_job(conn, c, _job_id(c, jid))

    return tx(run)


# ---------------------------------------------------------------------------- claims & attempts


@app.post("/v1/claims")
def post_claim(
    body: dict = Body(default={}),
    authorization: str | None = Header(None),
    idempotency_key: str | None = Header(None, min_length=1, max_length=128),
):
    def run(conn):
        c = _client(conn, authorization)
        if idempotency_key is not None:
            # The same decision lock covers lookup and delivery, even across API replicas.
            store.lock(conn)
            hit = store.idem_lookup(conn, c["id"], idempotency_key, "claim", body)
            if hit:
                return hit[1]
        job = _job_id(c, body["job"]) if body.get("job") else None
        result = store.claim(conn, c, body.get("slots"), job, body.get("step"), bool(body.get("shadow")))
        store.idem_store(conn, c["id"], idempotency_key, "claim", body, 200, result)
        return result

    return tx(run)


@app.post("/v1/observations")
def post_observation(body: dict = Body(...), authorization: str | None = Header(None)):
    def run(conn):
        c = _client(conn, authorization)
        return store.observe(conn, c, {**body, "job": _job_id(c, body["job"])})

    return tx(run)


@app.post("/v1/attempts/{aid}/start")
def post_start(aid: str, authorization: str | None = Header(None)):
    def run(conn):
        return store.start(conn, _client(conn, authorization), aid)

    return tx(run)


@app.post("/v1/attempts/{aid}/finish")
def post_finish(aid: str, body: dict = Body(...), authorization: str | None = Header(None)):
    def run(conn):
        return store.finish(conn, _client(conn, authorization), aid, body)

    return tx(run)


@app.post("/v1/attempts/{aid}/reconcile")
def post_reconcile(aid: str, body: dict = Body(...), authorization: str | None = Header(None)):
    def run(conn):
        return store.reconcile(conn, _client(conn, authorization), aid, body)

    return tx(run)


@app.get("/v1/decisions")
def get_decisions(job: str | None = None, limit: int = 200, authorization: str | None = Header(None)):
    def run(conn):
        c = _client(conn, authorization)
        return store.decisions(conn, c, _job_id(c, job) if job else None, limit)

    return tx(run)


@app.get("/v1/usage")
def get_usage(authorization: str | None = Header(None)):
    return tx(lambda conn: store.usage(conn, _client(conn, authorization)))


# ---------------------------------------------------------------------------- admin


def _admin(conn, authorization):
    c = _client(conn, authorization)
    store.require_admin(c)
    return c


@app.put("/v1/admin/resources")
def put_resources(body: list = Body(...), authorization: str | None = Header(None)):
    def run(conn):
        c = _admin(conn, authorization)
        return [vars(r) for r in store.put_resources(conn, body, c["id"])]

    return tx(run)


@app.put("/v1/admin/policy")
def put_policy(body: dict = Body(...), authorization: str | None = Header(None)):
    def run(conn):
        c = _admin(conn, authorization)
        try:
            return store.set_policy(conn, body, c["id"])
        except (TypeError, ValueError) as e:
            raise ApiError(422, "invalid", str(e)) from e

    return tx(run)


@app.put("/v1/admin/tariff")
def put_tariff(body: dict = Body(...), authorization: str | None = Header(None)):
    def run(conn):
        c = _admin(conn, authorization)
        try:
            return store.save_tariff(conn, body, c["id"])
        except (KeyError, TypeError, ValueError) as e:
            raise ApiError(422, "invalid", str(e)) from e

    return tx(run)


@app.get("/v1/admin/policy-drafts")
def drafts(authorization: str | None = Header(None)):
    def run(conn):
        _admin(conn, authorization)
        return conn.execute("SELECT * FROM policy_drafts ORDER BY id DESC LIMIT 100").fetchall()

    return tx(run)


@app.post("/v1/admin/policy-drafts", status_code=201)
def create_draft(body: dict = Body(...), authorization: str | None = Header(None)):
    return tx(lambda conn: store.new_policy_draft(conn, body, _admin(conn, authorization)["id"]))


@app.post("/v1/admin/policy-drafts/{draft}/preview")
def preview_draft(draft: int, body: dict = Body(...), authorization: str | None = Header(None)):
    from ..metrics import summarize
    from ..policies import make_policy
    from ..sim import Simulator
    from ..trace import loads

    def inputs(conn):
        _admin(conn, authorization)
        row = store.policy_draft(conn, draft)
        return row, store.policy_revision(conn, row["base_revision"]), store.active_tariff(conn)

    row, previous, tariff = tx(inputs)
    text = body.get("trace")
    if not isinstance(text, str) or len(text) > 1_000_000:
        raise ApiError(422, "invalid_trace", "provide a rubato.trace/v1 text of up to one million characters")
    try:
        tr = loads(text)
        if not tr.jobs or len(tr.jobs) > 1000:
            raise ValueError("preview expects 1–1000 jobs")
        horizon = max(j.arrival for j in tr.jobs) + 86400

        def replay(config):
            args = dict(config)
            p = make_policy(args.pop("name"), **args)
            return summarize(
                Simulator(
                    tr.jobs, tr.resources, p, tr.events, horizon, max_iterations=10_000, tariff=tariff
                ).run()
            )

        return {
            "draft": draft,
            "base_revision": row["base_revision"],
            "trace": tr.fingerprint(),
            "jobs": len(tr.jobs),
            "arrival_provenance": sorted({j.arrival_provenance for j in tr.jobs}),
            "assumptions": tr.header.get("assumptions", []),
            "horizon": horizon,
            "before": replay(previous),
            "after": replay(row["body"]),
        }
    except (ValueError, TypeError, RuntimeError) as exc:
        raise ApiError(422, "invalid_trace", str(exc)) from exc


@app.get("/v1/admin/policy-history")
def policy_history(authorization: str | None = Header(None)):
    def run(conn):
        _admin(conn, authorization)
        _, revision = store.load_policy(conn)
        return {
            "active_revision": revision or 0,
            "revisions": conn.execute(
                "SELECT * FROM config_versions WHERE kind='policy' ORDER BY id DESC LIMIT 100"
            ).fetchall(),
        }

    return tx(run)


def _change_revision(target, body, if_match, authorization, idempotency_key, revert=False):
    def run(conn):
        c = _admin(conn, authorization)
        store.lock(conn)
        op = f"policy:{'revert' if revert else 'activate'}:{target}"
        request = {"body": body, "if_match": if_match}
        hit = store.idem_lookup(conn, c["id"], idempotency_key, op, request)
        if hit:
            return hit[1]
        result = store.activate_revision(conn, c, if_match, body.get("reason"), target, revert=revert)
        store.idem_store(conn, c["id"], idempotency_key, op, request, 200, result)
        return result

    return tx(run)


@app.post("/v1/admin/policy-drafts/{draft}/activate")
def activate_draft(
    draft: int,
    body: dict = Body(...),
    if_match: str | None = Header(None),
    authorization: str | None = Header(None),
    idempotency_key: str | None = Header(None),
):
    return _change_revision(draft, body, if_match, authorization, idempotency_key)


@app.post("/v1/admin/policy-history/{revision}/revert")
def revert_policy(
    revision: int,
    body: dict = Body(...),
    if_match: str | None = Header(None),
    authorization: str | None = Header(None),
    idempotency_key: str | None = Header(None),
):
    return _change_revision(revision, body, if_match, authorization, idempotency_key, revert=True)


@app.post("/v1/admin/clients")
def post_client(body: dict = Body(...), authorization: str | None = Header(None)):
    def run(conn):
        _admin(conn, authorization)
        token = body.get("token") or store.new_token()
        store.bootstrap_clients(
            conn, f"{body['id']}:{token}:{body.get('role', 'client')}:{body.get('slots', 1)}"
        )
        if body.get("models") is not None:
            conn.execute("UPDATE clients SET models=%s WHERE id=%s", (json.dumps(body["models"]), body["id"]))
        return {"id": body["id"], "token": token}

    return tx(run)


@app.post("/v1/admin/schedule")
def post_schedule(authorization: str | None = Header(None)):
    def run(conn):
        _admin(conn, authorization)
        return {"grants": store.schedule_pass(conn)}

    return tx(run)


@app.post("/v1/admin/clock")
def post_clock(body: dict = Body(...), authorization: str | None = Header(None)):
    def run(conn):
        _admin(conn, authorization)
        return {"now": store.advance_clock(conn, int(body.get("advance", 0)))}

    return tx(run)


@app.post("/v1/admin/reset")
def post_reset(authorization: str | None = Header(None)):
    """Test-only: wipe work and configuration (enabled only with RUBATO_TEST_CLOCK=1)."""

    def run(conn):
        _admin(conn, authorization)
        if os.environ.get("RUBATO_TEST_CLOCK") != "1":
            raise ApiError(403, "disabled", "reset is only available in test mode")
        store.lock(conn)
        conn.execute(
            "TRUNCATE ledger, attempts, decisions, events, idempotency, pauses, steps, jobs, config_versions, "
            "policy_drafts, replay_rounds, replay_checkpoints"
        )
        conn.execute("DELETE FROM resources")
        conn.execute(
            "UPDATE clients SET available_until=NULL, slots=CASE WHEN role='admin' THEN 0 ELSE 1 END, "
            "models='[]', mode='off', mode_changed_at=NULL, last_claim_at=NULL, last_slot_claim_at=NULL"
        )
        conn.execute("UPDATE test_clock SET offset_s=0")
        return {"ok": True}

    return tx(run)


@app.get("/v1/mode")
def get_mode(authorization: str | None = Header(None)):
    """The coordination mode the administrator chose for the calling client."""
    return tx(lambda conn: store.get_mode(conn, _client(conn, authorization)))


@app.put("/v1/admin/clients/{cid}/mode")
def put_mode(cid: str, body: dict = Body(...), authorization: str | None = Header(None)):
    def run(conn):
        return store.set_mode(conn, _admin(conn, authorization), cid, body.get("mode"))

    return tx(run)


@app.get("/v1/admin/overview")
def get_overview(authorization: str | None = Header(None)):
    def run(conn):
        _admin(conn, authorization)
        return store.overview(conn)

    return tx(run)


@app.get("/v1/investigation/jobs")
def investigation_jobs(
    owner: str | None = None,
    q: str | None = Query(None, max_length=256),
    state: str | None = None,
    reason: str | None = None,
    resource: str | None = None,
    filter_by: str | None = None,
    since: int | None = None,
    cursor: str | None = Query(None, max_length=48),
    limit: int = Query(30, ge=1, le=100),
    authorization: str | None = Header(None),
):
    return tx(
        lambda conn: investigate.jobs(
            conn,
            _client(conn, authorization),
            owner=owner,
            q=q,
            state=state,
            reason=reason,
            resource=resource,
            filter_by=filter_by,
            since=since,
            cursor=cursor,
            limit=limit,
        )
    )


@app.get("/v1/investigation/decisions")
def investigation_decisions(
    owner: str | None = None,
    job: str | None = None,
    action: str | None = None,
    reason: str | None = None,
    deferred: bool = False,
    cursor: str | None = Query(None, max_length=48),
    limit: int = Query(30, ge=1, le=100),
    authorization: str | None = Header(None),
):
    return tx(
        lambda conn: investigate.decisions(
            conn,
            _client(conn, authorization),
            owner=owner,
            job=job,
            action=action,
            reason=reason,
            deferred=deferred,
            cursor=cursor,
            limit=limit,
        )
    )


@app.get("/v1/investigation/incidents")
def investigation_incidents(
    owner: str | None = None,
    reason: str | None = None,
    seconds: int = Query(86400, ge=1, le=2_678_400),
    cursor: str | None = Query(None, max_length=48),
    limit: int = Query(30, ge=1, le=100),
    authorization: str | None = Header(None),
):
    return tx(
        lambda conn: investigate.incidents(
            conn,
            _client(conn, authorization),
            since=store.now(conn) - seconds,
            cursor=cursor,
            limit=limit,
            owner=owner,
            reason=reason,
        )
    )


@app.get("/v1/admin/digest")
def daily_digest(seconds: int = Query(86400, ge=1, le=2_678_400), authorization: str | None = Header(None)):
    return tx(lambda conn: investigate.digest(conn, _admin(conn, authorization), seconds))


@app.get("/v1/admin/replay-checkpoints")
def get_replay_checkpoints(
    include: int | None = Query(None, ge=1, le=2**63 - 1), authorization: str | None = Header(None)
):
    def run(conn):
        _admin(conn, authorization)
        return {"items": investigate.recent_checkpoints(conn, include)}

    return tx(run)


@app.post("/v1/admin/replay-checkpoints", status_code=201)
def create_replay_checkpoint(body: dict | None = Body(None), authorization: str | None = Header(None)):
    def run(conn):
        _admin(conn, authorization)
        seconds = (body or {}).get("record_seconds")
        if seconds is not None:
            store._int(seconds, "'record_seconds'", 1, 900)
        return investigate.create_checkpoint(conn, seconds)

    return tx(run)


def _read_checkpoint(checkpoint, authorization):
    def run(conn):
        conn.execute("SET TRANSACTION ISOLATION LEVEL REPEATABLE READ READ ONLY")
        _admin(conn, authorization)
        return investigate.read_checkpoint(conn, checkpoint)

    return tx(run)


@app.get("/v1/admin/replay-checkpoints/{checkpoint}")
def get_replay_checkpoint(checkpoint: int, authorization: str | None = Header(None)):
    return _read_checkpoint(checkpoint, authorization)


@app.post("/v1/admin/replay-checkpoints/{checkpoint}/stop")
def stop_replay_checkpoint(checkpoint: int, authorization: str | None = Header(None)):
    def run(conn):
        _admin(conn, authorization)
        return investigate.stop_checkpoint(conn, checkpoint)

    return tx(run)


@app.post("/v1/admin/replay-checkpoints/{checkpoint}/replay")
def replay_incident(checkpoint: int, body: dict = Body(...), authorization: str | None = Header(None)):
    capture = _read_checkpoint(checkpoint, authorization)
    candidate = store.checked_policy(body["policy"]) if "policy" in body else None
    return recording.replay_capture(capture, candidate, _replay_job(body))


def _replay_job(body):
    job = body.get("job")
    if job is not None and (not isinstance(job, str) or len(job) > 256):
        raise ApiError(422, "invalid_job", "job must be a job identifier")
    return job


@app.post("/v1/admin/replay-checkpoints/{checkpoint}/preview")
def preview_replay_checkpoint(
    checkpoint: int, body: dict = Body(...), authorization: str | None = Header(None)
):
    def read(conn):
        _admin(conn, authorization)
        return conn.execute("SELECT * FROM replay_checkpoints WHERE id=%s", (checkpoint,)).fetchone()

    row = tx(read)
    if row is None:
        raise ApiError(404, "not_found", "checkpoint was not found")
    return investigate.preview_checkpoint(row, store.checked_policy(body.get("policy")), _replay_job(body))


@app.get("/v1/consumption")
def consumption(
    owner: str | None = None,
    model: str | None = None,
    cls: str | None = None,
    since: int | None = None,
    authorization: str | None = Header(None),
):
    return tx(
        lambda conn: investigate.spend(
            conn,
            _client(conn, authorization),
            owner=owner,
            model=model,
            cls=cls,
            since=since,
        )
    )


@app.get("/panel", response_class=HTMLResponse)
def panel():
    """Live panel (static page; it asks for the admin token and calls the API)."""
    return HTMLResponse(PANEL_HTML)


@app.get("/v1/admin/attempts")
def get_attempts(state: str = "unknown", authorization: str | None = Header(None)):
    def run(conn):
        _admin(conn, authorization)
        return conn.execute(
            "SELECT id, job_id, step_id, n, client_id, state, admitted, started_at FROM attempts "
            "WHERE state=%s ORDER BY started_at",
            (state,),
        ).fetchall()

    return tx(run)
