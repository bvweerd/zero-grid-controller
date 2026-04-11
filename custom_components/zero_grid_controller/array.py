"""Array configuration dataclass."""

from __future__ import annotations

import logging
from collections.abc import Mapping
from dataclasses import dataclass
from typing import Any

from .const import (
    CONF_ARRAY_NAME,
    CONF_CALIBRATION_CONFIDENCE,
    CONF_OUTPUT_TYPE,
    CONF_SETPOINT_ENTITY,
    CONF_SETPOINT_MAX,
    CONF_SETPOINT_MIN,
    CONF_SETTLING_TIME_S,
    CONF_SWITCH_DEBOUNCE_S,
    CONF_SWITCH_OFF_THRESHOLD_W,
    CONF_SWITCH_ON_THRESHOLD_W,
    CONF_W_PER_UNIT,
    DEFAULT_SETPOINT_MAX,
    DEFAULT_SETPOINT_MIN,
    DEFAULT_SETTLING_TIME_S,
    DEFAULT_SWITCH_DEBOUNCE_S,
    DEFAULT_SWITCH_OFF_THRESHOLD_W,
    DEFAULT_SWITCH_ON_THRESHOLD_W,
    DEFAULT_W_PER_UNIT,
    OUTPUT_TYPE_PERCENT,
    OUTPUT_TYPE_SWITCH,
)

_LOGGER = logging.getLogger(__name__)


@dataclass
class ArrayConfig:
    """Configuration and runtime state for a single PV array."""

    name: str
    output_type: str  # "percent" | "watt" | "switch"
    setpoint_entity: str
    w_per_unit: float  # W per setpoint unit (measured by calibration)
    calibration_confidence: str  # "measured" | "estimated"
    setpoint_min: float
    setpoint_max: float
    settling_time_s: int

    # Switch-specific
    switch_on_threshold_w: float = DEFAULT_SWITCH_ON_THRESHOLD_W
    switch_off_threshold_w: float = DEFAULT_SWITCH_OFF_THRESHOLD_W
    switch_debounce_s: int = DEFAULT_SWITCH_DEBOUNCE_S

    @property
    def is_switch(self) -> bool:
        """Return True if this is an on/off switch array."""
        return self.output_type == OUTPUT_TYPE_SWITCH

    @property
    def max_power_w(self) -> float:
        """Maximum power capacity in Watts (used for priority distribution)."""
        if self.is_switch:
            return self.switch_on_threshold_w
        return self.setpoint_max * self.w_per_unit

    def headroom_up_w(self, current_setpoint: float) -> float:
        """Watts available to curtail further (lower setpoint)."""
        return max(0.0, (current_setpoint - self.setpoint_min) * self.w_per_unit)

    def headroom_down_w(self, current_setpoint: float) -> float:
        """Watts available to open further (raise setpoint)."""
        return max(0.0, (self.setpoint_max - current_setpoint) * self.w_per_unit)

    def w_to_setpoint(self, watts: float) -> float:
        """Convert a watt delta to setpoint units."""
        if self.w_per_unit == 0:
            return 0.0
        return watts / self.w_per_unit


def array_config_from_subentry(
    subentry_id: str, data: Mapping[str, Any]
) -> ArrayConfig:
    """Build an ArrayConfig from a subentry data dict."""
    return ArrayConfig(
        name=data.get(CONF_ARRAY_NAME, subentry_id),
        output_type=data.get(CONF_OUTPUT_TYPE, OUTPUT_TYPE_PERCENT),
        setpoint_entity=data[CONF_SETPOINT_ENTITY],
        w_per_unit=float(data.get(CONF_W_PER_UNIT, DEFAULT_W_PER_UNIT)),
        calibration_confidence=data.get(CONF_CALIBRATION_CONFIDENCE, "estimated"),
        setpoint_min=float(data.get(CONF_SETPOINT_MIN, DEFAULT_SETPOINT_MIN)),
        setpoint_max=float(data.get(CONF_SETPOINT_MAX, DEFAULT_SETPOINT_MAX)),
        settling_time_s=int(data.get(CONF_SETTLING_TIME_S, DEFAULT_SETTLING_TIME_S)),
        switch_on_threshold_w=float(
            data.get(CONF_SWITCH_ON_THRESHOLD_W, DEFAULT_SWITCH_ON_THRESHOLD_W)
        ),
        switch_off_threshold_w=float(
            data.get(CONF_SWITCH_OFF_THRESHOLD_W, DEFAULT_SWITCH_OFF_THRESHOLD_W)
        ),
        switch_debounce_s=int(
            data.get(CONF_SWITCH_DEBOUNCE_S, DEFAULT_SWITCH_DEBOUNCE_S)
        ),
    )
