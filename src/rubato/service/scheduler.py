"""Background planner: runs the same scheduling pass as claims, periodically.

It expires unused permits, flags uncertain attempts and pre-computes permits for
clients whose executors are available. ``python -m rubato.service.scheduler``.
"""

from __future__ import annotations

import json
import logging
import os
import signal
import time

import psycopg

from . import db, store

log = logging.getLogger("rubato.scheduler")


def main() -> None:
    logging.basicConfig(level=os.environ.get("RUBATO_LOG_LEVEL", "INFO"), format="%(message)s", force=True)
    interval = float(os.environ.get("RUBATO_SCHEDULER_INTERVAL", "2"))
    stop = {"flag": False}
    signal.signal(signal.SIGTERM, lambda *_: stop.update(flag=True))
    while not stop["flag"]:
        try:
            with db.connect() as conn:
                db.migrate(conn)
                last_maintenance = float("-inf")  # run once at start, then hourly
                while not stop["flag"]:
                    if time.monotonic() - last_maintenance > 3600:
                        with conn.transaction():
                            log.info(json.dumps({"msg": "maintenance", **store.maintenance(conn)}))
                        last_maintenance = time.monotonic()
                    t0 = time.perf_counter()
                    with conn.transaction():
                        grants = store.schedule_pass(conn)
                    if grants:
                        log.info(
                            json.dumps(
                                {
                                    "msg": "scheduled",
                                    "grants": len(grants),
                                    "ms": round((time.perf_counter() - t0) * 1000, 2),
                                }
                            )
                        )
                    time.sleep(interval)
        except psycopg.OperationalError as e:
            log.warning(json.dumps({"msg": "db unavailable, retrying", "error": str(e)[:200]}))
            time.sleep(min(10, interval * 2))


if __name__ == "__main__":
    main()
