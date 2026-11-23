"""Helpers for integration tests against a running Rubato stack (Compose or Kubernetes).

Set RUBATO_TEST_API (e.g. http://127.0.0.1:8000). Tokens default to the Compose ones.
"""

from __future__ import annotations

import os

import httpx
import pytest

API = os.environ.get("RUBATO_TEST_API")
PROVIDER = os.environ.get("RUBATO_TEST_PROVIDER", "http://127.0.0.1:8100")
TOKENS = {
    "admin": os.environ.get("RUBATO_TEST_ADMIN_TOKEN", "admin-local-token"),
    "radar": os.environ.get("RUBATO_TEST_RADAR_TOKEN", "radar-local-token"),
    "citalab": os.environ.get("RUBATO_TEST_CITALAB_TOKEN", "citalab-local-token"),
}

needs_service = pytest.mark.skipif(not API, reason="set RUBATO_TEST_API to a running Rubato stack")


def client(who: str) -> httpx.Client:
    assert API
    return httpx.Client(base_url=API, headers={"Authorization": f"Bearer {TOKENS[who]}"}, timeout=30)


def ok(r: httpx.Response, status: int | tuple = (200, 201)) -> dict:
    st = (status,) if isinstance(status, int) else status
    assert r.status_code in st, f"{r.status_code}: {r.text}"
    return r.json()


def reset(resources: list[dict], policy: dict | None = None) -> None:
    with client("admin") as a:
        ok(a.post("/v1/admin/reset"))
        ok(a.put("/v1/admin/resources", json=resources))
        if policy:
            ok(a.put("/v1/admin/policy", json=policy))


def advance(seconds: int) -> int:
    with client("admin") as a:
        return ok(a.post("/v1/admin/clock", json={"advance": seconds}))["now"]


def job(jid: str, steps=None, **kw) -> dict:
    return {
        "id": jid,
        "class": kw.pop("cls", "match"),
        "steps": steps or [{"id": "s", "estimate": {"requests": 1}, "model": "sim-flash"}],
        **kw,
    }
