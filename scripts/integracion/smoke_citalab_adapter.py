"""Smoke: the real CitaLab adapter (branch rubato-adapter) against the running Rubato service."""
import json
import os
from dataclasses import replace

import httpx

os.environ.update(RUBATO_URL="http://127.0.0.1:8000", RUBATO_TOKEN="citalab-local-token")
from citalab.config import ProviderConfig, Settings  # noqa: E402
from citalab.llm import rubato  # noqa: E402
from citalab.llm.client import ChatClient, LLMError, RequestGate  # noqa: E402

admin = httpx.Client(base_url="http://127.0.0.1:8000", headers={"Authorization": "Bearer admin-local-token"})
OK = {"choices": [{"message": {"content": "{}"}}], "usage": {"prompt_tokens": 120, "completion_tokens": 30}}
rubato.install()
out = {}
for mode in ("shadow", "enforce"):
    admin.post("/v1/admin/reset").raise_for_status()
    admin.put("/v1/admin/resources", json=[{"id": "quota", "unit": "requests", "capacity": 3, "window": "total"}]).raise_for_status()
    admin.put("/v1/admin/policy", json={"name": "reserva", "reserve": {"quota": 2}}).raise_for_status()
    os.environ["CITALAB_RUBATO_MODE"] = mode
    rubato.set_client(None)
    gate = RequestGate()
    providers = {"google": ProviderConfig("google", "https://test.invalid", "k", "gemini-test")}
    cfg = Settings(providers=providers, simulator=replace(providers["google"], model="sim"), max_attempts=1)
    sent = []
    res = []
    for k in range(2):
        c = ChatClient(providers["google"], cfg, sleep=lambda _: None, gate=gate)
        c._http.close()
        c._http = httpx.Client(transport=httpx.MockTransport(lambda r: (sent.append(1), httpx.Response(200, json=OK))[1]))
        try:
            with rubato.episode(f"google:H:fam-{k}:primary", "smoke"):
                c._post("patient", {}, {}, "s", None, "v1")
            res.append("ran")
        except LLMError as e:
            res.append(f"blocked: {e}"[:80])
    usage = admin.get("/v1/usage").json()["resources"][0]
    out[mode] = {"calls": res, "provider_calls": len(sent), "gate_pause": gate.pause_code,
                 "rubato_used": usage["used"]}
print(json.dumps(out, indent=1, ensure_ascii=False))
