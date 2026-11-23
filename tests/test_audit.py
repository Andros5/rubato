"""Audit (2026-11-23): regressions for the issues found while reviewing the code."""

import json
import random

import pytest

from rubato.model import Job, Provenance, Step, find_cycle
from rubato.policies import FifoPolicy
from rubato.report import TEMPLATE
from rubato.resources import NEVER, Ledger, ResourceSpec
from rubato.service.ops import _esc
from rubato.service.store import ApiError, _validate_job
from rubato.sim import Simulator
from rubato.trace import SCHEMA, TraceError, loads

# --- the indexed ledger behaves exactly like the naive scan it replaced ---------------------------


class NaiveLedger:
    """The pre-audit implementation: scans every entry and converts every timestamp."""

    def __init__(self, specs):
        self.specs = {s.id: s for s in specs}
        self.entries = {s.id: [] for s in specs}

    def book(self, rid, t, amount, urgent):
        self.entries[rid].append([t, amount, urgent, "reserved"])

    def live(self, rid, t):
        spec = self.specs[rid]
        es = [e for e in self.entries[rid] if e[3] != "released" and e[0] <= t]
        if spec.window == "rolling":
            return [e for e in es if e[0] > t - spec.length]
        if spec.window == "calendar":
            start = spec.window_start(t)
            return [e for e in es if spec.window_start(e[0]) == start]
        if spec.window == "concurrency":
            return [e for e in es if e[3] in ("reserved", "unknown")]
        return es

    def used(self, rid, t, urgent=None):
        es = self.live(rid, t)
        if urgent is not None:
            es = [e for e in es if e[2] == urgent]
        return sum(e[1] for e in es)

    def next_available(self, rid, t, amount):
        spec = self.specs[rid]
        if amount > spec.capacity:
            return NEVER
        used = self.used(rid, t)
        if used + amount <= spec.capacity:
            return t
        if spec.window == "rolling":
            for e in sorted(self.live(rid, t), key=lambda e: e[0]):
                used -= e[1]
                if used + amount <= spec.capacity:
                    return e[0] + spec.length
            return NEVER
        if spec.window == "calendar":
            return spec.next_reset(t)
        if spec.window == "concurrency":
            return None
        return NEVER


@pytest.mark.parametrize("seed", range(20))
def test_indexed_ledger_matches_the_naive_scan(seed):
    r = random.Random(seed)
    specs = [
        ResourceSpec("rpm", "requests", r.randint(1, 8), "rolling", length=60),
        ResourceSpec("rpd", "requests", r.randint(5, 40), "calendar", reset_tz="America/Los_Angeles"),
        ResourceSpec("day", "requests", r.randint(5, 40), "calendar", length=3600, offset=600),
        ResourceSpec("all", "requests", r.randint(20, 80), "total"),
    ]
    fast, slow = Ledger(specs), NaiveLedger(specs)
    t = 1_795_651_200 + r.randint(0, 86400)
    for k in range(150):
        t += r.choice((0, 1, 7, 30, 61, 600, 3599, 40_000))
        rid = r.choice([s.id for s in specs])
        amt, urgent = r.randint(1, 3), r.random() < 0.3
        fast.book(rid, t, amt, f"a{k}", urgent, "c")
        slow.book(rid, t, amt, urgent)
        probe = t + r.choice((0, 5, 59, 60, 3600, 86_400))
        for s in specs:
            for u in (None, True, False):
                assert fast.used(s.id, probe, urgent=u) == slow.used(s.id, probe, u)
            need = r.randint(1, 5)
            assert fast.next_available(s.id, probe, need) == slow.next_available(s.id, probe, need)


def test_settlement_and_release_through_the_attempt_index():
    specs = [
        ResourceSpec("tok", "tokens_in", 100, "total"),
        ResourceSpec("conc", "concurrent", 2, "concurrency"),
    ]
    led = Ledger(specs)
    led.book("tok", 5, 40, "a1", False, "c")
    led.book("conc", 5, 1, "a1", False, "c")
    led.book("tok", 3, 10, "a0", False, "c")  # out-of-order booking stays sorted
    assert [e.t for e in led.entries["tok"]] == [3, 5]
    assert led.used("conc", 6) == 1
    led.settle("a1", {"tokens_in": 25})
    assert led.used("tok", 6) == 35 and led.used("conc", 6) == 0 and led.entries["conc"] == []
    led.release("a0")
    assert led.used("tok", 6) == 25
    ov = led.overlay()
    ov.book("tok", 6, 50, "tentative", True, "c")
    assert ov.used("tok", 6) == 75 and led.used("tok", 6) == 25  # the base is untouched


# --- dependency cycles are rejected everywhere ----------------------------------------------------


def test_find_cycle():
    assert find_cycle({"a": ["b"], "b": ["c"], "c": ["a"]}) == ["a", "b", "c", "a"]
    assert find_cycle({"a": [], "b": ["a"], "c": ["a", "b"]}) is None
    assert find_cycle({"a": ["a"]}) == ["a", "a"]


def test_trace_with_a_dependency_cycle_is_rejected():
    job = {
        "type": "job",
        "id": "j",
        "client": "c",
        "class": "x",
        "arrival": 0,
        "provenance": {"arrival": "synthetic"},
        "steps": [
            {"id": "a", "estimate": {}, "depends_on": ["b"]},
            {"id": "b", "estimate": {}, "depends_on": ["a"]},
        ],
    }
    with pytest.raises(TraceError, match="dependency cycle a -> b -> a"):
        loads(json.dumps({"type": "header", "schema": SCHEMA}) + "\n" + json.dumps(job))


def test_simulator_rejects_a_cycle_instead_of_leaving_the_job_waiting_forever():
    job = Job(
        "j",
        "c",
        "x",
        0,
        (Step("a", {"requests": 1}, ("b",)), Step("b", {"requests": 1}, ("a",))),
        provenance={"arrival": Provenance.SYNTHETIC},
    )
    with pytest.raises(ValueError, match="dependency cycle"):
        Simulator([job], [ResourceSpec("q", "requests", 5, "total")], FifoPolicy())


# --- service input validation ---------------------------------------------------------------------


def good():
    return {"id": "j1", "class": "match", "steps": [{"id": "s", "estimate": {"requests": 1}}]}


@pytest.mark.parametrize(
    "change, message",
    [
        ({"id": ""}, "stable client id"),
        ({"id": "x" * 201}, "stable client id"),
        ({"urgent": "yes"}, "'urgent'"),
        ({"priority": True}, "'priority'"),
        ({"deadline": "tomorrow"}, "'deadline'"),
        ({"steps": []}, "between 1 and 100 steps"),
        ({"steps": ["s"]}, "each step must be an object"),
        ({"steps": [{"id": "s", "estimate": {}, "max_attempts": 0}]}, "'max_attempts'"),
        ({"steps": [{"id": "s", "estimate": {}, "depends_on": "t"}]}, "'depends_on'"),
        (
            {
                "steps": [
                    {"id": "a", "estimate": {}, "depends_on": ["b"]},
                    {"id": "b", "estimate": {}, "depends_on": ["a"]},
                ]
            },
            "dependency cycle",
        ),
    ],
)
def test_malformed_registrations_get_a_422(change, message):
    with pytest.raises(ApiError) as e:
        _validate_job({**good(), **change})
    assert e.value.status == 422 and message in e.value.message


def test_a_valid_registration_passes_validation():
    _validate_job(good())


# --- output escaping -------------------------------------------------------------------------------


def test_metric_label_values_are_escaped():
    assert _esc('a"b\\c\nd') == 'a\\"b\\\\c\\nd'


def test_report_escapes_trace_text_inserted_as_html():
    assert "const esc=" in TEMPLATE
    for raw in ("${j.job}", "${j.class}", "${j.client}", "${j.reason}", "${label}", "${c}", "${l}"):
        assert raw not in TEMPLATE, raw


def test_gate_keeps_the_application_error_when_rubato_cannot_settle():
    import httpx

    from rubato.sdk import Gate, RubatoClient

    def handle(request):
        path = request.url.path
        if path == "/v1/claims":
            return httpx.Response(200, json={"permit": {"attempt": "a1"}})
        if path.endswith("/finish"):
            raise httpx.ConnectError("rubato down")
        return httpx.Response(200, json={})

    gate = Gate(RubatoClient("http://rubato", "t", transport=httpx.MockTransport(handle)), "enforce")

    def call():
        raise TimeoutError("provider timed out")

    with pytest.raises(TimeoutError, match="provider timed out"):
        gate.run("j", "s", call)


def test_reserve_does_not_protect_urgent_work_that_already_missed_its_deadline():
    from rubato.policies import ReservePolicy
    from rubato.sim import simulate

    res = [ResourceSpec("q", "requests", 2, "total"), ResourceSpec("rate", "requests", 1, "rolling", 1)]
    late = Job(
        "u",
        "c",
        "app",
        0,
        (Step("a", {"requests": 1}, duration_estimate=5), Step("b", {"requests": 1}, ("a",))),
        urgent=True,
        deadline=3,
        provenance={"arrival": Provenance.SYNTHETIC},
    )
    bg = Job("bg", "c", "bg", 0, (Step("s", {"requests": 1}),), provenance={"arrival": Provenance.SYNTHETIC})
    kept = simulate([late, bg], res, ReservePolicy(reserve={"q": 0}, impossible="run")).by_id()
    dropped = simulate([late, bg], res, ReservePolicy(reserve={"q": 0}, impossible="drop")).by_id()
    assert kept["bg"].status == "pending"  # quota held for the urgent steps
    assert dropped["u"].status == "closed" and dropped["bg"].status == "done"


@pytest.mark.parametrize("which", ["panel", "report"])
def test_embedded_javascript_parses(which, tmp_path):
    """Guard against broken page scripts (e.g. a raw newline inside a JS string literal)."""
    import re
    import shutil
    import subprocess

    node = shutil.which("node")
    if not node:
        pytest.skip("node is not installed")
    if which == "panel":
        from rubato.service.panel import PANEL_HTML as html
    else:
        html = TEMPLATE.replace("__DATA__", "{}").replace("__TITLE__", "t")
    scripts = re.findall(r"<script>(.*?)</script>", html, flags=re.S)
    assert scripts
    js = tmp_path / f"{which}.js"
    js.write_text("\n".join(scripts), encoding="utf-8")
    r = subprocess.run([node, "--check", str(js)], capture_output=True, text=True)
    assert r.returncode == 0, r.stderr
