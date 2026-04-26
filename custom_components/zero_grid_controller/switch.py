"""Switch platform for Zero Grid Controller — controller enable switch."""

from __future__ import annotations

import logging

from homeassistant.components.switch import SwitchEntity
from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant
from homeassistant.helpers.device_registry import DeviceInfo
from homeassistant.helpers.entity_platform import AddConfigEntryEntitiesCallback

from .const import CONF_CONTROLLER_ENABLED
from .coordinator import ZeroGridCoordinator

_LOGGER = logging.getLogger(__name__)

PARALLEL_UPDATES = 0


async def async_setup_entry(
    hass: HomeAssistant,
    entry: ConfigEntry,
    async_add_entities: AddConfigEntryEntitiesCallback,
) -> None:
    """Set up switch entities."""
    coordinator: ZeroGridCoordinator = entry.runtime_data.coordinator
    main_device: DeviceInfo = entry.runtime_data.device
    async_add_entities([ZGCEnableSwitch(coordinator, entry, main_device)])


class ZGCEnableSwitch(SwitchEntity):
    """Switch to enable or disable the Zero Grid Controller."""

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
        return self._coordinator.enabled

    async def async_turn_on(self, **kwargs: object) -> None:
        await self._set(True)

    async def async_turn_off(self, **kwargs: object) -> None:
        await self._set(False)

    async def _set(self, enabled: bool) -> None:
        self._coordinator.set_enabled(enabled)
        self.async_write_ha_state()
        options = {**self._entry.options, CONF_CONTROLLER_ENABLED: enabled}
        self.hass.config_entries.async_update_entry(self._entry, options=options)
