"""The single decision function shared by the simulator and the service.

``decidir`` is pure with respect to its inputs: it only sees what is known at
``now`` (ready steps, the ledger snapshot, pauses and known pending demand). It
never sees outcomes or future arrivals.
"""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass, field
from typing import Protocol

from .model import Action, Decision, ReadyItem, Reason
from .resources import NEVER, Ledger, ResourceSpec

LIMIT_REASON = {
    "rolling": Reason.RATE_LIMITED,
    "calendar": Reason.CAPACITY_EXHAUSTED,
    "total": Reason.CAPACITY_EXHAUSTED,
    "concurrency": Reason.CONCURRENCY_LIMIT,
}


@dataclass(frozen=True)
class StepDemand:
    step_id: str
    model: str | None
    need: dict[str, int]
    provider: str | None = None


@dataclass(frozen=True)
class Demand:
    """Known unfinished work of one job (used by reserve policies)."""

    job_id: str
    urgent: bool
    deadline: int | None
    remaining_need: dict[str, int]
    oldest_wait_since: int | None = None
    remaining_duration: int = 0
    client: str | None = None
    steps: tuple[StepDemand, ...] = ()

    def pending_for(self, spec: ResourceSpec, admitted: set[tuple[str, str]]) -> int:
        if not self.steps:
            # Public callers can still supply a job-level aggregate for a shared limit.
            return spec.need_of(self.remaining_need) if not spec.scope else 0
        return sum(
            spec.need_of(step.need)
            for step in self.steps
            if (self.job_id, step.step_id) not in admitted
            and spec.applies(self.client, step.model, step.provider)
        )


def pause_key(model: str | None, provider: str | None) -> tuple[str, str]:
    """Scope paused by a 429: the model when known, else the whole provider."""
    if model or not provider:
        return ("model", model or "")
    return ("provider", provider)


@dataclass
class Capacity:
    """Snapshot of limits at ``now``: the ledger plus active pauses.

    pauses maps a scope key -- ("provider", name), ("model", name), ("client", name)
    or ("all", "*") -- to the instant the pause ends.
    """

    ledger: Ledger
    pauses: dict[tuple[str, str], int] = field(default_factory=dict)
    executors: dict[str, int] | None = None
    turn: tuple[str, int] | None = None
    turn_blocked: dict[str, ReadyItem] = field(default_factory=dict)
    turn_deliveries: int = 0
    turn_closed: set[tuple[str, str]] = field(default_factory=set)

    @property
    def specs(self) -> list[ResourceSpec]:
        return list(self.ledger.specs.values())

    def applicable(self, item: ReadyItem) -> list[ResourceSpec]:
        return [s for s in self.specs if s.applies(item.client, item.model, item.provider)]

    def paused_until(self, item: ReadyItem, now: int) -> int | None:
        until = None
        keys = [("all", "*"), ("model", item.model or ""), ("client", item.client)]
        if item.provider:
            keys.append(("provider", item.provider))
        for key in keys:
            end = self.pauses.get(key)
            if end is not None and end > now:
                until = end if until is None else max(until, end)
        return until


class Policy(Protocol):
    name: str
    version: int

    @property
    def label(self) -> str: ...

    def order(self, now: int, ready: Sequence[ReadyItem]) -> list[ReadyItem]: ...

    def authorize_reason(self, item: ReadyItem) -> Reason: ...

    def on_impossible(self, item: ReadyItem) -> str:
        """'run' (go ahead late), 'drop' (close) or 'background' (lose urgent privileges)."""
        ...

    def held(
        self,
        now: int,
        item: ReadyItem,
        spec: ResourceSpec,
        capacity: Capacity,
        demand: Sequence[Demand],
        admitted: set[tuple[str, str]],
    ) -> tuple[int, int | None]:
        """Amount of ``spec`` withheld from ``item`` and the instant that hold ends (or None)."""
        ...


def _impossible(now: int, item: ReadyItem) -> bool:
    return item.deadline is not None and now + item.remaining_duration > item.deadline


def urgent_for(now: int, item: ReadyItem, policy: Policy) -> bool:
    return item.urgent and not (_impossible(now, item) and policy.on_impossible(item) == "background")


def decidir(
    now: int,
    ready: Sequence[ReadyItem],
    capacity: Capacity,
    policy: Policy,
    demand: Sequence[Demand] = (),
) -> list[Decision]:
    """Decide which ready steps start at ``now``.

    Walks the policy order; each step is authorized only if every applicable
    resource has room for its admission amount after holds (all dimensions or none).
    A step that does not fit is deferred with the binding resource, the numbers
    and the next instant worth reconsidering; later steps may still start
    (backfill), and the decision records which steps they overtook.
    """
    decisions: list[Decision] = []
    # tentative bookings of this round live in an overlay so availability and
    # reconsideration times already account for them
    capacity = Capacity(capacity.ledger.overlay(), capacity.pauses, capacity.executors, capacity.turn)
    free = None if capacity.executors is None else dict(capacity.executors)
    admitted: set[tuple[str, str]] = set()
    overtaken: list[str] = []
    pol = policy.label

    ordered = (
        policy.round_order(now, ready, capacity)
        if hasattr(policy, "round_order")
        else policy.order(now, ready)
    )
    fair_spec = capacity.ledger.specs.get(getattr(policy, "resource", None))
    for item in ordered:
        base = dict(t=now, job_id=item.job_id, step_id=item.step_id, attempt=item.attempt, policy=pol)
        mode = policy.on_impossible(item) if _impossible(now, item) else "run"
        urgent = item.urgent and mode != "background"
        fair_item = (
            not urgent
            and mode != "drop"
            and fair_spec is not None
            and fair_spec.applies(item.client, item.model, item.provider)
            and fair_spec.need_of(item.need)
        )
        if fair_item and (front := capacity.turn_blocked.get(item.client)):
            earlier = next(
                d for d in reversed(decisions) if (d.job_id, d.step_id) == (front.job_id, front.step_id)
            )
            decisions.append(
                Decision(
                    **base,
                    action=Action.DEFER,
                    reason=Reason.FAIR_TURN,
                    resource=earlier.resource,
                    detail=f"{item.client} waits for {front.job_id}/{front.step_id} ({earlier.reason})",
                    value=earlier.value,
                    next_t=earlier.next_t,
                )
            )
            continue
        if mode == "drop":
            decisions.append(
                Decision(
                    **base,
                    action=Action.CLOSE,
                    reason=Reason.DEADLINE_IMPOSSIBLE,
                    detail=f"estimated critical path {item.remaining_duration}; deadline {item.deadline} < {now + item.remaining_duration} (configured drop rule)",
                    value={
                        "deadline": item.deadline,
                        "earliest_finish": now + item.remaining_duration,
                        "basis": "estimated_critical_path",
                    },
                )
            )
            continue

        pause_end = capacity.paused_until(item, now)
        if pause_end is not None:
            decisions.append(
                Decision(
                    **base,
                    action=Action.DEFER,
                    reason=Reason.PROVIDER_PAUSE,
                    detail=f"provider pause until {pause_end}",
                    value={"pause_until": pause_end},
                    next_t=pause_end,
                )
            )
            overtaken.append(item.job_id)
            continue

        never: tuple[ResourceSpec, int] | None = None
        blocks: list[dict] = []
        for spec in capacity.applicable(item):
            if spec.unit == "cost_micro" and "cost_micro" not in item.need:
                blocks.append(dict(resource=spec.id, reason=Reason.USAGE_UNKNOWN, next_t=None))
                continue
            need = spec.need_of(item.need)
            if need == 0:
                continue
            if need > spec.capacity:
                never = (spec, need)
                break
            avail = capacity.ledger.available(spec.id, now)
            held, hold_end = (0, None)
            if not urgent:
                held, hold_end = policy.held(now, item, spec, capacity, demand, admitted)
            if need > avail:
                nxt = capacity.ledger.next_available(spec.id, now, need)
                blocks.append(
                    dict(
                        resource=spec.id,
                        reason=LIMIT_REASON[spec.window],
                        need=need,
                        available=avail,
                        capacity=spec.capacity,
                        next_t=None if nxt in (NEVER, now) else nxt,
                        no_reset=nxt == NEVER,
                    )
                )
            elif need > avail - held:
                nxt = capacity.ledger.next_available(spec.id, now, min(need + held, spec.capacity))
                cands = [x for x in (hold_end, None if nxt in (NEVER, now) else nxt) if x is not None]
                blocks.append(
                    dict(
                        resource=spec.id,
                        reason=Reason.DEADLINE_RESERVE,
                        need=need,
                        available=avail,
                        held=held,
                        capacity=spec.capacity,
                        next_t=min(cands) if cands else None,
                    )
                )

        if never is not None:
            spec, need = never
            if fair_item:
                capacity.turn_closed.add((item.job_id, item.step_id))
            decisions.append(
                Decision(
                    **base,
                    action=Action.CLOSE,
                    reason=Reason.NEVER_FITS,
                    detail=(
                        f"step needs {need} {spec.unit} but {spec.id} allows {spec.capacity} per window; "
                        "raise the limit, lower the step maximum or split the step"
                    ),
                    resource=spec.id,
                    value={"need": need, "capacity": spec.capacity},
                )
            )
            continue

        if blocks:
            # the binding block is the one that clears last; unknown (None) means "on next event"
            def clear_key(b: dict) -> tuple[int, int]:
                return (1, 0) if b["next_t"] is None else (0, b["next_t"])

            binding = max(blocks, key=clear_key)
            nxt = None if any(b["next_t"] is None for b in blocks) else binding["next_t"]
            parts = []
            for b in blocks:
                if b["reason"] == Reason.DEADLINE_RESERVE:
                    parts.append(
                        f"{b['resource']}: needs {b['need']}, {b['available']} available but {b['held']} held for urgent work"
                    )
                elif b["reason"] == Reason.USAGE_UNKNOWN:
                    parts.append(f"{b['resource']}: admission cost is unknown")
                else:
                    tail = " (no reset before horizon)" if b.get("no_reset") else ""
                    parts.append(f"{b['resource']}: needs {b['need']}, {b['available']} available{tail}")
            decisions.append(
                Decision(
                    **base,
                    action=Action.DEFER,
                    reason=binding["reason"],
                    detail="; ".join(parts),
                    resource=binding["resource"],
                    value={"blocks": blocks},
                    next_t=nxt,
                )
            )
            overtaken.append(item.job_id)
            continue

        if free is not None and free.get(item.client, 0) == 0:
            decisions.append(
                Decision(
                    **base,
                    action=Action.DEFER,
                    reason=Reason.CLIENT_UNAVAILABLE,
                    detail=f"client {item.client} has no executor available",
                )
            )
            continue

        for spec in capacity.applicable(item):
            need = spec.need_of(item.need)
            capacity.ledger.book(
                spec.id, now, need, f"round:{item.job_id}/{item.step_id}", urgent, item.client
            )
        admitted.add((item.job_id, item.step_id))
        if free is not None:
            free[item.client] -= 1
        reason = policy.authorize_reason(item) if urgent == item.urgent else Reason.FIFO
        detail = ""
        if overtaken:
            reason = Reason.BACKFILL
            detail = "starts ahead of " + ", ".join(dict.fromkeys(overtaken)) + " which do not fit now"
        value = {"need": dict(item.need)}
        if fair_item:
            weight = policy.weights.get(item.client, 1)
            previous, count = capacity.turn or ("", 0)
            position = count + 1 if previous == item.client and count < weight else 1
            capacity.turn = (item.client, position)
            capacity.turn_deliveries += 1
            reason = Reason.FAIR_TURN
            detail = f"{item.client} receives a turn on {fair_spec.id} ({position}/{weight})"
            value["turn"] = {
                "client": item.client,
                "position": position,
                "weight": weight,
                "resource": fair_spec.id,
            }
        decisions.append(
            Decision(
                **base,
                action=Action.AUTHORIZE,
                reason=reason,
                detail=detail,
                value=value,
            )
        )
    return decisions
