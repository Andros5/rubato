"""The single decision function shared by the simulator and the service.

``decidir`` is pure with respect to its inputs: it only sees what is known at
``now`` (ready steps, the ledger snapshot, pauses and known pending demand). It
never sees outcomes or future arrivals.
"""

from __future__ import annotations

from collections import defaultdict
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
class Demand:
    """Known unfinished work of one job (used by reserve policies)."""

    job_id: str
    urgent: bool
    deadline: int | None
    remaining_need: dict[str, int]
    oldest_wait_since: int | None = None


@dataclass
class Capacity:
    """Snapshot of limits at ``now``: the ledger plus active pauses.

    pauses maps a scope key -- ("model", name), ("client", name) or ("all", "*") --
    to the instant the pause ends.
    """

    ledger: Ledger
    pauses: dict[tuple[str, str], int] = field(default_factory=dict)

    @property
    def specs(self) -> list[ResourceSpec]:
        return list(self.ledger.specs.values())

    def applicable(self, item: ReadyItem) -> list[ResourceSpec]:
        return [s for s in self.specs if s.applies(item.client, item.model)]

    def paused_until(self, item: ReadyItem, now: int) -> int | None:
        until = None
        for key in (("all", "*"), ("model", item.model or ""), ("client", item.client)):
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
        round_alloc: dict[tuple[str, bool], int],
    ) -> tuple[int, int | None]:
        """Amount of ``spec`` withheld from ``item`` and the instant that hold ends (or None)."""
        ...


def _impossible(now: int, item: ReadyItem) -> bool:
    return item.deadline is not None and now + item.remaining_duration > item.deadline


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
    capacity = Capacity(capacity.ledger.overlay(), dict(capacity.pauses))
    round_alloc: dict[tuple[str, bool], int] = defaultdict(int)  # (resource, urgent) -> allocated
    overtaken: list[str] = []
    pol = policy.label

    for item in policy.order(now, ready):
        base = dict(t=now, job_id=item.job_id, step_id=item.step_id, attempt=item.attempt, policy=pol)
        urgent = item.urgent
        if _impossible(now, item):
            mode = policy.on_impossible(item)
            if mode == "drop":
                decisions.append(
                    Decision(
                        **base,
                        action=Action.CLOSE,
                        reason=Reason.DEADLINE_IMPOSSIBLE,
                        detail=f"needs {item.remaining_duration} more time units, deadline {item.deadline} < {now + item.remaining_duration}",
                        value={"deadline": item.deadline, "earliest_finish": now + item.remaining_duration},
                    )
                )
                continue
            if mode == "background":
                urgent = False

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
            need = spec.need_of(item.need)
            if need == 0:
                continue
            if need > spec.capacity:
                never = (spec, need)
                break
            avail = capacity.ledger.available(spec.id, now)
            held, hold_end = (0, None)
            if not urgent:
                held, hold_end = policy.held(now, item, spec, capacity, demand, round_alloc)
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

        for spec in capacity.applicable(item):
            need = spec.need_of(item.need)
            capacity.ledger.book(
                spec.id, now, need, f"round:{item.job_id}/{item.step_id}", urgent, item.client
            )
            round_alloc[(spec.id, urgent)] += need
        reason = policy.authorize_reason(item) if urgent == item.urgent else Reason.FIFO
        detail = ""
        if overtaken:
            reason = Reason.BACKFILL
            detail = "starts ahead of " + ", ".join(dict.fromkeys(overtaken)) + " which do not fit now"
        decisions.append(
            Decision(
                **base,
                action=Action.AUTHORIZE,
                reason=reason,
                detail=detail,
                value={"need": dict(item.need)},
            )
        )
    return decisions
