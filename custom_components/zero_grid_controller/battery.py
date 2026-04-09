"""Battery configuration dataclass and related helpers."""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass, field
from typing import Any

from .const import (
    BATTERY_CLIPPING_THRESHOLD,
    BATTERY_HARD_RESET_RATIO,
    BATTERY_RECOVERY_BLEND,
    BATTERY_RESPONSE_EWM_ALPHA,
    BATTERY_UNRESPONSIVE_CYCLES,
    BATTERY_UNRESPONSIVE_THRESHOLD_W,
    CONF_BATTERY_CONTROL_ENABLED,
    CONF_BATTERY_MAX_CHARGE_W,
    CONF_BATTERY_MAX_DISCHARGE_W,
    CONF_BATTERY_SENSOR,
    CONF_BATTERY_SETPOINT_ENTITY,
    CONF_NAME,
    CONF_RESPONSE_FACTOR,
    CONF_SETTLING_TIME_S,
    DEFAULT_BATTERY_MAX_CHARGE_W,
    DEFAULT_BATTERY_SETTLING_TIME_S,
    DEFAULT_RESPONSE_FACTOR,
)


@dataclass
class BatteryConfig:
    """Configuration for a single battery."""

    subentry_id: str
    name: str
    sensor_entity: str
    max_charge_w: float
    max_discharge_w: float
    control_enabled: bool
    setpoint_entity: str | None
    response_factor: float = 1.0
    settling_time_s: float = DEFAULT_BATTERY_SETTLING_TIME_S

    # Runtime fields — not persisted in config entry data, restored separately
    measured_response_factor: float = 1.0
    _unresponsive_count: int = field(default=0, init=False, repr=False)

    def is_clipping(self, current_power_w: float) -> bool:
        """Return True if battery is at maximum charge power."""
        return -current_power_w >= self.max_charge_w * BATTERY_CLIPPING_THRESHOLD

    def is_unresponsive(self) -> bool:
        """Return True when the battery has failed to respond for several cycles."""
        return self._unresponsive_count >= BATTERY_UNRESPONSIVE_CYCLES

    def update_response(self, actual_w: float, commanded_w: float) -> None:
        """Update measured_response_factor from one observed (commanded, actual) pair.

        actual_w    — battery power read from sensor (negative = charging)
        commanded_w — setpoint that was written (negative = charging command)

        Uses a slow EWM (α=0.1) for stability.  Triggers a hard reset when the
        battery delivers less than 20 % of what was commanded for three consecutive
        cycles (BMS cutoff, empty SoC, thermal protection).
        """
        if abs(commanded_w) < BATTERY_UNRESPONSIVE_THRESHOLD_W:
            return  # command too small to measure reliably

        observed_ratio = abs(actual_w) / abs(commanded_w)

        if observed_ratio < BATTERY_HARD_RESET_RATIO:
            self._unresponsive_count += 1
            if self._unresponsive_count >= BATTERY_UNRESPONSIVE_CYCLES:
                # Hard reset: snap to observed ratio to stop over-commanding
                self.measured_response_factor = max(0.1, observed_ratio)
        else:
            if self._unresponsive_count > 0:
                # Recovery: blend measured_response_factor toward 1.0 once
                self._unresponsive_count = 0
                self.measured_response_factor = (
                    BATTERY_RECOVERY_BLEND * self.measured_response_factor
                    + (1.0 - BATTERY_RECOVERY_BLEND) * 1.0
                )
            # Normal EWM update
            self.measured_response_factor = (
                BATTERY_RESPONSE_EWM_ALPHA * observed_ratio
                + (1.0 - BATTERY_RESPONSE_EWM_ALPHA) * self.measured_response_factor
            )

        self.measured_response_factor = max(
            0.1, min(2.0, self.measured_response_factor)
        )

    def reset_unresponsive(self) -> None:
        """Clear the unresponsive counter (called after sensor recovers)."""
        self._unresponsive_count = 0


def battery_config_from_subentry(
    subentry_id: str, data: Mapping[str, Any]
) -> BatteryConfig:
    """Build a BatteryConfig from subentry data."""
    name = data.get(CONF_NAME) or f"Battery {subentry_id[:8]}"

    return BatteryConfig(
        subentry_id=subentry_id,
        name=name,
        sensor_entity=data[CONF_BATTERY_SENSOR],
        max_charge_w=float(
            data.get(CONF_BATTERY_MAX_CHARGE_W, DEFAULT_BATTERY_MAX_CHARGE_W)
        ),
        max_discharge_w=float(
            data.get(CONF_BATTERY_MAX_DISCHARGE_W, DEFAULT_BATTERY_MAX_CHARGE_W)
        ),
        control_enabled=bool(data.get(CONF_BATTERY_CONTROL_ENABLED, False)),
        setpoint_entity=data.get(CONF_BATTERY_SETPOINT_ENTITY),
        response_factor=float(data.get(CONF_RESPONSE_FACTOR, DEFAULT_RESPONSE_FACTOR)),
        settling_time_s=float(
            data.get(CONF_SETTLING_TIME_S, DEFAULT_BATTERY_SETTLING_TIME_S)
        ),
    )
