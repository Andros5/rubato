import asyncio
import json
from pathlib import Path
from zipfile import ZipFile

import httpx
import pytest

from rubato.bundle import bundle, verify
from rubato.drill import verify_effects
from rubato.explore import explore
from rubato.importers.csv import convert
from rubato.model import Job, Step
from rubato.policies import FairPolicy, RadarPolicy
from rubato.resources import ResourceSpec
from rubato.risk import Estimate, forecast
from rubato.runs import read_run, run_scenario
from rubato.sdk import AsyncGate
from rubato.sdk import Outcome as SDKOutcome
from rubato.shadow import shadow_report
from rubato.sim import RuleChange, Simulator, simulate
from rubato.tariffs import load_tariff

EXAMPLES = Path(__file__).resolve().parents[1] / "examples"


def test_explore_bundle_and_private_rows(tmp_path):
    trace, scenarios, policies = (EXAMPLES / p for p in ("demo.jsonl", "scenarios.json", "policies.json"))
    base, dest = tmp_path / "base", tmp_path / "alternatives"
    run_scenario(trace, "cuota-reducida", scenarios, policies, ["radar", "reserva"], base, grid=False)
    spec = json.loads((EXAMPLES / "entrevista" / "reservas.json").read_text())
    checks = iter((False, False, True))
    assert not explore(base, spec, dest, cancelled=lambda: next(checks))["complete"]
    result = explore(base, spec, dest)
    assert result["recommended"] == "reserva-reserve-2"
    assert result["alternatives"]["reserva-reserve-8"]["failures"][0]["actual"] > 300
    assert result["alternatives"]["reserva-reserve-2"]["affected"]
    out = tmp_path / "demo.zip"
    assert bundle(dest, out)["kind"] == "synthetic"
    verify(out, replay=True)
    tampered = tmp_path / "tampered.zip"
    with ZipFile(out) as original, ZipFile(tampered, "w") as changed:
        for name in original.namelist():
            data = original.read(name)
            changed.writestr(name, data + b"%" if name == "informe.html" else data)
    with pytest.raises(ValueError, match="informe.html"):
        verify(tampered)
    source = tmp_path / "sensitive-user-path.jsonl"
    source.write_text(
        trace.read_text(encoding="utf-8").replace('"radar"', '"sensitive-client"'), encoding="utf-8"
    )
    run_scenario(source, "sin-urgencia", scenarios, policies, ["radar"], tmp_path / "private")
    assert bundle(tmp_path / "private", out)["kind"] == "aggregate"
    verify(out)
    with ZipFile(out) as packed:
        assert sorted(packed.namelist()) == ["bundle.json", "resumen.json"]
        assert b"sensitive" not in packed.read("resumen.json")


def test_rule_change_keeps_started_attempts_and_prefix():
    jobs = [Job("old", "a", "test", 0, (Step("s", {"requests": 1}, duration_estimate=15),))]
    jobs.append(Job("later", "b", "test", 11, (Step("s", {"requests": 2}),)))
    resources = [ResourceSpec("q", "requests", 2)]
    baseline = simulate(jobs, resources, RadarPolicy(), horizon=20)
    alt = Simulator(
        jobs, resources, RadarPolicy(), horizon=20, change=RuleChange(10, capacities={"q": 3})
    ).run()
    assert [d.to_dict() for d in baseline.decisions if d.t < 10] == [
        d.to_dict() for d in alt.decisions if d.t < 10
    ]
    assert alt.attempts[0] == baseline.attempts[0]
    assert alt.by_id()["later"].status == "done"
    assert baseline.by_id()["later"].status == "pending"


def test_fair_turns_and_single_client_work_conserving():
    def run(counts):
        jobs = [
            Job(f"{client}-{i}", client, "test", 0, (Step("s", {"requests": 1}),))
            for client, n in counts.items()
            for i in range(n)
        ]
        result = simulate(
            jobs,
            [ResourceSpec("rpm", "requests", 1, "rolling", 1)],
            FairPolicy(resource="rpm", weights={"a": 2}),
            horizon=60,
        )
        return [a.job_id.split("-")[0] for a in result.attempts]

    assert run({"a": 60, "b": 60}) == ["a", "a", "b"] * 20
    assert run({"a": 60}) == ["a"] * 60


def test_fair_demotes_impossible_urgency_into_the_client_rotation():
    jobs = [
        Job("a1", "a", "test", 0, (Step("s", {"requests": 1}),)),
        Job("a2", "a", "test", 0, (Step("s", {"requests": 1}),)),
        Job("late", "b", "test", 0, (Step("s", {"requests": 1}),), urgent=True, deadline=0),
    ]
    result = simulate(
        jobs,
        [ResourceSpec("rpm", "requests", 1, "rolling", 1)],
        FairPolicy(resource="rpm", weights={"a": 2}, impossible="background"),
    )
    assert [attempt.job_id for attempt in result.attempts] == ["a1", "a2", "late"]
    decision = next(d for d in result.decisions if d.job_id == "late" and d.action == "authorize")
    assert decision.value["turn"]["client"] == "b"


def test_forecast_distinguishes_work_path_and_missing_executor():
    result = forecast(
        10,
        19,
        {
            "a": Estimate((), 4),
            "b": Estimate((), 3, 17, "quota"),
            "c": Estimate(("a", "b"), 2),
        },
    )
    assert (result["work_s"], result["critical_path_s"], result["earliest_finish"]) == (9, 6, 22)
    assert result["next_reconsider"] == 17 and result["status"] == "risk"
    assert forecast(10, 20, {"a": Estimate((), 4, None, "client_unavailable")})["status"] == "unknown"


def test_csv_dst_atomicity_and_tariff(tmp_path):
    files = EXAMPLES / "entrevista"
    source = files / "intentos.csv"
    output = tmp_path / "trace.jsonl"
    first = convert(source, files / "mapa.json", output)
    before = output.read_bytes()
    assert convert(source, files / "mapa.json", output)["fingerprint"] == first["fingerprint"]
    ambiguous = tmp_path / "bad.csv"
    ambiguous.write_text(source.read_text().replace("2026-11-24T09", "2026-10-25T02"), encoding="utf-8")
    with pytest.raises(ValueError, match=r"fila 2, arrival"):
        convert(ambiguous, files / "mapa.json", output)
    assert output.read_bytes() == before
    tariff = load_tariff(files / "tarifa.json")
    assert tariff.cost_micro("sim-flash", {"tokens_in": 2000, "tokens_out": 600}) == 5500
    assert tariff.cost_micro("sim-flash", {"tokens_in": 0, "tokens_out": 0}) == 0
    assert tariff.cost_micro("sim-flash", {"tokens_in": 2000}) is None
    assert tariff.cost_micro("sim-flash", {}) is None
    run = tmp_path / "run"
    run_scenario(
        output,
        "sin-urgencia",
        EXAMPLES / "scenarios.json",
        EXAMPLES / "policies.json",
        ["radar"],
        run,
        grid=False,
        tariff_path=files / "tarifa.json",
    )
    data = read_run(run)["runs"]["radar"]
    assert data["summary"]["consumption"]["cost_by_currency_and_client"]["EUR"]["radar"]["confirmed"] == 8750


def test_csv_semicolon_deduplicates_exact_rows(tmp_path):
    source = tmp_path / "trace.csv"
    row = "a;j;s;a1;2026-11-24T09:00:00+01:00"
    source.write_text("client;job;step;attempt;start\n" + row + "\n" + row + "\n", encoding="utf-8")
    mapping = tmp_path / "map.json"
    mapping.write_text(
        json.dumps(
            {
                "delimiter": ";",
                "timezone": "Europe/Madrid",
                "arrival_from_start": True,
                "columns": {k: k for k in ("client", "job", "step", "attempt", "start")},
            }
        ),
        encoding="utf-8",
    )
    stats = convert(source, mapping, tmp_path / "trace.jsonl")
    assert stats["duplicates"] == 1 and stats["coverage"]["tokens_in"]["unknown"] == 1


def test_shadow_minimums_and_accounting_discrepancy(tmp_path):
    file = tmp_path / "shadow.jsonl"
    rows = [
        {"t": day * 86400, "latency_ms": 12_000, "rubato": {"action": "defer", "reason": "rate_limited"}}
        for day in range(3)
    ]
    file.write_text("\n".join(map(json.dumps, rows)), encoding="utf-8")
    report = shadow_report(file)
    assert report["ready_for_enforce"] is True and report["trial_verdict"]["ready"] is False
    assert "latency_exceeds_limit" in report["trial_verdict"]["reasons"]
    assert verify_effects(["a"], [{"key": "a", "usage": {"requests": 1}}], 2, 0)["balance_discrepancy"] == 1


def test_async_wait_and_cancel_after_provider_started():
    async def check():
        reached = asyncio.Event()
        hits = []

        def handle(r):
            if r.url.path == "/v1/claims":
                body = json.loads(r.content)
                return httpx.Response(
                    200,
                    json=(
                        {"permit": None, "retry_after": 100}
                        if body["job"] == "wait"
                        else {"permit": {"attempt": "one"}}
                    ),
                )
            hits.append(r.url.path)
            return httpx.Response(200, json={"ok": True})

        async with httpx.AsyncClient(base_url="http://test", transport=httpx.MockTransport(handle)) as c:
            gate = AsyncGate(c, "enforce")

            async def provider(key):
                assert key == "one"
                reached.set()
                await asyncio.Event().wait()

            waiting = asyncio.create_task(gate.run("wait", "s", provider))
            launched = asyncio.create_task(gate.run("go", "s", provider))
            await asyncio.wait_for(reached.wait(), timeout=1)
            for task in (waiting, launched):
                task.cancel()
                with pytest.raises(asyncio.CancelledError):
                    await task
            assert gate.pending == {"one": None}
            await gate.reconcile(
                "one", consumed=True, outcome=SDKOutcome(usage={"requests": 1}, receipt="rcpt")
            )
            assert not gate.pending and hits == ["/v1/attempts/one/start", "/v1/attempts/one/reconcile"]

    asyncio.run(check())
