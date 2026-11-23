"""F1: read-only radar importer on a fixture database (no private data)."""

import os
import sqlite3
from pathlib import Path

import pytest

from rubato.importers.radar import SchemaError, import_radar
from rubato.trace import dumps, loads

CALLS = (
    "CREATE TABLE calls(id INTEGER PRIMARY KEY,day TEXT,model TEXT,task TEXT,status TEXT,created REAL,"
    "input_tokens INTEGER DEFAULT 0,output_tokens INTEGER DEFAULT 0, detail TEXT, ref TEXT, duration_ms INTEGER,"
    " provider TEXT NOT NULL DEFAULT 'gemini', thoughts_tokens INTEGER, batch_summary TEXT)"
)
ROWS = [
    # day, task, status, created, tin, tout, ref, duration_ms
    ("2026-11-10", "match", "http_503", 1000.0, 0, 0, "job:1", 900),
    ("2026-11-10", "match", "ok", 1010.0, 9000, 700, "job:1", 12000),
    ("2026-11-10", "match", "ok_sanitized", 5000.0, 9100, 650, "job:1", None),  # re-analysis, no duration
    ("2026-11-10", "match", "invalid_output", 1200.0, 8000, 600, "job:2", 11000),
    ("2026-11-11", "cv", "ok", 2000.0, 14000, 2500, "application:abc", 40000),
    ("2026-11-11", "letter", "invalid_output", 2050.0, 12000, 1800, "application:abc", 35000),
    ("2026-11-11", "letter", "ok", 2100.0, 12000, 1700, "application:abc", 33000),
    ("2026-11-11", "match", "ok", 2200.0, 9000, 700, None, 10000),  # cannot form a job
    ("2026-11-11", "match", "ok", 2300.0, 9000, 700, "test:x", 10000),
    ("2026-11-12", "match", "ok", 9000.0, 9000, 700, "job:3", 10000),  # out of range
]


def make_db(path: Path, rows=ROWS, schema=CALLS):
    con = sqlite3.connect(path)
    con.execute(schema)
    for r in rows:
        con.execute(
            "INSERT INTO calls(day,model,task,status,created,input_tokens,output_tokens,ref,duration_ms,detail)"
            " VALUES(?,?,?,?,?,?,?,?,?,?)",
            (r[0], "m", r[1], r[2], r[3], r[4], r[5], r[6], r[7], "free text that must not leak"),
        )
    con.commit()
    con.close()


def test_import_is_reconciled_and_deterministic(tmp_path):
    db = tmp_path / "google-ai.db"
    make_db(db)
    a = import_radar(db, None, "2026-11-10", "2026-11-11", b"k" * 16)
    b = import_radar(db, None, "2026-11-10", "2026-11-11", b"k" * 16)
    assert dumps(a.trace) == dumps(b.trace)
    assert a.report["fingerprint"] == b.report["fingerprint"]
    r = a.report
    assert r["read"] == 9 and r["reconciled"]
    assert r["discarded"] == {"no_ref": 1, "test_ref": 1}
    assert r["included"] == 7
    assert r["jobs_by_class"] == {"application": 1, "match": 3}  # job:1 has two episodes
    assert r["missing"]["attempt.duration"] == 1
    assert r["missing"]["attempt.tokens_in.zero"] == 1  # observed zero, not missing
    text = dumps(a.trace)
    assert "free text" not in text and "job:1" not in text and "abc" not in text
    loads(text)  # valid trace


def test_job_episodes_retries_and_application_steps(tmp_path):
    db = tmp_path / "g.db"
    make_db(db)
    jobs = import_radar(db, None, "2026-11-10", "2026-11-11", b"k" * 16).trace.jobs
    first = [j for j in jobs if j.cls == "match" and j.arrival == 1000][0]
    assert [str(o.status) for o in first.steps[0].outcomes] == ["error", "ok"]
    assert first.provenance["arrival"] == "derived"
    assert first.provenance["deadline"] == "missing" and first.deadline is None
    app = [j for j in jobs if j.cls == "application"][0]
    assert app.urgent and {s.id for s in app.steps} == {"cv", "letter"}
    assert len(app.step("letter").outcomes) == 2


def test_incompatible_schema_is_rejected(tmp_path):
    db = tmp_path / "old.db"
    con = sqlite3.connect(db)
    con.execute("CREATE TABLE calls(id INTEGER PRIMARY KEY, day TEXT, task TEXT)")
    con.commit()
    con.close()
    with pytest.raises(SchemaError, match="incompatible schema"):
        import_radar(db, None, "2026-11-01", "2026-11-30", b"k" * 16)


def test_source_is_opened_read_only(tmp_path):
    db = tmp_path / "g.db"
    make_db(db)
    before = db.stat().st_mtime_ns, db.read_bytes()
    import_radar(db, None, "2026-11-10", "2026-11-11", b"k" * 16)
    assert (db.stat().st_mtime_ns, db.read_bytes()) == before


RADAR = os.environ.get("RUBATO_RADAR_DIR")


@pytest.mark.private
@pytest.mark.skipif(not RADAR, reason="set RUBATO_RADAR_DIR to the radar data folder to run")
def test_private_control_counts_match_plan():
    d = Path(RADAR or ".")
    r = import_radar(d / "google-ai.db", d / "radar.db", "2026-11-09", "2026-11-21", b"k" * 16).report
    assert r["control"] == {
        "calls": 3185,
        "match": 2880,
        "positive_duration": 3085,
        "positive_tokens_in": 3157,
    }
    assert r["reconciled"]
