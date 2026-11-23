"""Resources (limits) and the consumption ledger.

Accounting rule (documented decision, see docs/decisiones.md):
consumption is booked in the window of the instant the attempt was *admitted*.
When the attempt finishes, the booked amount is adjusted to the confirmed usage
but stays in its original window, so a call that ends after a counter reset does
not leak into the new window.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import UTC, datetime, timedelta
from zoneinfo import ZoneInfo

NEVER = -1  # sentinel for next_available: it will never fit under current rules

WINDOW_KINDS = ("rolling", "calendar", "concurrency", "total")


@dataclass(frozen=True)
class ResourceSpec:
    id: str
    unit: str
    capacity: int
    window: str = "total"  # rolling | calendar | concurrency | total
    length: int | None = None  # rolling length or calendar period in time units
    reset_tz: str | None = None  # calendar: daily reset at local midnight in this zone (times = epoch s)
    offset: int = 0  # calendar without tz: windows start at offset + k*length
    scope: dict[str, str] = field(default_factory=dict)  # {"model": ..., "client": ...}
    strict: bool = True  # strict caps reserve the enforceable maximum
    version: int = 1

    def __post_init__(self) -> None:
        if self.window not in WINDOW_KINDS:
            raise ValueError(f"resource {self.id}: unknown window {self.window!r}")
        if self.capacity < 0:
            raise ValueError(f"resource {self.id}: negative capacity")
        if self.window == "rolling" and not self.length:
            raise ValueError(f"resource {self.id}: rolling window needs length")
        if self.window == "calendar" and not (self.length or self.reset_tz):
            raise ValueError(f"resource {self.id}: calendar window needs length or reset_tz")

    def applies(self, client: str, model: str | None) -> bool:
        if "client" in self.scope and self.scope["client"] != client:
            return False
        if "model" in self.scope and self.scope["model"] != model:
            return False
        return True

    def need_of(self, amount: dict[str, int]) -> int:
        if self.window == "concurrency":
            return 1
        return int(amount.get(self.unit, 0))

    # --- calendar helpers -------------------------------------------------
    def window_start(self, t: int) -> int:
        if self.window != "calendar":
            raise ValueError("window_start only for calendar windows")
        if self.reset_tz:
            tz = ZoneInfo(self.reset_tz)
            local = datetime.fromtimestamp(t, UTC).astimezone(tz)
            midnight = local.replace(hour=0, minute=0, second=0, microsecond=0)
            return int(midnight.timestamp())
        assert self.length
        return self.offset + ((t - self.offset) // self.length) * self.length

    def next_reset(self, t: int) -> int:
        if self.reset_tz:
            tz = ZoneInfo(self.reset_tz)
            local = datetime.fromtimestamp(t, UTC).astimezone(tz)
            nxt = (local.replace(hour=0, minute=0, second=0, microsecond=0) + timedelta(days=1)).replace(
                tzinfo=None
            )
            # rebuild through the zone so DST days (23/25 h) land on the real local midnight
            return int(nxt.replace(tzinfo=tz).timestamp())
        assert self.length
        return self.window_start(t) + self.length


@dataclass
class Entry:
    resource: str
    t: int  # admission instant: decides the window
    amount: int
    attempt_id: str
    urgent: bool
    client: str
    state: str = "reserved"  # reserved | confirmed | unknown | released


class Ledger:
    """In-memory ledger used by the simulator. The service keeps the same rules in PostgreSQL."""

    def __init__(self, specs: list[ResourceSpec]):
        ids = [s.id for s in specs]
        if len(set(ids)) != len(ids):
            raise ValueError("duplicate resource ids")
        self.specs = {s.id: s for s in specs}
        self.entries: dict[str, list[Entry]] = {s.id: [] for s in specs}

    def _live(self, rid: str, t: int) -> list[Entry]:
        spec = self.specs[rid]
        es = [e for e in self.entries[rid] if e.state != "released" and e.t <= t]
        if spec.window == "rolling":
            assert spec.length
            return [e for e in es if e.t > t - spec.length]
        if spec.window == "calendar":
            start = spec.window_start(t)
            return [e for e in es if spec.window_start(e.t) == start]
        if spec.window == "concurrency":
            return [e for e in es if e.state in ("reserved", "unknown")]
        return es

    def used(self, rid: str, t: int, *, urgent: bool | None = None) -> int:
        es = self._live(rid, t)
        if urgent is not None:
            es = [e for e in es if e.urgent == urgent]
        return sum(e.amount for e in es)

    def available(self, rid: str, t: int) -> int:
        return self.specs[rid].capacity - self.used(rid, t)

    def next_available(self, rid: str, t: int, amount: int) -> int | None:
        """Earliest instant > t at which ``amount`` fits, assuming no new admissions.

        Returns NEVER when it cannot fit under the current rules and None when the
        answer depends on a future event (a running attempt finishing).
        """
        spec = self.specs[rid]
        if amount > spec.capacity:
            return NEVER
        used = self.used(rid, t)
        if used + amount <= spec.capacity:
            return t
        if spec.window == "rolling":
            assert spec.length
            for e in sorted(self._live(rid, t), key=lambda e: e.t):
                used -= e.amount
                if used + amount <= spec.capacity:
                    return e.t + spec.length
            return NEVER  # unreachable: once all entries expire, amount <= capacity
        if spec.window == "calendar":
            return spec.next_reset(t)
        if spec.window == "concurrency":
            return None
        return NEVER

    def overlay(self) -> Ledger:
        """Copy whose new bookings do not touch this ledger (tentative allocations of a round)."""
        other = Ledger.__new__(Ledger)
        other.specs = self.specs
        other.entries = {rid: list(es) for rid, es in self.entries.items()}
        return other

    def book(self, rid: str, t: int, amount: int, attempt_id: str, urgent: bool, client: str) -> Entry:
        e = Entry(rid, t, amount, attempt_id, urgent, client)
        self.entries[rid].append(e)
        return e

    def settle(self, attempt_id: str, usage: dict[str, int] | None, state: str = "confirmed") -> None:
        """Adjust every entry of an attempt to its confirmed usage (kept in the original window)."""
        for rid, es in self.entries.items():
            spec = self.specs[rid]
            for e in es:
                if e.attempt_id != attempt_id or e.state == "released":
                    continue
                if spec.window == "concurrency":
                    e.state = "released"
                    continue
                if usage is not None and spec.unit in usage:
                    e.amount = int(usage[spec.unit])
                e.state = state

    def release(self, attempt_id: str) -> None:
        for es in self.entries.values():
            for e in es:
                if e.attempt_id == attempt_id:
                    e.state = "released"

    def consumed(self, rid: str) -> int:
        return sum(e.amount for e in self.entries[rid] if e.state in ("confirmed", "unknown"))
