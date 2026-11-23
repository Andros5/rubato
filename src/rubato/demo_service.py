"""Two clients share one quota through the running service (simulated provider, synthetic load).

Radar analyses and a CitaLab evaluation lot compete for the same quota; a late urgent
application (CV + letter) arrives once the background work has used what it could.
The run is repeated under the reserve policy and the reference policy, and finally a
client switches to ``off`` and keeps working through its usual path.

Everything here is synthetic and the provider is the local simulator: no keys, no cost.
"""

from __future__ import annotations

from collections import Counter

import httpx

from .sdk import Gate, Outcome, RubatoClient

QUOTA = 24
RESERVE = 2


def _admin(api: str, token: str) -> httpx.Client:
    return httpx.Client(base_url=api, headers={"Authorization": f"Bearer {token}"}, timeout=30)


def _provider_call(provider: str, key: str, model: str = "sim-flash"):
    def call():
        r = httpx.post(
            f"{provider}/v1/generate",
            json={"key": key, "model": model, "tokens_in": 800, "max_tokens_out": 200},
            timeout=30,
        )
        body = r.json()
        if r.status_code == 429:
            return body, Outcome(
                "rate_limited", {"requests": 1}, retry_after=int(r.headers.get("Retry-After", "5"))
            )
        return body, Outcome("ok", body["usage"], body["receipt"])

    return call


def _background(radar: RubatoClient, citalab: RubatoClient, run: str) -> list[tuple[RubatoClient, str, str]]:
    work = []
    for k in range(12):
        jid = f"{run}-match-{k:02d}"
        radar.register_job(
            {
                "id": jid,
                "class": "match",
                "steps": [
                    {
                        "id": "match",
                        "model": "sim-flash",
                        "estimate": {"requests": 1},
                        "maximum": {"requests": 1},
                    }
                ],
            }
        )
        work.append((radar, jid, "match"))
    for e in range(6):
        jid = f"{run}-eval-{e:02d}"
        citalab.register_job(
            {
                "id": jid,
                "class": "eval",
                "steps": [
                    {
                        "id": "answer",
                        "model": "sim-flash",
                        "estimate": {"requests": 1},
                        "maximum": {"requests": 1},
                    },
                    {
                        "id": "judge",
                        "model": "sim-flash",
                        "estimate": {"requests": 1},
                        "maximum": {"requests": 1},
                        "depends_on": ["answer"],
                    },
                ],
            }
        )
        work += [(citalab, jid, "answer"), (citalab, jid, "judge")]
    return work


def _drain(work, provider: str, gates: dict) -> Counter:
    """Round-robin until no step obtains a permit in a full pass."""
    done: Counter = Counter()
    pending = list(work)
    progress = True
    while pending and progress:
        progress = False
        rest = []
        for cl, jid, step in pending:
            g = gates[id(cl)]
            res = g.run(jid, step, _provider_call(provider, f"{jid}/{step}"))
            if res.ran:
                done["radar" if "match" in jid else "citalab"] += 1
                progress = True
            else:
                rest.append((cl, jid, step))
        pending = rest
    return done


def run_policy(api: str, provider: str, tokens: dict, policy: dict, run: str) -> dict:
    with _admin(api, tokens["admin"]) as a:
        a.post("/v1/admin/reset").raise_for_status()
        a.put(
            "/v1/admin/resources",
            json=[{"id": "quota", "unit": "requests", "capacity": QUOTA, "window": "total"}],
        ).raise_for_status()
        a.put("/v1/admin/policy", json=policy).raise_for_status()
    radar = RubatoClient(api, tokens["radar"])
    citalab = RubatoClient(api, tokens["citalab"])
    gates = {
        id(radar): Gate(radar, "enforce", wait_timeout=0),
        id(citalab): Gate(citalab, "enforce", wait_timeout=0),
    }
    work = _background(radar, citalab, run)
    done = _drain(work, provider, gates)
    # the urgent application arrives after the background lot has used what it could
    urgent = f"{run}-candidatura"
    radar.register_job(
        {
            "id": urgent,
            "class": "application",
            "urgent": True,
            "priority": 10,
            "steps": [
                {"id": "cv", "model": "sim-flash", "estimate": {"requests": 1}, "maximum": {"requests": 1}},
                {
                    "id": "letter",
                    "model": "sim-flash",
                    "estimate": {"requests": 1},
                    "maximum": {"requests": 1},
                    "depends_on": ["cv"],
                },
            ],
        }
    )
    urgent_steps = []
    for step in ("cv", "letter"):
        res = gates[id(radar)].run(urgent, step, _provider_call(provider, f"{urgent}/{step}"))
        urgent_steps.append({"step": step, "ran": res.ran, "reason": res.reason})
    reasons = Counter(d["reason"] for d in radar.decisions() + citalab.decisions() if d["action"] == "defer")
    eval_jobs = [citalab.job(f"{run}-eval-{e:02d}")["state"] for e in range(6)]
    usage = radar.usage()
    return {
        "policy": policy["name"],
        "background_requests_done": dict(done),
        "background_requests_total": 24,
        "citalab_episodes_done": eval_jobs.count("done"),
        "citalab_episodes_pending": eval_jobs.count("active"),
        "urgent": urgent_steps,
        "urgent_done": all(s["ran"] for s in urgent_steps),
        "deferrals_by_reason": dict(reasons),
        "quota": next(r for r in usage["resources"] if r["id"] == "quota"),
    }


def run_off(api: str, provider: str, tokens: dict, run: str) -> dict:
    """The radar switches to off: later work goes straight to the provider."""
    with _admin(api, tokens["admin"]) as a:
        before_jobs = len(a.get("/v1/decisions", params={"limit": 10000}).json())
    receipts_before = len(httpx.get(f"{provider}/v1/receipts").json())
    gate = Gate(RubatoClient(api, tokens["radar"]), "off")
    ran = sum(
        gate.run(f"{run}-later-{k}", "match", _provider_call(provider, f"{run}-later-{k}")).ran
        for k in range(3)
    )
    receipts_after = len(httpx.get(f"{provider}/v1/receipts").json())
    with _admin(api, tokens["admin"]) as a:
        after_jobs = len(a.get("/v1/decisions", params={"limit": 10000}).json())
    return {
        "mode": "off",
        "ran": ran,
        "provider_calls": receipts_after - receipts_before,
        "new_rubato_decisions": after_jobs - before_jobs,
    }


def run_demo(api: str, provider: str, tokens: dict, tag: str) -> dict:
    reserve = run_policy(
        api, provider, tokens, {"name": "reserva", "reserve": {"quota": RESERVE}}, f"{tag}-r"
    )
    reference = run_policy(api, provider, tokens, {"name": "radar"}, f"{tag}-b")
    off = run_off(api, provider, tokens, tag)
    return {
        "load": "synthetic (12 radar analyses + 6 CitaLab episodes x 2 steps), simulated provider",
        "quota": QUOTA,
        "reserva": reserve,
        "radar": reference,
        "lot_delay": {
            "citalab_episodes_pending_with_reserve": reserve["citalab_episodes_pending"],
            "citalab_episodes_pending_with_reference": reference["citalab_episodes_pending"],
            "background_requests_given_up_for_the_urgent_job": sum(
                reference["background_requests_done"].values()
            )
            - sum(reserve["background_requests_done"].values()),
        },
        "off": off,
    }
