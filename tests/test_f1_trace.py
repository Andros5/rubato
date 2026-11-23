"""F1: trace schema v1, provenance, synthetic generator and prospective capture."""

import json

import pytest

from rubato.capture import CaptureLogger, read_capture, to_trace
from rubato.synth import SynthConfig, generate
from rubato.trace import SCHEMA, TraceError, dumps, loads

HEADER = json.dumps({"type": "header", "schema": SCHEMA, "time_unit": "s"})


def job(**over):
    d = {
        "type": "job",
        "id": "j1",
        "client": "radar",
        "class": "match",
        "arrival": 10,
        "deadline": None,
        "provenance": {"arrival": "observed"},
        "steps": [
            {
                "id": "s",
                "estimate": {"requests": 1},
                "attempts": [
                    {
                        "duration": 3,
                        "status": "ok",
                        "usage": {"requests": 1, "tokens_in": 0, "tokens_out": None},
                    }
                ],
            }
        ],
    }
    d.update(over)
    return json.dumps(d)


def test_minimal_valid_trace_loads_and_roundtrips_byte_identical():
    tr = loads(HEADER + "\n" + job())
    assert tr.jobs[0].steps[0].outcomes[0].duration == 3
    text = dumps(tr)
    assert dumps(loads(text)) == text


def test_zero_is_kept_and_null_is_unknown():
    usage = loads(HEADER + "\n" + job()).jobs[0].steps[0].outcomes[0].usage
    assert usage["tokens_in"] == 0
    assert "tokens_out" not in usage


@pytest.mark.parametrize(
    "line, message",
    [
        (job(steps=[{"id": "s", "estimate": {"requests": -1}}]), "non-negative integers"),
        (job(steps=[{"id": "s", "estimate": {}, "depends_on": ["x"]}]), "depends on unknown step 'x'"),
        (job(provenance={}), "provenance.arrival is required"),
        (job(provenance={"arrival": "guessed"}), "provenance of 'arrival' is 'guessed'"),
        (job(arrival="10"), "field 'arrival' must be an integer"),
        ('{"type": "job", "id": "j"}', "missing field 'steps'"),
        ("{not json", "not valid JSON"),
    ],
)
def test_invalid_trace_gives_understandable_error(line, message):
    with pytest.raises(TraceError) as e:
        loads(HEADER + "\n" + line)
    assert message in str(e.value)
    assert "line 2" in str(e.value)


def test_incompatible_schema_version_is_rejected():
    with pytest.raises(TraceError, match="not supported"):
        loads('{"type":"header","schema":"rubato.trace/v2"}\n' + job())


def test_synthetic_generator_is_seeded_and_anchored():
    a, b = dumps(generate(SynthConfig(seed=3))), dumps(generate(SynthConfig(seed=3)))
    assert a == b
    assert a != dumps(generate(SynthConfig(seed=4)))
    small = {j.id: j for j in generate(SynthConfig(seed=3, eval_episodes=5)).jobs}
    big = {j.id: j for j in generate(SynthConfig(seed=3, eval_episodes=6)).jobs}
    assert all(big[k] == v for k, v in small.items())  # adding a job does not reshuffle others


def test_synthetic_trace_contains_only_allowed_fields():
    allowed_job = {
        "type",
        "id",
        "client",
        "class",
        "arrival",
        "deadline",
        "urgent",
        "priority",
        "provenance",
        "steps",
    }
    for ln in dumps(generate()).splitlines()[1:]:
        rec = json.loads(ln)
        if rec["type"] == "job":
            assert set(rec) <= allowed_job
            assert set(rec["provenance"].values()) == {"synthetic"}
            for s in rec["steps"]:
                for a in s.get("attempts", []):
                    assert set(a) <= {"duration", "usage", "status", "retry_after"}


def test_capture_is_fail_safe_and_rejects_free_text(tmp_path):
    blocker = tmp_path / "blocker"
    blocker.write_text("x")
    bad = CaptureLogger(blocker / "cap.jsonl", enabled=True)  # parent is a file: cannot write
    bad.emit("job_enqueued", "c1", job="j", client="radar")  # no exception reaches the host
    assert bad.dropped == 1
    ticks = iter([100.0, 101.0, 102.0, 110.0, 111.0])
    log = CaptureLogger(tmp_path / "cap.jsonl", enabled=True, clock=lambda: next(ticks))
    log.emit("job_enqueued", "c1", job="j", client="radar", urgent=False, **{"class": "match"})
    log.emit("job_enqueued", "c2", job="k", client="radar", prompt="secret text")  # dropped
    log.emit("step_ready", "c1", job="j", step="match")
    log.emit("attempt_started", "c1", job="j", step="match", attempt=1, model="m", estimate={"requests": 1})
    log.emit(
        "attempt_finished",
        "c1",
        job="j",
        step="match",
        attempt=1,
        status="ok",
        usage={"requests": 1, "tokens_in": 50},
        duration_ms=8000,
    )
    assert log.dropped == 1
    assert "secret" not in (tmp_path / "cap.jsonl").read_text()
    tr = to_trace(read_capture(tmp_path / "cap.jsonl"))
    (j,) = tr.jobs
    assert j.provenance["arrival"] == "observed" and j.arrival == 100
    assert j.steps[0].outcomes[0].duration == 8


def test_capture_can_be_switched_off(tmp_path, monkeypatch):
    monkeypatch.setenv("RUBATO_CAPTURE", "off")
    log = CaptureLogger(tmp_path / "cap.jsonl")
    log.emit("job_enqueued", "c", job="j")
    assert not (tmp_path / "cap.jsonl").exists()
