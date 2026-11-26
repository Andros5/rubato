"""One synchronous or asynchronous call through the local simulated provider."""

from __future__ import annotations

import argparse
import asyncio
import os
import uuid

import httpx

from rubato.sdk import AsyncGate, Gate, Outcome, RubatoClient

API = "http://127.0.0.1:18700"
SIMULATOR = "http://127.0.0.1:18710"


def metadata():
    return {
        "id": f"python-example-{uuid.uuid4().hex[:8]}",
        "class": "example",
        "steps": [{"id": "summary", "model": "sim-flash", "estimate": {"requests": 1}}],
    }


def call_body(key):
    return {"key": key, "model": "sim-flash", "tokens_in": 120, "max_tokens_out": 30}


def outcome(receipt):
    return receipt, Outcome(
        status="ok" if receipt["valid"] else "invalid", usage=receipt["usage"], receipt=receipt["receipt"]
    )


def sync_example(token):
    client = RubatoClient(API, token)
    with client.http, httpx.Client(base_url=SIMULATOR) as provider:
        gate, job = Gate(client, "enforce"), metadata()
        gate.register(job)

        def call():
            return outcome(provider.post("/v1/generate", json=call_body(job["id"])).raise_for_status().json())

        return gate.run(job["id"], "summary", call)


async def async_example(token):
    async with (
        httpx.AsyncClient(base_url=API, headers={"Authorization": f"Bearer {token}"}) as api,
        httpx.AsyncClient(base_url=SIMULATOR) as provider,
    ):
        gate, job = AsyncGate(api, "enforce"), metadata()
        await gate.register(job)

        async def call(attempt):
            response = await provider.post("/v1/generate", json=call_body(attempt))
            return outcome(response.raise_for_status().json())

        return await gate.run(job["id"], "summary", call)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--async", dest="asynchronous", action="store_true")
    args = parser.parse_args()
    token = os.environ.get("RUBATO_DEMO_CLIENT_TOKEN", "demo-radar-local")
    result = asyncio.run(async_example(token)) if args.asynchronous else sync_example(token)
    print(f"{'Completed' if result.ran else 'Waiting'} · {result.attempt or result.reason}")
    return 0 if result.ran else 1


if __name__ == "__main__":
    raise SystemExit(main())
