"""Seeded synthetic trace generator (``rubato.synth/v1``).

Generated from scratch: no field comes from private data. Randomness is anchored
to (seed, job, step, attempt) so adding a job does not reshuffle the others.

The default day mixes three loads that compete for one provider quota:
* Radar background analyses (class ``match``) arriving in bursts;
* CitaLab evaluation episodes (class ``eval``, 3 dependent steps, can wait);
* urgent Radar applications (CV + letter) with a deadline, if requested.
"""

from __future__ import annotations

import hashlib
import random
from dataclasses import dataclass

from .model import Job, Outcome, Provenance, Status, Step
from .resources import ResourceSpec
from .trace import SCHEMA, Trace

GENERATOR = "rubato.synth/v1"
SYN = Provenance.SYNTHETIC.value


def rng(seed: int, *key: object) -> random.Random:
    h = hashlib.sha256(("|".join(map(str, (seed, *key)))).encode()).digest()
    return random.Random(int.from_bytes(h[:8], "big"))


@dataclass
class SynthConfig:
    seed: int = 7
    day_start: int = 1_795_651_200  # 2026-11-24T00:00:00Z (synthetic calendar day)
    hours: int = 10  # working window, starting at 08:00 UTC
    match_bursts: int = 6
    match_per_burst: int = 18
    eval_episodes: int = 12
    urgent_applications: int = 1
    urgent_at_hour: float = 5.0  # hours after 08:00
    urgent_deadline_s: int = 3600
    invalid_rate: float = 0.08
    error_rate: float = 0.03
    rpm: int = 6
    rpd: int = 150
    tpm: int = 120_000
    model: str = "sim-flash"


def resources(cfg: SynthConfig) -> list[ResourceSpec]:
    """Declared limits of the simulated provider (shape of a per-project Gemini tier)."""
    return [
        ResourceSpec("rpm", "requests", cfg.rpm, window="rolling", length=60, scope={"model": cfg.model}),
        ResourceSpec(
            "rpd",
            "requests",
            cfg.rpd,
            window="calendar",
            reset_tz="America/Los_Angeles",
            scope={"model": cfg.model},
        ),
        ResourceSpec("tpm", "tokens_in", cfg.tpm, window="rolling", length=60, scope={"model": cfg.model}),
    ]


def _outcomes(cfg: SynthConfig, jid: str, sid: str, est: dict[str, int], dur: int, attempts: int):
    outs = []
    for a in range(1, attempts + 1):
        r = rng(cfg.seed, jid, sid, a)
        x = r.random()
        status = (
            Status.INVALID
            if x < cfg.invalid_rate
            else (Status.ERROR if x < cfg.invalid_rate + cfg.error_rate else Status.OK)
        )
        factor = r.lognormvariate(0, 0.25)
        usage = {
            "requests": 1,
            "tokens_in": max(1, int(est["tokens_in"] * factor)),
            "tokens_out": 0
            if status == Status.ERROR
            else max(1, int(est["tokens_out"] * r.lognormvariate(0, 0.3))),
        }
        outs.append(Outcome(max(1, int(dur * r.lognormvariate(0, 0.35))), usage, status))
        if status == Status.OK:
            break
    return tuple(outs)


def generate(cfg: SynthConfig | None = None) -> Trace:
    cfg = cfg or SynthConfig()
    t0 = cfg.day_start + 8 * 3600
    jobs: list[Job] = []
    prov = {"arrival": SYN, "deadline": SYN, "estimate": SYN, "attempts": SYN}
    for b in range(cfg.match_bursts):
        rb = rng(cfg.seed, "burst", b)
        bt = t0 + int((b + rb.random() * 0.5) * cfg.hours * 3600 / cfg.match_bursts)
        for i in range(cfg.match_per_burst):
            jid = f"match-{b:02d}-{i:03d}"
            est = {"requests": 1, "tokens_in": 9000, "tokens_out": 700}
            jobs.append(
                Job(
                    jid,
                    "radar",
                    "match",
                    bt + i,
                    (
                        Step(
                            "match",
                            est,
                            model=cfg.model,
                            duration_estimate=12,
                            max_attempts=2,
                            outcomes=_outcomes(cfg, jid, "match", est, 12, 2),
                        ),
                    ),
                    priority=0,
                    provenance=prov,
                )
            )
    for e in range(cfg.eval_episodes):
        re_ = rng(cfg.seed, "eval", e)
        at = t0 + int(re_.random() * 0.6 * cfg.hours * 3600)
        jid = f"eval-{e:03d}"
        steps = []
        prev: tuple[str, ...] = ()
        for k, (sid, tin, tout) in enumerate(
            (("retrieve", 3000, 300), ("answer", 6000, 900), ("judge", 5000, 200))
        ):
            est = {"requests": 1, "tokens_in": tin, "tokens_out": tout}
            steps.append(
                Step(
                    sid, est, prev, cfg.model, None, 8 + 4 * k, 2, _outcomes(cfg, jid, sid, est, 8 + 4 * k, 2)
                )
            )
            prev = (sid,)
        jobs.append(Job(jid, "citalab", "eval", at, tuple(steps), provenance=prov))
    for u in range(cfg.urgent_applications):
        at = t0 + int((cfg.urgent_at_hour + u * 0.5) * 3600)
        jid = f"application-{u:02d}"
        cv = {"requests": 1, "tokens_in": 14000, "tokens_out": 2500}
        lt = {"requests": 1, "tokens_in": 12000, "tokens_out": 1800}
        jobs.append(
            Job(
                jid,
                "radar",
                "application",
                at,
                (
                    Step(
                        "cv",
                        cv,
                        model=cfg.model,
                        duration_estimate=40,
                        max_attempts=3,
                        outcomes=_outcomes(cfg, jid, "cv", cv, 40, 3),
                    ),
                    Step(
                        "letter",
                        lt,
                        ("cv",),
                        cfg.model,
                        None,
                        35,
                        3,
                        _outcomes(cfg, jid, "letter", lt, 35, 3),
                    ),
                ),
                urgent=True,
                priority=10,
                deadline=at + cfg.urgent_deadline_s,
                provenance=prov,
            )
        )
    jobs.sort(key=lambda j: (j.arrival, j.id))
    header = {
        "type": "header",
        "schema": SCHEMA,
        "time_unit": "s",
        "created_by": GENERATOR,
        "seed": cfg.seed,
        "params": {k: v for k, v in vars(cfg).items()},
        "assumptions": ["all values synthetic; provider limits are declared, not observed"],
    }
    return Trace(header=header, resources=resources(cfg), jobs=jobs)
