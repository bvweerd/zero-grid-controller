"""Switch platform for Zero Grid Controller — enable/disable per array."""

from __future__ import annotations

import logging
from inspect import isawaitable
from typing import Any

from homeassistant.components.switch import SwitchEntity
from homeassistant.config_entries import ConfigEntry
from homeassistant.const import EntityCategory
from homeassistant.core import HomeAssistant
from homeassistant.helpers.device_registry import DeviceInfo
from homeassistant.helpers.entity_platform import AddConfigEntryEntitiesCallback

from .const import ARRAY_SUBENTRY_TYPE
from .coordinator import ZeroGridCoordinator

_LOGGER = logging.getLogger(__name__)

PARALLEL_UPDATES = 0


async def async_setup_entry(
    hass: HomeAssistant,
    entry: ConfigEntry,
    async_add_entities: AddConfigEntryEntitiesCallback,
) -> None:
    """Set up enable/disable switches."""
    coordinator: ZeroGridCoordinator = entry.runtime_data.coordinator
    main_device: DeviceInfo = entry.runtime_data.device
    array_devices: dict[str, DeviceInfo] = entry.runtime_data.array_devices

    async_add_entities([ZGCMasterEnableSwitch(coordinator, entry, main_device)])

    for subentry in entry.subentries.values():
        if subentry.subentry_type != ARRAY_SUBENTRY_TYPE:
            continue
        array_name = subentry.data.get("array_name", subentry.subentry_id)
        device = array_devices.get(subentry.subentry_id)
        if device is None:
            continue
        async_add_entities(
            [
                ZGCArrayEnableSwitch(
                    coordinator, entry, device, subentry.subentry_id, array_name
                )
            ],
            config_subentry_id=subentry.subentry_id,
        )


class ZGCArrayEnableSwitch(SwitchEntity):
    """Switch to enable or disable control of a PV array."""

    _attr_has_entity_name = True
    _attr_entity_category = EntityCategory.CONFIG

    def __init__(
        self,
        coordinator: ZeroGridCoordinator,
        entry: ConfigEntry,
        device: DeviceInfo,
        subentry_id: str,
        array_name: str,
    ) -> None:
        self._coordinator = coordinator
        self._entry = entry
        self._array_name = array_name
        self._attr_unique_id = f"{entry.entry_id}_{subentry_id}_enabled"
        self._attr_device_info = device
        self._attr_translation_key = "array_enabled"

    @property
    def is_on(self) -> bool:
        array = self._coordinator.get_array(self._array_name)
        return array.enabled if array is not None else True

    async def async_turn_on(self, **kwargs: Any) -> None:
        result = self._coordinator.async_update_array_config(
            self._array_name, {"enabled": True}
        )
        if isawaitable(result):
            await result
        self.async_write_ha_state()

    async def async_turn_off(self, **kwargs: Any) -> None:
        result = self._coordinator.async_update_array_config(
            self._array_name, {"enabled": False}
        )
        if isawaitable(result):
            await result
        self.async_write_ha_state()


class ZGCMasterEnableSwitch(SwitchEntity):
    """Master switch to enable or disable the entire Zero Grid Controller.

    When disabled:
    - PV arrays are set to maximum output (100%)
    - Battery setpoints are set to 0 W
    - PID controller is frozen
    """

    _attr_has_entity_name = True
    _attr_translation_key = "controller_enabled"

    def __init__(
        self,
        coordinator: ZeroGridCoordinator,
        entry: ConfigEntry,
        device: DeviceInfo,
    ) -> None:
        self._coordinator = coordinator
        self._entry = entry
        self._attr_unique_id = f"{entry.entry_id}_controller_enabled"
        self._attr_device_info = device

    @property
    def is_on(self) -> bool:
        """Return True if controller is enabled."""
        return self._coordinator.controller_enabled

    async def async_turn_on(self, **kwargs: Any) -> None:
        """Enable the controller."""
        _LOGGER.info("Enabling Zero Grid Controller")
        await self._coordinator.async_set_controller_enabled(True)
        self.async_write_ha_state()

    async def async_turn_off(self, **kwargs: Any) -> None:
        """Disable the controller and set PV to max, batteries to 0."""
        _LOGGER.info(
            "Disabling Zero Grid Controller - setting PV to max, batteries to 0"
        )
        await self._coordinator.async_set_controller_enabled(False)
        await self._coordinator.async_enter_safe_state(force=True)
        self.async_write_ha_state()
