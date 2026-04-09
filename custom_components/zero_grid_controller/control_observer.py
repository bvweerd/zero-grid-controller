"""Observation helpers for control-loop state classification."""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass

from .array import ArrayConfig
from .battery import BatteryConfig


@dataclass(frozen=True)
class ControlObservation:
    """Observed plant state relevant to control decisions."""

    battery_clipping: bool
    pv_clipping_any: bool
    cloud_shadow: bool
    saturation: bool
    any_array_settling: bool


class ControlObserver:
    """Compute derived control-loop observations from raw entity state."""

    def __init__(self, read_sensor_safe: Callable[[str, float], float]) -> None:
        self._read_sensor_safe = read_sensor_safe

    def detect_battery_clipping(self, batteries: list[BatteryConfig]) -> bool:
        """Return True if any battery is charging at its clipping threshold."""
        for battery in batteries:
            battery_w = self._read_sensor_safe(battery.sensor_entity, 0.0)
            if battery.is_clipping(battery_w):
                return True
        return False

    def assess(
        self,
        *,
        residual_w: float,
        now: float,
        arrays: list[ArrayConfig],
        batteries: list[BatteryConfig],
        current_setpoints: dict[str, float],
        settling_until: dict[str, float],
    ) -> ControlObservation:
        """Classify plant behavior for the current control cycle."""
        battery_clipping = self.detect_battery_clipping(batteries)
        arrays_with_pv = [a for a in arrays if a.pv_power_entity]

        pv_clipping_any = False
        all_low_generation = bool(arrays_with_pv)
        any_array_settling = any(
            now < settling_until.get(array.name, 0.0)
            for array in arrays
            if array.enabled
        )

        for array in arrays_with_pv:
            pv_w = self._read_sensor_safe(array.pv_power_entity or "", 0.0)
            sp = current_setpoints.get(array.name, array.setpoint_max)
            sp_w = sp * array.w_per_unit

            if array.is_clipping_active(sp_w, pv_w):
                pv_clipping_any = True

            low_generation = (
                sp_w > 0
                and pv_w <= sp_w * array.cloud_shadow_pv_ratio
                and (sp_w - pv_w) >= array.cloud_shadow_min_gap_w
            )
            all_low_generation = all_low_generation and low_generation

        cloud_shadow = (
            residual_w < 0.0
            and bool(arrays_with_pv)
            and not battery_clipping
            and not pv_clipping_any
            and not any_array_settling
            and all_low_generation
        )
        saturation = battery_clipping and not pv_clipping_any and bool(arrays_with_pv)

        return ControlObservation(
            battery_clipping=battery_clipping,
            pv_clipping_any=pv_clipping_any,
            cloud_shadow=cloud_shadow,
            saturation=saturation,
            any_array_settling=any_array_settling,
        )
