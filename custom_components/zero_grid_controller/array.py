"""Array configuration dataclass and related helpers."""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass
from typing import Any

from .const import (
    ARRAY_CLIPPING_THRESHOLD,
    DEFAULT_SETPOINT_MAX,
    DEFAULT_SETPOINT_MIN,
    DEFAULT_SETTLING_TIME_S,
    DEFAULT_SWITCH_DEBOUNCE_S,
    DEFAULT_SWITCH_OFF_THRESHOLD_W,
    DEFAULT_SWITCH_ON_THRESHOLD_W,
    DEFAULT_W_PER_UNIT,
    OUTPUT_TYPE_PERCENT,
)


@dataclass
class ArrayConfig:
    """Configuration and runtime state for a single PV array or switch output."""

    name: str
    enabled: bool
    output_type: str  # "percent" | "watt" | "switch"
    setpoint_entity: str
    pv_power_entity: str | None
    w_per_unit: float  # W per % or W per W (auto-measured or manual)
    calibration_confidence: str  # "measured" | "estimated" | "failed"
    setpoint_min: float
    setpoint_max: float
    settling_time_s: int
    priority: int  # 1 = highest priority (served first)

    # Switch-specific
    switch_on_threshold_w: float = DEFAULT_SWITCH_ON_THRESHOLD_W
    switch_off_threshold_w: float = DEFAULT_SWITCH_OFF_THRESHOLD_W
    switch_debounce_s: int = DEFAULT_SWITCH_DEBOUNCE_S

    # ------------------------------------------------------------------
    # Convenience methods
    # ------------------------------------------------------------------

    def is_clipping_active(self, current_setpoint_w: float, pv_power_w: float) -> bool:
        """Return True if our limit is actually constraining the inverter.

        Considers clipping active when PV output is within 10% of the applied
        limit — i.e. the inverter is pressing against our ceiling, not a cloud.
        """
        if current_setpoint_w <= 0:
            # Fully curtailed — we are the constraint, not a cloud
            return True
        return pv_power_w >= current_setpoint_w * ARRAY_CLIPPING_THRESHOLD

    def headroom_up_w(self, current_setpoint: float) -> float:
        """Watt available to tighten the limit (reduce output)."""
        return max(0.0, (current_setpoint - self.setpoint_min) * self.w_per_unit)

    def headroom_down_w(self, current_setpoint: float) -> float:
        """Watt available to open the limit (increase output)."""
        return max(0.0, (self.setpoint_max - current_setpoint) * self.w_per_unit)

    def setpoint_to_w(self, setpoint: float) -> float:
        """Convert a raw setpoint value to Watts."""
        return setpoint * self.w_per_unit

    def w_to_setpoint(self, watts: float) -> float:
        """Convert Watts to a raw setpoint delta."""
        if self.w_per_unit == 0:
            return 0.0
        return watts / self.w_per_unit


def array_config_from_subentry(
    subentry_id: str, data: Mapping[str, Any]
) -> ArrayConfig:
    """Build an ArrayConfig from a subentry data dict."""
    from .const import (
        CONF_ARRAY_NAME,
        CONF_CALIBRATION_CONFIDENCE,
        CONF_OUTPUT_TYPE,
        CONF_PRIORITY,
        CONF_PV_POWER_ENTITY,
        CONF_SETPOINT_ENTITY,
        CONF_SETPOINT_MAX,
        CONF_SETPOINT_MIN,
        CONF_SETTLING_TIME_S,
        CONF_SWITCH_DEBOUNCE_S,
        CONF_SWITCH_OFF_THRESHOLD_W,
        CONF_SWITCH_ON_THRESHOLD_W,
        CONF_W_PER_UNIT,
    )

    return ArrayConfig(
        name=data.get(CONF_ARRAY_NAME, subentry_id),
        enabled=data.get("enabled", True),
        output_type=data.get(CONF_OUTPUT_TYPE, OUTPUT_TYPE_PERCENT),
        setpoint_entity=data[CONF_SETPOINT_ENTITY],
        pv_power_entity=data.get(CONF_PV_POWER_ENTITY),
        w_per_unit=float(data.get(CONF_W_PER_UNIT, DEFAULT_W_PER_UNIT)),
        calibration_confidence=data.get(CONF_CALIBRATION_CONFIDENCE, "estimated"),
        setpoint_min=float(data.get(CONF_SETPOINT_MIN, DEFAULT_SETPOINT_MIN)),
        setpoint_max=float(data.get(CONF_SETPOINT_MAX, DEFAULT_SETPOINT_MAX)),
        settling_time_s=int(data.get(CONF_SETTLING_TIME_S, DEFAULT_SETTLING_TIME_S)),
        priority=int(data.get(CONF_PRIORITY, 1)),
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
