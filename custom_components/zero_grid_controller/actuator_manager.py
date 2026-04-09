"""Actuator write helpers for the Zero Grid Controller."""

from __future__ import annotations

import logging
import math
from collections.abc import Awaitable, Callable

from homeassistant.const import ATTR_ENTITY_ID
from homeassistant.core import HomeAssistant

from .array import ArrayConfig
from .battery import BatteryConfig
from .const import (
    BATTERY_WRITE_THRESHOLD_W,
    MODE_PASSIVE,
    OUTPUT_TYPE_SWITCH,
)

_LOGGER = logging.getLogger(__name__)


class ActuatorManager:
    """Handle actuator writes and fail-safe transitions."""

    def __init__(self, hass: HomeAssistant) -> None:
        self._hass = hass

    async def write_numeric_entity(self, entity_id: str, value: float) -> None:
        """Write a numeric target entity, supporting both number and input_number."""
        domain = entity_id.split(".", 1)[0]
        if domain not in {"number", "input_number"}:
            raise ValueError(f"Unsupported numeric entity domain for {entity_id}")
        await self._hass.services.async_call(
            domain,
            "set_value",
            {ATTR_ENTITY_ID: entity_id, "value": value},
            blocking=True,
        )

    async def write_setpoint(self, array: ArrayConfig, value: float) -> None:
        """Write a setpoint to the configured inverter entity."""
        if array.output_type == OUTPUT_TYPE_SWITCH:
            service = "turn_on" if value > 0 else "turn_off"
            await self._hass.services.async_call(
                "switch",
                service,
                {ATTR_ENTITY_ID: array.setpoint_entity},
                blocking=True,
            )
        else:
            await self.write_numeric_entity(array.setpoint_entity, value)

    async def enter_safe_state(
        self,
        *,
        arrays: list[ArrayConfig],
        batteries: list[BatteryConfig],
        current_setpoints: dict[str, float],
        current_battery_setpoints: dict[str, float | None],
        write_setpoint: Callable[[ArrayConfig, float], Awaitable[None]],
        write_numeric_entity: Callable[[str, float], Awaitable[None]],
    ) -> None:
        """Move all actuators to a neutral fail-safe state."""
        for array in arrays:
            if not array.enabled:
                continue
            try:
                await write_setpoint(array, array.setpoint_max)
                current_setpoints[array.name] = array.setpoint_max
                _LOGGER.debug(
                    "Set array %s to max: %.1f", array.name, array.setpoint_max
                )
            except Exception as err:
                _LOGGER.error("Failed to set array %s to max: %s", array.name, err)

        for battery in batteries:
            if not battery.control_enabled or not battery.setpoint_entity:
                continue
            try:
                await write_numeric_entity(battery.setpoint_entity, 0.0)
                current_battery_setpoints[battery.name] = 0.0
                _LOGGER.debug("Set battery %s to 0 W", battery.name)
            except Exception as err:
                _LOGGER.error("Failed to set battery %s to 0: %s", battery.name, err)

    async def apply_battery_targets(
        self,
        *,
        targets: dict[str, float],
        now: float,
        batteries: list[BatteryConfig],
        current_battery_setpoints: dict[str, float | None],
        battery_settling_until: dict[str, float],
        battery_verify_at: dict[str, tuple[float, float]],
        write_numeric_entity: Callable[[str, float], Awaitable[None]],
    ) -> dict[str, float]:
        """Write battery setpoints, respecting settling time and change threshold."""
        for battery in batteries:
            if not battery.control_enabled or not battery.setpoint_entity:
                continue

            new_val = targets.get(battery.name, 0.0)
            if now < battery_settling_until.get(battery.name, 0.0):
                continue

            current_val = current_battery_setpoints.get(battery.name)
            if (
                current_val is not None
                and abs(new_val - current_val) < BATTERY_WRITE_THRESHOLD_W
            ):
                continue

            try:
                await write_numeric_entity(battery.setpoint_entity, new_val)
            except Exception as err:
                _LOGGER.error(
                    "Failed to write battery %s setpoint: %s", battery.name, err
                )
                continue

            current_battery_setpoints[battery.name] = new_val
            battery_settling_until[battery.name] = now + battery.settling_time_s
            battery_verify_at[battery.name] = (
                now + battery.settling_time_s + 1.0,
                new_val,
            )

        return {
            name: val
            for name, val in current_battery_setpoints.items()
            if val is not None
        }

    async def distribute_and_write(
        self,
        *,
        delta_w: float,
        now: float,
        mode: str,
        filtered_w: float,
        arrays: list[ArrayConfig],
        current_setpoints: dict[str, float],
        settling_until: dict[str, float],
        override_setpoints: dict[str, tuple[float, float]],
        write_setpoint: Callable[[ArrayConfig, float], Awaitable[None]],
        clamp_func: Callable[[float, float, float], float],
    ) -> dict[str, float]:
        """Distribute delta_w across active PV arrays proportional to headroom."""
        expired = [name for name, (_, exp) in override_setpoints.items() if now >= exp]
        for name in expired:
            del override_setpoints[name]

        switch_written = await self.apply_switch_hysteresis(
            now=now,
            mode=mode,
            filtered_w=filtered_w,
            arrays=arrays,
            current_setpoints=current_setpoints,
            settling_until=settling_until,
            override_setpoints=override_setpoints,
            write_setpoint=write_setpoint,
        )

        active = [
            a
            for a in arrays
            if a.enabled
            and now >= settling_until.get(a.name, 0.0)
            and a.name not in override_setpoints
            and a.output_type != OUTPUT_TYPE_SWITCH
        ]

        if not active or delta_w == 0:
            return switch_written

        headrooms = {
            a.name: (
                a.headroom_up_w(current_setpoints.get(a.name, a.setpoint_max))
                if delta_w > 0
                else a.headroom_down_w(current_setpoints.get(a.name, a.setpoint_max))
            )
            * a.response_factor
            for a in active
        }
        total = sum(headrooms.values())
        if total <= 0:
            return switch_written

        written: dict[str, float] = dict(switch_written)
        for array in active:
            share_w = delta_w * headrooms[array.name] / total
            delta_unit = share_w / array.w_per_unit
            delta_unit = (
                math.floor(delta_unit) if delta_w > 0 else math.ceil(delta_unit)
            )
            if delta_unit == 0:
                continue

            current = current_setpoints.get(array.name, array.setpoint_max)
            new_sp = clamp_func(current - delta_unit, array.setpoint_min, array.setpoint_max)
            actual_delta = new_sp - current
            if actual_delta == 0:
                continue

            await write_setpoint(array, new_sp)
            current_setpoints[array.name] = new_sp
            settling_until[array.name] = now + array.settling_time_s
            written[array.name] = actual_delta * array.w_per_unit

        return written

    async def apply_switch_hysteresis(
        self,
        *,
        now: float,
        mode: str,
        filtered_w: float,
        arrays: list[ArrayConfig],
        current_setpoints: dict[str, float],
        settling_until: dict[str, float],
        override_setpoints: dict[str, tuple[float, float]],
        write_setpoint: Callable[[ArrayConfig, float], Awaitable[None]],
    ) -> dict[str, float]:
        """Apply on/off hysteresis for switch-based arrays."""
        written: dict[str, float] = {}

        for array in arrays:
            if (
                not array.enabled
                or array.output_type != OUTPUT_TYPE_SWITCH
                or now < settling_until.get(array.name, 0.0)
                or array.name in override_setpoints
            ):
                continue

            current = current_setpoints.get(array.name, array.setpoint_min)
            is_on = current > array.setpoint_min

            if not is_on and filtered_w >= array.switch_on_threshold_w:
                if mode == MODE_PASSIVE:
                    continue
                new_sp = array.setpoint_max
            elif is_on and filtered_w <= -array.switch_off_threshold_w:
                new_sp = array.setpoint_min
            else:
                continue

            await write_setpoint(array, new_sp)
            current_setpoints[array.name] = new_sp
            settling_until[array.name] = now + array.switch_debounce_s

        return written
