"""Versioned tariffs: money as integers (``cost_micro`` = millionths of the currency).

Prices are per million tokens, so cost_micro = tokens * price_per_mtok (exact integers).
A tariff belongs to a provider profile; the version is stored with every result.
"""

from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class Tariff:
    version: str
    currency: str
    prices: dict[str, tuple[int, int]]  # model -> (input, output) price per 1M tokens, in currency units

    def cost_micro(self, model: str | None, usage: dict[str, int]) -> int | None:
        if model not in self.prices:
            return None
        pin, pout = self.prices[model]
        return usage.get("tokens_in", 0) * pin + usage.get("tokens_out", 0) * pout

    def with_cost(self, model: str | None, usage: dict[str, int]) -> dict[str, int]:
        if "cost_micro" in usage:
            return usage
        c = self.cost_micro(model, usage)
        return usage if c is None else {**usage, "cost_micro": c}
