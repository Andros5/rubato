"""Resources (limits) and the consumption ledger.

Accounting rule (documented decision, see docs/decisiones.md):
consumption is booked in the window of the instant the attempt was *admitted*.
When the attempt finishes, the booked amount is adjusted to the confirmed usage
but stays in its original window, so a call that ends after a counter reset does
not leak into the new window.
"""

from __future__ import annotations

from bisect import bisect_left, bisect_right
from dataclasses import dataclass, field
from datetime import UTC, datetime, timedelta
from zoneinfo import ZoneInfo

NEVER = -1  # sentinel for next_available: it will never fit under current rules

WINDOW_KINDS = ("rolling", "calendar", "concurrency", "total")
SCOPE_KEYS = ("provider", "model", "client")


@dataclass(frozen=True)
class ResourceSpec:
    id: str
    unit: str
    capacity: int
    window: str = "total"  # rolling | calendar | concurrency | total
    length: int | None = None  # rolling length or calendar period in time units
    reset_tz: str | None = None  # calendar: daily reset at local midnight in this zone (times = epoch s)
    offset: int = 0  # calendar without tz: windows start at offset + k*length
    # {"provider": ..., "model": ..., "client": ...}; every key present must match.
    # {"provider": "p", "client": "c"} is client c's own share of provider p.
    scope: dict[str, str] = field(default_factory=dict)
    strict: bool = True  # strict caps reserve the enforceable maximum
    version: int = 1
    calendar_period: str = "day"
    alert_at: int | None = None  # percentage of capacity used

    def __post_init__(self) -> None:
        if self.window not in WINDOW_KINDS:
            raise ValueError(f"resource {self.id}: unknown window {self.window!r}")
        if self.capacity < 0:
            raise ValueError(f"resource {self.id}: negative capacity")
        if self.window == "rolling" and not self.length:
            raise ValueError(f"resource {self.id}: rolling window needs length")
        if self.window == "calendar" and not (self.length or self.reset_tz):
            raise ValueError(f"resource {self.id}: calendar window needs length or reset_tz")
        unknown = sorted(set(self.scope) - set(SCOPE_KEYS))
        if unknown:
            # an unknown key would otherwise be ignored and the limit would apply to everything
            raise ValueError(
                f"resource {self.id}: unknown scope key(s) {unknown}; expected {list(SCOPE_KEYS)}"
            )
        for k, v in self.scope.items():
            if not isinstance(v, str) or not v:
                raise ValueError(f"resource {self.id}: scope {k!r} must be a non-empty string")
        if self.calendar_period not in ("day", "month") or (
            self.calendar_period == "month" and (self.window != "calendar" or not self.reset_tz)
        ):
            raise ValueError(f"resource {self.id}: monthly calendar requires a reset timezone")
        if self.alert_at is not None and (type(self.alert_at) is not int or not 1 <= self.alert_at <= 100):
            raise ValueError(f"resource {self.id}: alert_at must be a percentage between 1 and 100")

    def applies(self, client: str | None, model: str | None, provider: str | None = None) -> bool:
        if "client" in self.scope and self.scope["client"] != client:
            return False
        if "model" in self.scope and self.scope["model"] != model:
            return False
        if "provider" in self.scope and self.scope["provider"] != provider:
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
            if self.calendar_period == "month":
                midnight = midnight.replace(day=1)
            return int(midnight.timestamp())
        assert self.length
        return self.offset + ((t - self.offset) // self.length) * self.length

    def next_reset(self, t: int) -> int:
        if self.reset_tz:
            tz = ZoneInfo(self.reset_tz)
            local = datetime.fromtimestamp(t, UTC).astimezone(tz)
            if self.calendar_period == "month":
                year, month = divmod(local.month, 12)
                return int(datetime(local.year + year, month + 1, 1, tzinfo=tz).timestamp())
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
    """In-memory ledger used by the simulator and rebuilt per round by the service.

    Entries are kept sorted by admission instant, so each window query is a binary
    search instead of a scan of the whole history (calendar windows convert the time
    zone once per query, not once per entry). An index by attempt makes settlement
    O(entries of that attempt). Released concurrency slots are dropped from the list.
    """

    def __init__(self, specs: list[ResourceSpec]):
        ids = [s.id for s in specs]
        if len(set(ids)) != len(ids):
            raise ValueError("duplicate resource ids")
        self.specs = {s.id: s for s in specs}
        self.entries: dict[str, list[Entry]] = {s.id: [] for s in specs}
        self._times: dict[str, list[int]] = {s.id: [] for s in specs}
        self._by_attempt: dict[str, list[Entry]] = {}

    # -- storage -------------------------------------------------------------
    def add(self, e: Entry) -> Entry:
        times = self._times[e.resource]
        i = bisect_right(times, e.t)
        times.insert(i, e.t)
        self.entries[e.resource].insert(i, e)
        self._by_attempt.setdefault(e.attempt_id, []).append(e)
        return e

    def book(self, rid: str, t: int, amount: int, attempt_id: str, urgent: bool, client: str) -> Entry:
        return self.add(Entry(rid, t, amount, attempt_id, urgent, client))

    def _drop(self, e: Entry) -> None:
        es, times = self.entries[e.resource], self._times[e.resource]
        i = bisect_left(times, e.t)
        while es[i] is not e:
            i += 1
        del es[i], times[i]

    # -- queries -------------------------------------------------------------
    def _live(self, rid: str, t: int) -> list[Entry]:
        spec, es, times = self.specs[rid], self.entries[rid], self._times[rid]
        hi = bisect_right(times, t)
        if spec.window == "rolling":
            assert spec.length
            lo = bisect_right(times, t - spec.length)
        elif spec.window == "calendar":
            lo = bisect_left(times, spec.window_start(t))
        else:  # total, concurrency (released slots are removed on settlement)
            lo = 0
        return [e for e in es[lo:hi] if e.state != "released"]

    def used(self, rid: str, t: int, *, urgent: bool | None = None) -> int:
        es = self._live(rid, t)
        if urgent is not None:
            return sum(e.amount for e in es if e.urgent == urgent)
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
        live = self._live(rid, t)  # sorted by admission instant
        used = sum(e.amount for e in live)
        if used + amount <= spec.capacity:
            return t
        if spec.window == "rolling":
            assert spec.length
            for e in live:
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
        """Tentative view for one decision round: new bookings stay out of this ledger."""
        return _Overlay(self)

    # -- settlement ----------------------------------------------------------
    def settle(self, attempt_id: str, usage: dict[str, int] | None, state: str = "confirmed") -> None:
        """Adjust every entry of an attempt to its confirmed usage (kept in the original window)."""
        for e in self._by_attempt.get(attempt_id, []):
            if e.state == "released":
                continue
            spec = self.specs[e.resource]
            if spec.window == "concurrency":
                e.state = "released"
                self._drop(e)
                continue
            if usage is not None and spec.unit in usage:
                e.amount = int(usage[spec.unit])
            e.state = (
                "unknown"
                if usage is not None and spec.unit == "cost_micro" and spec.unit not in usage
                else state
            )

    def release(self, attempt_id: str) -> None:
        for e in self._by_attempt.get(attempt_id, []):
            if e.state != "released":
                e.state = "released"
                if self.specs[e.resource].window == "concurrency":
                    self._drop(e)

    def consumed(self, rid: str) -> int:
        return sum(e.amount for e in self.entries[rid] if e.state in ("confirmed", "unknown"))


class _Overlay(Ledger):
    """Base ledger plus the tentative bookings of the current round (all at ``now``)."""

    def __init__(self, base: Ledger):
        self.specs = base.specs
        self.base = base
        self.extra: dict[str, list[Entry]] = {rid: [] for rid in base.specs}

    def add(self, e: Entry) -> Entry:
        self.extra[e.resource].append(e)
        return e

    def _live(self, rid: str, t: int) -> list[Entry]:
        live = self.base._live(rid, t)
        extra = [e for e in self.extra[rid] if e.t <= t]
        return live + extra if extra else live

    def overlay(self) -> Ledger:
        return _Overlay(self)

    def settle(self, attempt_id, usage, state="confirmed"):  # pragma: no cover - rounds never settle
        raise NotImplementedError("an overlay only holds tentative bookings")

    release = settle
