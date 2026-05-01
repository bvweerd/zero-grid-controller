"""Load configuration dataclass."""

from __future__ import annotations

import logging
import math
from collections.abc import Mapping
from dataclasses import dataclass
from typing import Any

from .const import (
    CONF_LOAD_ABSOLUTE_MIN_W,
    CONF_LOAD_NAME,
    CONF_LOAD_POWER_W,
    CONF_LOAD_PRIORITY,
    CONF_LOAD_TYPE,
    CONF_POWER_SENSOR_ENTITY,
    CONF_SETPOINT_ENTITY,
    CONF_SETPOINT_MAX,
    CONF_SETPOINT_MIN,
    CONF_SETTLING_TIME_S,
    CONF_SWITCH_DEBOUNCE_S,
    CONF_W_PER_UNIT,
    DEFAULT_LOAD_DEBOUNCE_S,
    DEFAULT_LOAD_PRIORITY,
    DEFAULT_SETPOINT_MAX,
    DEFAULT_SETPOINT_MIN,
    DEFAULT_SETTLING_TIME_S,
    DEFAULT_W_PER_UNIT,
    LOAD_TYPE_SWITCH,
)

_LOGGER = logging.getLogger(__name__)


@dataclass
class LoadConfig:
    """Configuration for a single controllable load."""

    name: str
    load_type: str  # "numeric" | "switch"
    setpoint_entity: str
    priority: int

    # Numeric-specific
    setpoint_min: float = 0.0
    setpoint_max: float = 100.0
    w_per_unit: float = 10.0
    power_sensor_entity: str | None = None
    settling_time_s: int = 15
    # Minimum active power (W); load must be off or at/above this value.
    # Used for EV chargers that require e.g. ≥ 6 A (1380 W) when on.
    absolute_min_w: float | None = None

    # Switch-specific
    power_w: float = 0.0  # fixed power consumption when on (W)
    switch_debounce_s: int = 30

    @property
    def is_switch(self) -> bool:
        """Return True if this is an on/off switch load."""
        return self.load_type == LOAD_TYPE_SWITCH

    def headroom_increase_w(self, current_setpoint: float) -> float:
        """Watts available to increase load further (raise setpoint)."""
        return max(0.0, (self.setpoint_max - current_setpoint) * self.w_per_unit)

    def headroom_decrease_w(self, current_setpoint: float) -> float:
        """Watts available to decrease load further (lower setpoint)."""
        return max(0.0, (current_setpoint - self.setpoint_min) * self.w_per_unit)

    def snap_setpoint(self, new_sp: float, increasing: bool) -> float:
        """Apply absolute_min_w snap logic to a candidate setpoint.

        When increasing from off: snap up to the minimum active setpoint so the
        load never sits in the forbidden zone below absolute_min_w.
        When decreasing toward off: snap down to setpoint_min (fully off) rather
        than stopping in the forbidden zone.
        """
        if self.absolute_min_w is None or self.w_per_unit == 0:
            return new_sp
        new_w = new_sp * self.w_per_unit
        if new_sp <= self.setpoint_min or new_w >= self.absolute_min_w:
            return new_sp
        if increasing:
            snapped = math.ceil(self.absolute_min_w / self.w_per_unit)
            return min(snapped, self.setpoint_max)
        return self.setpoint_min


def load_config_from_subentry(subentry_id: str, data: Mapping[str, Any]) -> LoadConfig:
    """Build a LoadConfig from a subentry data dict."""
    return LoadConfig(
        name=data.get(CONF_LOAD_NAME, subentry_id),
        load_type=data.get(CONF_LOAD_TYPE, "numeric"),
        setpoint_entity=data[CONF_SETPOINT_ENTITY],
        priority=int(data.get(CONF_LOAD_PRIORITY, DEFAULT_LOAD_PRIORITY)),
        setpoint_min=float(data.get(CONF_SETPOINT_MIN, DEFAULT_SETPOINT_MIN)),
        setpoint_max=float(data.get(CONF_SETPOINT_MAX, DEFAULT_SETPOINT_MAX)),
        w_per_unit=float(data.get(CONF_W_PER_UNIT, DEFAULT_W_PER_UNIT)),
        power_sensor_entity=data.get(CONF_POWER_SENSOR_ENTITY),
        settling_time_s=int(data.get(CONF_SETTLING_TIME_S, DEFAULT_SETTLING_TIME_S)),
        absolute_min_w=(
            float(data[CONF_LOAD_ABSOLUTE_MIN_W])
            if data.get(CONF_LOAD_ABSOLUTE_MIN_W) is not None
            else None
        ),
        power_w=float(data.get(CONF_LOAD_POWER_W, 0.0)),
        switch_debounce_s=int(
            data.get(CONF_SWITCH_DEBOUNCE_S, DEFAULT_LOAD_DEBOUNCE_S)
        ),
    )
