"""Select platform for Zero Grid Controller — control mode selector."""

from __future__ import annotations

import logging

from homeassistant.components.select import SelectEntity
from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant
from homeassistant.helpers.device_registry import DeviceInfo
from homeassistant.helpers.entity_platform import AddConfigEntryEntitiesCallback

from .const import CONF_CONTROL_MODE, ControllerMode
from .coordinator import ZeroGridCoordinator

_LOGGER = logging.getLogger(__name__)

PARALLEL_UPDATES = 0

_ALL_MODES = [
    ControllerMode.ZERO_GRID,
    ControllerMode.ZERO_IMPORT,
    ControllerMode.ZERO_EXPORT,
    ControllerMode.MAXIMIZE_EXPORT,
    ControllerMode.MAXIMIZE_IMPORT,
]


async def async_setup_entry(
    hass: HomeAssistant,
    entry: ConfigEntry,
    async_add_entities: AddConfigEntryEntitiesCallback,
) -> None:
    """Set up select entities."""
    coordinator: ZeroGridCoordinator = entry.runtime_data.coordinator
    main_device: DeviceInfo = entry.runtime_data.device
    async_add_entities([ZGCControlModeSelect(coordinator, entry, main_device)])


class ZGCControlModeSelect(SelectEntity):
    """Select entity to choose the control mode at runtime."""

    _attr_has_entity_name = True
    _attr_translation_key = "control_mode_select"
    _attr_options = [str(m) for m in _ALL_MODES]

    def __init__(
        self,
        coordinator: ZeroGridCoordinator,
        entry: ConfigEntry,
        device: DeviceInfo,
    ) -> None:
        self._coordinator = coordinator
        self._entry = entry
        self._attr_unique_id = f"{entry.entry_id}_control_mode"
        self._attr_device_info = device

    @property
    def current_option(self) -> str:
        return str(self._coordinator.mode)

    async def async_select_option(self, option: str) -> None:
        self._coordinator.set_mode(ControllerMode(option))
        self.async_write_ha_state()
        options = {**self._entry.options, CONF_CONTROL_MODE: option}
        self.hass.config_entries.async_update_entry(self._entry, options=options)
