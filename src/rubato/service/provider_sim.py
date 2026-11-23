"""Simulated AI provider with receipts. No network calls, no keys, no money.

POST /v1/generate {key, model, tokens_in, max_tokens_out} -> usage + receipt id.
Repeating a key returns the original receipt (provider-side idempotency).
GET  /v1/receipts/{key} -> the receipt, or 404 if the provider never processed it.
POST /v1/control {rate_limit_next: n, retry_after: s, latency_ms: x, fail_next: n}

Receipts live in a SQLite file (RUBATO_PROVIDER_DB) so they survive restarts.
"""

from __future__ import annotations

import hashlib
import json
import os
import sqlite3
import threading
import time

from fastapi import Body, FastAPI
from fastapi.responses import JSONResponse

app = FastAPI(title="Rubato simulated provider")
_lock = threading.Lock()
_ctl = {
    "rate_limit_next": 0,
    "retry_after": 5,
    "latency_ms": int(os.environ.get("PROVIDER_LATENCY_MS", "50")),
    "fail_next": 0,
}


def _db() -> sqlite3.Connection:
    con = sqlite3.connect(os.environ.get("RUBATO_PROVIDER_DB", "provider.sqlite"))
    con.execute("CREATE TABLE IF NOT EXISTS receipts (key TEXT PRIMARY KEY, body TEXT, t REAL)")
    return con


@app.get("/healthz")
def healthz():
    return {"ok": True}


@app.post("/v1/control")
def control(body: dict = Body(...)):
    with _lock:
        _ctl.update({k: int(v) for k, v in body.items() if k in _ctl})
        return dict(_ctl)


@app.post("/v1/generate")
def generate(body: dict = Body(...)):
    key = body["key"]
    with _lock, _db() as con:
        r = con.execute("SELECT body FROM receipts WHERE key=?", (key,)).fetchone()
        if r:
            return json.loads(r[0])
        if _ctl["rate_limit_next"] > 0:
            _ctl["rate_limit_next"] -= 1
            return JSONResponse(
                {"error": "rate_limited"}, status_code=429, headers={"Retry-After": str(_ctl["retry_after"])}
            )
        if _ctl["fail_next"] > 0:
            _ctl["fail_next"] -= 1
            return JSONResponse({"error": "unavailable"}, status_code=503)
        h = int(hashlib.sha256(key.encode()).hexdigest()[:8], 16)
        max_out = int(body.get("max_tokens_out", 1000))
        usage = {
            "requests": 1,
            "tokens_in": int(body.get("tokens_in", 0)),
            "tokens_out": max_out // 2 + h % (max_out // 2 + 1),
        }
        receipt = {
            "receipt": f"rcpt-{hashlib.sha256(key.encode()).hexdigest()[:16]}",
            "key": key,
            "model": body.get("model"),
            "usage": usage,
            "valid": not body.get("force_invalid", False),
        }
        time.sleep(_ctl["latency_ms"] / 1000)
        con.execute(
            "INSERT INTO receipts (key, body, t) VALUES (?,?,?)", (key, json.dumps(receipt), time.time())
        )
        return receipt


@app.get("/v1/receipts/{key}")
def receipt(key: str):
    with _db() as con:
        r = con.execute("SELECT body FROM receipts WHERE key=?", (key,)).fetchone()
    if not r:
        return JSONResponse({"error": "not_found"}, status_code=404)
    return json.loads(r[0])


@app.get("/v1/receipts")
def receipts():
    with _db() as con:
        rows = con.execute("SELECT body FROM receipts ORDER BY t").fetchall()
    return [json.loads(r[0]) for r in rows]
