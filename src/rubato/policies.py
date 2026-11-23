"""Admission policies. Each one only orders ready work and declares holds; the
admission arithmetic lives in ``core.decidir`` and is identical for all of them."""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass, field

from .core import Capacity, Demand
from .model import ReadyItem, Reason
from .resources import ResourceSpec


@dataclass
class _Base:
    name: str = "base"
    version: int = 1

    @property
    def label(self) -> str:
        return f"{self.name}@{self.version}"

    def on_impossible(self, item: ReadyItem) -> str:
        return "run"

    def held(self, now, item, spec, capacity, demand, round_alloc) -> tuple[int, int | None]:
        return 0, None

    def config(self) -> dict:
        return {"name": self.name, "version": self.version}


@dataclass
class FifoPolicy(_Base):
    """Orden de llegada: job arrival, then trace order."""

    name: str = "fifo"

    def order(self, now: int, ready: Sequence[ReadyItem]) -> list[ReadyItem]:
        return sorted(ready, key=lambda r: (r.arrival, r.seq, r.step_id))

    def authorize_reason(self, item: ReadyItem) -> Reason:
        return Reason.FIFO


@dataclass
class RadarPolicy(_Base):
    """Referencia Radar: ``ORDER BY COALESCE(priority,0) DESC, scheduled_at`` over eligible
    rows (``scheduled_at <= now``), as in ``Workspace.claim`` of swiss-job-radar.

    ``ready_since`` plays the role of ``scheduled_at``. Differences with the real
    worker are listed in docs/referencia-radar.md (batching of analyses, leases,
    browser tasks and one claim per worker loop).
    """

    name: str = "radar"

    def order(self, now: int, ready: Sequence[ReadyItem]) -> list[ReadyItem]:
        return sorted(ready, key=lambda r: (-r.priority, r.ready_since, r.seq, r.step_id))

    def authorize_reason(self, item: ReadyItem) -> Reason:
        return Reason.PRIORITY


@dataclass
class ReservePolicy(_Base):
    """Reserva para urgencias.

    * ``reserve``: per resource id, amount per window withheld from background work
      while no urgent job is known (speculative reserve). Urgent usage in the same
      window counts against it.
    * Known urgent demand (remaining steps of registered urgent jobs) is always
      protected on the reserved resources, even after the speculative reserve is released.
    * ``release_at`` (absolute instant) or ``release_offset`` (offset inside a calendar
      window, e.g. 18 h after local midnight) frees the speculative reserve.
    * ``aging_limit``: background steps that have waited at least this long ignore the
      speculative reserve (anti-starvation).
    * ``impossible``: what to do with urgent work that can no longer meet its deadline:
      ``run`` (keep priority), ``background`` (compete as background) or ``drop``.
    """

    name: str = "reserva"
    reserve: dict[str, int] = field(default_factory=dict)
    release_at: int | None = None
    release_offset: int | None = None
    aging_limit: int | None = None
    impossible: str = "run"

    def config(self) -> dict:
        return {
            "name": self.name,
            "version": self.version,
            "reserve": dict(self.reserve),
            "release_at": self.release_at,
            "release_offset": self.release_offset,
            "aging_limit": self.aging_limit,
            "impossible": self.impossible,
        }

    def order(self, now: int, ready: Sequence[ReadyItem]) -> list[ReadyItem]:
        big = 1 << 62

        def key(r: ReadyItem):
            urgent_now = r.urgent and not (
                self.impossible != "run"
                and r.deadline is not None
                and now + r.remaining_duration > r.deadline
            )
            if urgent_now:
                return (0, r.deadline if r.deadline is not None else big, r.arrival, r.seq, r.step_id)
            return (1, r.arrival, 0, r.seq, r.step_id)

        return sorted(ready, key=key)

    def authorize_reason(self, item: ReadyItem) -> Reason:
        return Reason.URGENT_FIRST if item.urgent else Reason.FIFO

    def on_impossible(self, item: ReadyItem) -> str:
        return self.impossible if item.urgent else "run"

    def _release_instant(self, now: int, spec: ResourceSpec) -> int | None:
        times = []
        if self.release_at is not None:
            times.append(self.release_at)
        if self.release_offset is not None and spec.window == "calendar":
            start = spec.window_start(now)
            times.append(start + self.release_offset)
        return min(times) if times else None

    def held(
        self,
        now: int,
        item: ReadyItem,
        spec: ResourceSpec,
        capacity: Capacity,
        demand: Sequence[Demand],
        round_alloc: dict[tuple[str, bool], int],
    ) -> tuple[int, int | None]:
        if spec.id not in self.reserve:
            return 0, None
        rel = self._release_instant(now, spec)
        released = rel is not None and now >= rel
        aged = self.aging_limit is not None and now - item.ready_since >= self.aging_limit
        speculative = 0
        if not released and not aged:
            urgent_used = capacity.ledger.used(spec.id, now, urgent=True) + round_alloc.get(
                (spec.id, True), 0
            )
            speculative = max(0, self.reserve[spec.id] - urgent_used)
        known = sum(spec.need_of(d.remaining_need) for d in demand if d.urgent)
        known = max(0, known - round_alloc.get((spec.id, True), 0))
        held = max(speculative, known)
        hold_end = None
        if speculative > known:
            ends = [x for x in (rel if not released else None,) if x is not None]
            if self.aging_limit is not None and not aged:
                ends.append(item.ready_since + self.aging_limit)
            hold_end = min(ends) if ends else None
        return held, hold_end


def make_policy(name: str, **params) -> _Base:
    if name == "fifo":
        return FifoPolicy(**params)
    if name == "radar":
        return RadarPolicy(**params)
    if name in ("reserva", "reserve"):
        return ReservePolicy(**params)
    raise ValueError(f"unknown policy {name!r}; expected fifo, radar or reserva")
