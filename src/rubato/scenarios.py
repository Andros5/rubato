"""Hand-checked toy scenarios. Time unit: one turn = 1; turn k spans [k-1, k)."""

from __future__ import annotations

from .model import Job, Outcome, Provenance, Status, Step
from .policies import FifoPolicy, ReservePolicy
from .resources import ResourceSpec

F0_HORIZON = 5  # end of turn 5: the day is over

SYN = {"arrival": Provenance.SYNTHETIC, "deadline": Provenance.SYNTHETIC}


def f0_resources() -> list[ResourceSpec]:
    """One request per turn and three requests in total for the day (5 turns)."""
    return [
        ResourceSpec("rate", "requests", 1, window="rolling", length=1),
        ResourceSpec("quota", "requests", 3, window="calendar", length=5),
    ]


def f0_jobs(urgent: bool = True, invalid_cv: bool = False) -> list[Job]:
    """Three analyses ready at turn 1 (deadline end of turn 3); optionally an urgent
    two-step application (CV, then letter) arriving at turn 4, due at the end of turn 5.

    A job is *finished* only when every step produced a valid output: an HTTP 200 with
    an invalid CV consumes a request but does not complete the CV step.
    """
    req = {"requests": 1}
    jobs = [
        Job(
            f"analisis-{i}",
            "radar",
            "analysis",
            arrival=0,
            deadline=3,
            provenance=SYN,
            steps=(Step("analyze", req),),
        )
        for i in (1, 2, 3)
    ]
    if urgent:
        cv_out = (Outcome(1, req, Status.INVALID),) if invalid_cv else ()
        jobs.append(
            Job(
                "candidatura-1",
                "radar",
                "application",
                arrival=3,
                deadline=5,
                urgent=True,
                priority=10,
                provenance=SYN,
                steps=(Step("cv", req, outcomes=cv_out), Step("letter", req, depends_on=("cv",))),
            )
        )
    return jobs


def f0_policies() -> dict:
    return {
        "fifo": FifoPolicy(),
        "reserva": ReservePolicy(reserve={"quota": 2}, release_at=3),
    }
