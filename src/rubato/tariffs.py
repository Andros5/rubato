"""Tariffs in currency units per million tokens, rounded once per attempt."""

from __future__ import annotations

import json
import re
from dataclasses import dataclass, field
from decimal import ROUND_HALF_UP, Decimal
from pathlib import Path

SCHEMA = "rubato.tariff/v1"
Prices = tuple[Decimal, Decimal]


def cost_from_prices(prices: Prices | None, usage: dict[str, int]) -> int | None:
    if "cost_micro" in usage:
        return usage["cost_micro"]
    if prices is None:
        return None
    charges = []
    for component, price in zip(("tokens_in", "tokens_out"), prices, strict=True):
        if price and component not in usage:
            return None
        charges.append(price * usage.get(component, 0))
    return int(sum(charges, Decimal(0)).quantize(Decimal(1), rounding=ROUND_HALF_UP))


@dataclass(frozen=True)
class Tariff:
    version: str
    currency: str
    prices: dict[str, Prices]
    changes: tuple[tuple[int, str, Prices], ...] = field(default_factory=tuple)

    def price_for(self, model: str | None, t: int | None = None) -> Prices | None:
        if model is None:
            return None
        prices = self.prices.get(model)
        if t is not None:
            for at, name, updated in self.changes:
                if at > t:
                    break
                if name == model:
                    prices = updated
        return prices

    def cost_micro(self, model: str | None, usage: dict[str, int], t: int | None = None) -> int | None:
        return cost_from_prices(self.price_for(model, t), usage)

    def with_cost(self, model: str | None, usage: dict[str, int], t: int | None = None) -> dict[str, int]:
        cost = self.cost_micro(model, usage, t)
        return usage if cost is None else {**usage, "cost_micro": cost}

    def config(self) -> dict:
        def entry(model, prices):
            return {"model": model, "input_per_million": str(prices[0]), "output_per_million": str(prices[1])}

        return {
            "schema": SCHEMA,
            "version": self.version,
            "currency": self.currency,
            "rates": [entry(m, p) for m, p in sorted(self.prices.items())]
            + [{**entry(m, p), "effective_at": t} for t, m, p in self.changes],
        }

    @classmethod
    def from_config(cls, cfg: dict) -> Tariff:
        if cfg.get("schema") != SCHEMA:
            raise ValueError(f"expected tariff schema {SCHEMA}")
        currency, version = cfg.get("currency"), cfg.get("version")
        if not isinstance(currency, str) or not re.fullmatch(r"[A-Z]{3}", currency):
            raise ValueError("tariff currency must be a three-letter code")
        if not isinstance(version, str) or not version:
            raise ValueError("tariff version is required")
        prices, changes, seen = {}, [], set()
        for row in cfg["rates"]:
            model, at = row["model"], row.get("effective_at")
            if not isinstance(model, str) or not model or (at is not None and type(at) is not int):
                raise ValueError(
                    "tariff rate requires model and optional integer effective_at (Unix seconds)"
                )
            if (model, at) in seen:
                raise ValueError(f"duplicate tariff rate for {model!r} at {at}")
            seen.add((model, at))
            pair = []
            for field_name in ("input_per_million", "output_per_million"):
                price = row[field_name]
                if not isinstance(price, str) or not re.fullmatch(r"[0-9]{1,8}(?:\.[0-9]{1,9})?", price):
                    raise ValueError(f"tariff {field_name} must be a non-negative decimal string")
                pair.append(Decimal(price))
            rate = (pair[0], pair[1])
            if at is None:
                prices[model] = rate
            else:
                changes.append((at, model, rate))
        return cls(version, currency, prices, tuple(sorted(changes)))


def load_tariff(path: str | Path) -> Tariff:
    return Tariff.from_config(json.loads(Path(path).read_text(encoding="utf-8")))
