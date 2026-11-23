"""Smoke: the real radar adapter (branch rubato-adapter) against the running Rubato service.
Google is mocked; Rubato is real (Compose). Run from the radar worktree root."""

import json
import os
import sqlite3
import sys
import tempfile
from pathlib import Path

import httpx

os.environ.update(
    RADAR_AI_PROVIDER="gemini",
    RADAR_GEMINI_FREE_CONFIRMED="1",
    RADAR_GEMINI_MIN_INTERVAL="0",
    RUBATO_URL="http://127.0.0.1:8000",
    RUBATO_TOKEN="radar-local-token",
)
import radar.google_ai as g  # noqa: E402
from radar import rubato_adapter  # noqa: E402
from radar.google_ai import CHAIN, GoogleAI  # noqa: E402

g.keyring.get_password = lambda *_: "test-key"
admin = httpx.Client(base_url="http://127.0.0.1:8000", headers={"Authorization": "Bearer admin-local-token"})
admin.post("/v1/admin/reset").raise_for_status()
admin.put(
    "/v1/admin/resources", json=[{"id": "quota", "unit": "requests", "capacity": 3, "window": "total"}]
).raise_for_status()
admin.put("/v1/admin/policy", json={"name": "reserva", "reserve": {"quota": 2}}).raise_for_status()
tmp = Path(tempfile.mkdtemp())
path = tmp / "google-ai.db"
called = []


def handle(request):
    if request.method == "GET":
        return httpx.Response(
            200,
            json={
                "models": [
                    {"name": "models/" + m, "supportedGenerationMethods": ["generateContent"]}
                    for m in set(CHAIN)
                    | {
                        "gemini-3.8-flash",
                        "gemini-3.6-flash",
                        "gemini-3.5-flash-lite",
                        "gemini-3.1-flash-lite",
                    }
                ]
            },
        )
    called.append(1)
    return httpx.Response(
        200,
        json={
            "candidates": [
                {"finishReason": "STOP", "content": {"parts": [{"text": json.dumps({"ok": True})}]}}
            ],
            "usageMetadata": {"promptTokenCount": 900, "candidatesTokenCount": 40},
        },
    )


def invoke(task):
    return GoogleAI(path, transport=httpx.MockTransport(handle)).invoke(
        task, {"x": 1}, {"type": "object"}, lambda _: None, "Only JSON", force=True
    )


out = {}
for mode in ("shadow", "enforce"):
    os.environ["RADAR_RUBATO_MODE"] = mode
    os.environ["RADAR_RUBATO_SHADOW_LOG"] = str(tmp / "shadow.jsonl")
    rubato_adapter.set_client(None)
    res = []
    for task in ("match", "match", "cv"):
        try:
            invoke(task)
            res.append((task, "ran"))
        except Exception as e:  # noqa: BLE001
            res.append((task, f"blocked: {str(e)[:90]}"))
    out[mode] = res
    if mode == "shadow":
        out["shadow_log"] = [json.loads(x)["rubato"] for x in (tmp / "shadow.jsonl").read_text().splitlines()]
        admin.post("/v1/admin/reset").raise_for_status()
        admin.put(
            "/v1/admin/resources",
            json=[{"id": "quota", "unit": "requests", "capacity": 3, "window": "total"}],
        ).raise_for_status()
        admin.put("/v1/admin/policy", json={"name": "reserva", "reserve": {"quota": 2}}).raise_for_status()
with sqlite3.connect(path) as db:
    out["local_calls"] = [r[0] for r in db.execute("select status from calls order by id")]
out["rubato_usage"] = [r for r in admin.get("/v1/usage").json()["resources"]]
out["provider_calls"] = len(called)
print(json.dumps(out, indent=1, ensure_ascii=False))
sys.exit(0)
