"""Battery configuration dataclass."""

from __future__ import annotations

import logging
from collections.abc import Mapping
from dataclasses import dataclass
from typing import Any

from .const import (
    CONF_BATTERY_MAX_CHARGE_W,
    CONF_BATTERY_MAX_DISCHARGE_W,
    CONF_BATTERY_SCHEDULE_SENSOR,
    CONF_BATTERY_SENSOR,
    CONF_BATTERY_SETPOINT_ENTITY,
    CONF_NAME,
    DEFAULT_BATTERY_MAX_CHARGE_W,
    DEFAULT_BATTERY_MAX_DISCHARGE_W,
)

_LOGGER = logging.getLogger(__name__)


@dataclass
class BatteryConfig:
    """Configuration for a single battery."""

    subentry_id: str
    name: str
    sensor_entity: str
    max_charge_w: float
    max_discharge_w: float
    setpoint_entity: str
    schedule_sensor_entity: str | None = None


def battery_config_from_subentry(
    subentry_id: str, data: Mapping[str, Any]
) -> BatteryConfig:
    """Build a BatteryConfig from subentry data."""
    name = data.get(CONF_NAME) or f"Battery {subentry_id[:8]}"
    schedule_sensor = data.get(CONF_BATTERY_SCHEDULE_SENSOR) or None
    return BatteryConfig(
        subentry_id=subentry_id,
        name=name,
        sensor_entity=data[CONF_BATTERY_SENSOR],
        max_charge_w=float(
            data.get(CONF_BATTERY_MAX_CHARGE_W, DEFAULT_BATTERY_MAX_CHARGE_W)
        ),
        max_discharge_w=float(
            data.get(CONF_BATTERY_MAX_DISCHARGE_W, DEFAULT_BATTERY_MAX_DISCHARGE_W)
        ),
        setpoint_entity=data[CONF_BATTERY_SETPOINT_ENTITY],
        schedule_sensor_entity=schedule_sensor if schedule_sensor else None,
    )
