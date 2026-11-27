"""The planner can run as a thread inside the API (RUBATO_EMBEDDED_SCHEDULER=1)."""

import threading

import psycopg
import pytest

pytest.importorskip("fastapi")

from rubato.service import app as api  # noqa: E402
from rubato.service import scheduler  # noqa: E402


def test_run_stops_promptly_while_the_database_is_down(monkeypatch):
    def down(*_a, **_k):
        raise psycopg.OperationalError("down")

    monkeypatch.setattr(scheduler.db, "connect", down)
    stop = threading.Event()
    t = threading.Thread(target=scheduler.run, args=(stop, 30))
    t.start()
    stop.set()
    t.join(timeout=2)
    assert not t.is_alive()  # waits on the event, not a blind sleep


def test_embedded_planner_restarts_after_an_unexpected_error(monkeypatch):
    calls = []
    stop = threading.Event()

    def flaky(ev):
        calls.append(1)
        if len(calls) == 1:
            raise RuntimeError("boom")
        ev.set()

    monkeypatch.setattr(api.scheduler, "run", flaky)
    monkeypatch.setattr(stop, "wait", lambda _t=None: False)  # no real back-off in the test
    api._embedded_planner(stop)
    assert len(calls) == 2
