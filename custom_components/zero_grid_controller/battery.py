"""Battery configuration dataclass and related helpers."""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass
from typing import Any

from .const import (
    BATTERY_CLIPPING_THRESHOLD,
    CONF_BATTERY_CONTROL_ENABLED,
    CONF_BATTERY_MAX_CHARGE_W,
    CONF_BATTERY_MAX_DISCHARGE_W,
    CONF_BATTERY_SENSOR,
    CONF_BATTERY_SETPOINT_ENTITY,
    CONF_NAME,
    CONF_RESPONSE_FACTOR,
    DEFAULT_BATTERY_MAX_CHARGE_W,
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

    def is_clipping(self, current_power_w: float) -> bool:
        """Return True if battery is at maximum charge power."""
        return current_power_w >= self.max_charge_w * BATTERY_CLIPPING_THRESHOLD


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
    )
