"""Vocabulary shared by the simulator and the service.

Times are integers. In real traces they are seconds since the Unix epoch (UTC);
in toy scenarios they are abstract "turns". Usage is integer amounts keyed by
unit name (``requests``, ``tokens_in``, ``tokens_out``, ``tokens_total``,
``cost_micro`` = millionths of the currency declared by the provider profile).
"""

from __future__ import annotations

from dataclasses import dataclass, field
from enum import StrEnum

UNITS = ("requests", "tokens_in", "tokens_out", "tokens_total", "cost_micro", "concurrent")


class Provenance(StrEnum):
    """Where a value comes from. Arrivals keep these classes separate in every report."""

    OBSERVED = "observed"  # read from a system that recorded the event when it happened
    DERIVED = "derived"  # estimated from other observed fields (e.g. arrival ~ first call)
    SYNTHETIC = "synthetic"  # produced by a seeded generator
    HYPOTHETICAL = "hypothetical"  # injected by a scenario ("what if an urgent job arrives")
    MISSING = "missing"  # the source has no value; never silently replaced by 0


class Status(StrEnum):
    OK = "ok"  # provider answered and output validated
    INVALID = "invalid"  # provider answered (e.g. HTTP 200) but the output failed validation
    RATE_LIMITED = "rate_limited"  # provider 429: pause the scope, retry later
    ERROR = "error"  # provider/transport error
    CANCELLED = "cancelled"


def find_cycle(deps: dict[str, list[str] | tuple[str, ...]]) -> list[str] | None:
    """Return one dependency cycle among step ids, or None."""
    state: dict[str, int] = {}
    path: list[str] = []

    def visit(n: str) -> list[str] | None:
        state[n] = 1
        path.append(n)
        for d in deps.get(n, ()):
            if state.get(d) == 1:
                return path[path.index(d) :] + [d]
            if state.get(d) is None and d in deps:
                found = visit(d)
                if found:
                    return found
        path.pop()
        state[n] = 2
        return None

    for n in deps:
        if state.get(n) is None:
            found = visit(n)
            if found:
                return found
    return None


def tokens_total(usage: dict[str, int]) -> dict[str, int]:
    """Return usage with ``tokens_total`` filled in when both halves are known."""
    if "tokens_total" in usage:
        return usage
    if "tokens_in" in usage and "tokens_out" in usage:
        return {**usage, "tokens_total": usage["tokens_in"] + usage["tokens_out"]}
    return usage


@dataclass(frozen=True)
class Outcome:
    """What really happened in one attempt. Hidden from the policy until it finishes."""

    duration: int
    usage: dict[str, int]
    status: Status = Status.OK
    retry_after: int | None = None

    @property
    def valid(self) -> bool:
        return self.status == Status.OK


@dataclass(frozen=True)
class Step:
    id: str
    estimate: dict[str, int]  # known before running (history or declared limits)
    depends_on: tuple[str, ...] = ()
    model: str | None = None
    maximum: dict[str, int] | None = None  # enforceable cap (e.g. max_output_tokens)
    duration_estimate: int = 1
    max_attempts: int = 1
    outcomes: tuple[Outcome, ...] = ()  # one per attempt; trace ground truth
    provider: str | None = None  # provider whose limits apply (resources scoped by "provider")

    def outcome(self, attempt: int) -> tuple[Outcome, bool]:
        """Outcome of attempt ``attempt`` (1-based) and whether it came from the trace."""
        if attempt <= len(self.outcomes):
            return self.outcomes[attempt - 1], True
        if self.outcomes:
            return self.outcomes[-1], True
        return Outcome(self.duration_estimate, dict(self.estimate)), False

    def admission_amount(self) -> dict[str, int]:
        """Amount reserved at admission: the enforceable maximum when known, else the estimate."""
        amount = dict(self.estimate)
        if self.maximum:
            amount.update(self.maximum)
        return tokens_total(amount)


@dataclass(frozen=True)
class Job:
    id: str
    client: str
    cls: str
    arrival: int
    steps: tuple[Step, ...]
    urgent: bool = False
    priority: int = 0
    deadline: int | None = None
    provenance: dict[str, str] = field(default_factory=dict)  # field -> Provenance
    known_at: int | None = None  # when the scheduler learned about the job (default: arrival)

    def step(self, step_id: str) -> Step:
        for s in self.steps:
            if s.id == step_id:
                return s
        raise KeyError(f"{self.id}: unknown step {step_id}")

    @property
    def arrival_provenance(self) -> str:
        return self.provenance.get("arrival", Provenance.OBSERVED)


class Action(StrEnum):
    AUTHORIZE = "authorize"
    DEFER = "defer"
    CLOSE = "close"


class Reason(StrEnum):
    """Stable reason codes. Never rename; add new ones."""

    FIFO = "fifo_order"
    PRIORITY = "priority_order"
    URGENT_FIRST = "urgent_first"
    BACKFILL = "backfill"
    DEADLINE_RESERVE = "deadline_reserve"
    CAPACITY_EXHAUSTED = "capacity_exhausted"
    RATE_LIMITED = "rate_limited"
    CONCURRENCY_LIMIT = "concurrency_limit"
    PROVIDER_PAUSE = "provider_pause"
    DEPENDS_ON_STEP = "depends_on_step"
    DEPENDENCY_FAILED = "dependency_failed"
    NEVER_FITS = "never_fits"
    DEADLINE_IMPOSSIBLE = "deadline_impossible"
    MAX_ATTEMPTS = "max_attempts"
    CANCELLED = "cancelled"
    CLIENT_UNAVAILABLE = "client_unavailable"


@dataclass(frozen=True)
class Decision:
    t: int
    job_id: str
    step_id: str
    attempt: int
    policy: str  # name@version
    action: Action
    reason: Reason
    detail: str = ""
    resource: str | None = None  # limiting resource
    value: dict | None = None  # numbers that decided it (need, available, held...)
    next_t: int | None = None  # when to reconsider (None = on next event)

    def to_dict(self) -> dict:
        return {
            "t": self.t,
            "job": self.job_id,
            "step": self.step_id,
            "attempt": self.attempt,
            "policy": self.policy,
            "action": str(self.action),
            "reason": str(self.reason),
            "detail": self.detail,
            "resource": self.resource,
            "value": self.value,
            "next_t": self.next_t,
        }


@dataclass(frozen=True)
class ReadyItem:
    """What a policy sees about one step that could start now. No outcome, no future arrivals."""

    job_id: str
    client: str
    cls: str
    urgent: bool
    priority: int
    arrival: int
    deadline: int | None
    step_id: str
    attempt: int
    ready_since: int
    need: dict[str, int]  # admission amount of this step
    remaining_need: dict[str, int]  # this step + steps of the same job not yet done
    remaining_duration: int  # estimated critical path from now, incl. this step
    model: str | None = None
    seq: int = 0  # stable tie-breaker (trace order)
    provider: str | None = None
