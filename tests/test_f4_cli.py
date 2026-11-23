"""F4: CLI end to end on the distributed synthetic example, report and evaluation protocol."""

import json
import re
from dataclasses import replace
from pathlib import Path

from rubato.cli import main
from rubato.evaluate import evaluate
from rubato.synth import SynthConfig, generate
from rubato.trace import Trace, dump, dumps, load, resource_to_dict

ROOT = Path(__file__).resolve().parents[1]
EX = ROOT / "examples"


def sim(out, scenario="urgencia-tardia", policies="radar,reserva"):
    assert (
        main(
            [
                "simulate",
                "--trace",
                str(EX / "demo.jsonl"),
                "--scenario",
                scenario,
                "--policies",
                policies,
                "--scenarios",
                str(EX / "scenarios.json"),
                "--policies-file",
                str(EX / "policies.json"),
                "--out",
                str(out),
                "--no-grid",
            ]
        )
        == 0
    )
    return {p.name: json.loads((p / "summary.json").read_text()) for p in Path(out).iterdir() if p.is_dir()}


def test_distributed_demo_trace_is_reproducible_from_the_generator():
    assert (EX / "demo.jsonl").read_text(encoding="utf-8") == dumps(
        generate(SynthConfig(seed=7, urgent_applications=0))
    )


def test_simulate_compare_and_explain_end_to_end(tmp_path, capsys):
    s = sim(tmp_path / "demo")
    assert (s["radar"]["urgent"]["done_on_time"], s["reserva"]["urgent"]["done_on_time"]) == (0, 1)
    assert (
        s["radar"]["background"]["done"] > s["reserva"]["background"]["done"]
    )  # the reserve costs background
    report = tmp_path / "informe.html"
    assert main(["compare", "--run", str(tmp_path / "demo"), "--report", str(report)]) == 0
    html = report.read_text(encoding="utf-8")
    assert not re.search(r"https?://|<script[^>]+src=|<link[^>]+href=", html)  # self-contained, offline
    for needle in ("candidatura-urgente", "deadline_reserve", "hypothetical", "Cuota disponible"):
        assert needle in html
    manifest = json.loads((tmp_path / "demo" / "manifest.json").read_text())
    assert manifest["trace"]["fingerprint"] == load(EX / "demo.jsonl").fingerprint()
    assert any("hipotética" in a for a in manifest["assumptions"])
    capsys.readouterr()
    assert (
        main(
            ["explain", "--run", str(tmp_path / "demo"), "--job", "candidatura-urgente", "--policy", "radar"]
        )
        == 0
    )
    out = capsys.readouterr().out
    assert "DEFER [capacity_exhausted] rpd: needs 1, 0 available" in out


def test_same_inputs_same_outputs(tmp_path):
    sim(tmp_path / "a")
    sim(tmp_path / "b")
    for f in ("decisions.jsonl", "jobs.jsonl", "attempts.jsonl", "usage.jsonl"):
        assert (tmp_path / "a" / "reserva" / f).read_bytes() == (tmp_path / "b" / "reserva" / f).read_bytes()


def test_day_without_urgency_shows_the_reserve_does_not_win(tmp_path):
    s = sim(tmp_path / "s", scenario="sin-urgencia")
    assert s["reserva"]["urgent"]["received"] == 0
    assert s["reserva"]["background"]["done"] <= s["radar"]["background"]["done"]


def test_evaluation_protocol_chooses_on_tuning_and_judges_on_test(tmp_path):
    days = []
    for k, d0 in enumerate((1795651200, 1795737600, 1795824000)):  # 24, 25, 26 Nov 2026 (UTC midnight)
        tr = generate(SynthConfig(seed=20 + k, day_start=d0, urgent_applications=1, urgent_at_hour=9.3))
        days += [replace(j, id=f"d{k}-{j.id}") for j in tr.jobs]
    trace = tmp_path / "t.jsonl"
    dump(Trace({"type": "header", "schema": "rubato.trace/v1", "time_unit": "s"}, [], days), trace)
    prof = tmp_path / "p.json"
    resources = [{k: v for k, v in resource_to_dict(r).items() if k != "type"} for r in generate().resources]
    prof.write_text(json.dumps({"version": "rubato.profile/v1", "id": "synthetic", "resources": resources}))
    pilot = {"min_urgent_gain": 1, "max_background_p95_extra_s": 1800}
    blocks = {
        "design": ("2026-11-24", "2026-11-24"),
        "tuning": ("2026-11-25", "2026-11-25"),
        "test": ("2026-11-26", "2026-11-26"),
    }
    r = evaluate(trace, prof, EX / "policies.json", blocks, 45, tz="UTC", pilot=pilot)
    assert set(r["blocks"]["tuning"]["runs"]) >= {"radar", "reserva", "reserva-rpd0", "reserva-rpd8"}
    assert r["chosen"].startswith("reserva")
    assert set(r["blocks"]["test"]["runs"]) == {"radar", r["chosen"]}
    v = r["verdict"]
    assert v["default_policy"] == (r["chosen"] if v["candidate_wins"] else "radar")
    assert all(d["urgent_received"] == 1 for d in r["blocks"]["test"]["runs"]["radar"]["per_day"].values())
