"""Shared utility helpers for Zero Grid Controller."""

from __future__ import annotations

# Multipliers converting a power sensor reading to Watts.
POWER_UNIT_FACTORS: dict[str, float] = {
    "W": 1.0,
    "kW": 1000.0,
    "MW": 1_000_000.0,
    "GW": 1_000_000_000.0,
    "mW": 0.001,
}


def clamp(value: float, lo: float, hi: float) -> float:
    """Clamp value to [lo, hi]."""
    return max(lo, min(hi, value))


def power_unit_factor(unit: str | None) -> float:
    """Return the multiplier to convert a power reading to Watts.

    Unknown or missing units are assumed to already be Watts.
    """
    if unit is None:
        return 1.0
    return POWER_UNIT_FACTORS.get(unit, 1.0)
