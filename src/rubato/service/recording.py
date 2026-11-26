"""Bounded recording of live planner inputs and replay over the observed history."""

from __future__ import annotations

import json
from dataclasses import asdict

from ..core import Capacity, Demand, StepDemand, decidir
from ..model import ReadyItem
from ..policies import make_policy
from ..resources import Entry, Ledger, ResourceSpec

MAX_ROUNDS = 300
MAX_BYTES = 16_000_000


def active_recording(conn):
    return conn.execute(
        "SELECT id,round_count,round_bytes FROM replay_checkpoints "
        "WHERE stopped_at IS NULL AND record_until > clock_timestamp() LIMIT 1"
    ).fetchone()


def record_round(conn, t, ready, cap, policy, revision, demand, decisions):
    # The caller holds the same scheduling lock as start and stop. The snapshot
    # and the corresponding scheduling changes commit together.
    active = active_recording(conn)
    if active is None:
        return
    body = json.dumps(
        {
            "policy": policy.config(),
            "label": policy.label,
            "revision": revision,
            "resources": [vars(spec) for spec in cap.specs],
            "ledger": [vars(entry) for entries in cap.ledger.entries.values() for entry in entries],
            "pauses": [[kind, key, end] for (kind, key), end in cap.pauses.items()],
            "executors": cap.executors,
            "turn": cap.turn,
            "ready": [vars(item) for item in ready],
            "demand": [asdict(item) for item in demand],
            "observed": [d.to_dict() for d in decisions],
        }
    )
    size = len(body)  # JSON encoding uses ASCII escapes, so characters equal bytes.
    recording = active["id"]
    ordinal = active["round_count"] + 1
    if ordinal > MAX_ROUNDS or active["round_bytes"] + size > MAX_BYTES:
        conn.execute(
            "UPDATE replay_checkpoints SET stopped_at=least(clock_timestamp(),record_until), "
            "stop_reason='capacity_limit' WHERE id=%s",
            (recording,),
        )
        return
    conn.execute(
        "INSERT INTO replay_rounds (checkpoint_id,ordinal,t,body) VALUES (%s,%s,%s,%s)",
        (recording, ordinal, t, body),
    )
    conn.execute(
        "UPDATE replay_checkpoints SET round_count=%s,round_bytes=round_bytes+%s WHERE id=%s",
        (ordinal, size, recording),
    )


def _replay(t, body, config, turn):
    ledger = Ledger([ResourceSpec(**r) for r in body["resources"]])
    for entry in body["ledger"]:
        ledger.add(Entry(**entry))
    pauses = {(kind, key): end for kind, key, end in body["pauses"]}
    cap = Capacity(ledger, pauses, body["executors"], tuple(turn) if turn else None)
    ready = [ReadyItem(**item) for item in body["ready"]]
    demand = [
        Demand(**{**item, "steps": tuple(StepDemand(**step) for step in item["steps"])})
        for item in body["demand"]
    ]
    policy = make_policy(config["name"], **{k: v for k, v in config.items() if k != "name"})
    return [d.to_dict() for d in decidir(t, ready, cap, policy, demand)]


def replay_capture(capture, candidate=None, job=None):
    """Replay saved rounds and compare a candidate against those same inputs."""

    def selected(decisions):
        return [d for d in decisions if job is None or d["job"] == job]

    rounds = []
    matched_rounds = 0
    for row in capture.get("rounds", []):
        body, t = row["body"], row["t"]
        config = body["policy"]
        reference = _replay(t, body, config, body["turn"])
        matched = reference == body["observed"]
        matched_rounds += matched
        observed = selected(body["observed"])
        reference = selected(reference)
        if job is not None and not (observed or reference):
            continue
        result = {
            "ordinal": row["ordinal"],
            "t": t,
            "policy": body["label"],
            "revision": body["revision"],
            "matched": matched,
            "observed": observed,
            "reference": reference,
        }
        if candidate is not None:
            turn = body["turn"] if config.get("resource") == candidate.get("resource") else None
            result["candidate"] = selected(_replay(t, body, candidate, turn))
        rounds.append(result)
    events = [e for e in capture["events"] if job is None or e["job_id"] == job]
    return {
        "checkpoint": capture["id"],
        "job": job,
        "coverage": capture["coverage"],
        "matched_rounds": matched_rounds,
        "rounds": rounds,
        "events": events,
    }
