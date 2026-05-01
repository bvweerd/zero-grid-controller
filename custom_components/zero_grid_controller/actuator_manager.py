"""Actuator write helpers for the Zero Grid Controller."""

from __future__ import annotations

import logging

from homeassistant.const import ATTR_ENTITY_ID
from homeassistant.core import HomeAssistant
from homeassistant.exceptions import HomeAssistantError

from .array import ArrayConfig
from .battery import BatteryConfig
from .const import OUTPUT_TYPE_SWITCH

_LOGGER = logging.getLogger(__name__)


class ActuatorManager:
    """Handle actuator writes and fail-safe transitions."""

    def __init__(self, hass: HomeAssistant) -> None:
        self._hass = hass

    async def write_numeric_entity(self, entity_id: str, value: float) -> None:
        """Write a value to a number or input_number entity."""
        domain = entity_id.split(".", 1)[0]
        if domain not in {"number", "input_number"}:
            raise ValueError(f"Unsupported numeric entity domain for {entity_id}")
        state = self._hass.states.get(entity_id)
        if state is None or state.state in ("unavailable", "unknown"):
            _LOGGER.debug("Skipping write to %s: unavailable", entity_id)
            return
        await self._hass.services.async_call(
            domain,
            "set_value",
            {ATTR_ENTITY_ID: entity_id, "value": value},
            blocking=True,
        )

    async def write_switch_entity(self, entity_id: str, turn_on: bool) -> None:
        """Write on/off command to a switch or input_boolean entity."""
        state = self._hass.states.get(entity_id)
        if state is None or state.state in ("unavailable", "unknown"):
            raise HomeAssistantError(f"Entity {entity_id} is unavailable")
        service = "turn_on" if turn_on else "turn_off"
        entity_domain = entity_id.split(".", 1)[0]
        await self._hass.services.async_call(
            entity_domain,
            service,
            {ATTR_ENTITY_ID: entity_id},
            blocking=True,
        )

    async def write_setpoint(self, array: ArrayConfig, value: float) -> None:
        """Write a setpoint to the configured inverter entity."""
        if array.output_type == OUTPUT_TYPE_SWITCH:
            state = self._hass.states.get(array.setpoint_entity)
            if state is None or state.state in ("unavailable", "unknown"):
                _LOGGER.debug(
                    "Skipping write to %s: unavailable", array.setpoint_entity
                )
                return
            service = "turn_on" if value > 0 else "turn_off"
            entity_domain = array.setpoint_entity.split(".", 1)[0]
            await self._hass.services.async_call(
                entity_domain,
                service,
                {ATTR_ENTITY_ID: array.setpoint_entity},
                blocking=True,
            )
        else:
            await self.write_numeric_entity(array.setpoint_entity, value)

    async def enter_safe_state(
        self,
        arrays: list[ArrayConfig],
        batteries: list[BatteryConfig],
        current_setpoints: dict[str, float],
    ) -> None:
        """Move all actuators to a neutral fail-safe state."""
        for array in arrays:
            if array.is_switch:
                continue  # switches are safe at their current state
            try:
                await self.write_setpoint(array, array.setpoint_max)
                current_setpoints[array.name] = array.setpoint_max
            except Exception as err:
                _LOGGER.error("Failed to set %s to max: %s", array.name, err)

        for battery in batteries:
            try:
                await self.write_numeric_entity(battery.setpoint_entity, 0.0)
            except Exception as err:
                _LOGGER.error("Failed to set battery %s to 0: %s", battery.name, err)
