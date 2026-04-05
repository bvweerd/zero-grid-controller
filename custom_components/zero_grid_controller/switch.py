"""Switch platform for Zero Grid Controller — enable/disable per array."""

from __future__ import annotations

import logging

from homeassistant.components.switch import SwitchEntity
from homeassistant.config_entries import ConfigEntry
from homeassistant.const import EntityCategory
from homeassistant.core import HomeAssistant
from homeassistant.helpers.device_registry import DeviceInfo
from homeassistant.helpers.entity_platform import AddEntitiesCallback

from .const import ARRAY_SUBENTRY_TYPE, DOMAIN
from .coordinator import ZeroGridCoordinator

_LOGGER = logging.getLogger(__name__)

PARALLEL_UPDATES = 0


async def async_setup_entry(
    hass: HomeAssistant,
    entry: ConfigEntry,
    async_add_entities: AddEntitiesCallback,
) -> None:
    """Set up enable/disable switch for each PV array subentry."""
    coordinator: ZeroGridCoordinator = entry.runtime_data.coordinator
    array_devices: dict[str, DeviceInfo] = entry.runtime_data.array_devices

    entities: list[SwitchEntity] = []
    for subentry in entry.subentries.values():
        if subentry.subentry_type != ARRAY_SUBENTRY_TYPE:
            continue
        array_name = subentry.data.get("array_name", subentry.subentry_id)
        device = array_devices.get(subentry.subentry_id)
        if device is None:
            continue
        entities.append(ZGCArrayEnableSwitch(coordinator, entry, device, array_name))

    async_add_entities(entities)


class ZGCArrayEnableSwitch(SwitchEntity):
    """Switch to enable or disable control of a PV array."""

    _attr_has_entity_name = True
    _attr_entity_category = EntityCategory.CONFIG

    def __init__(
        self,
        coordinator: ZeroGridCoordinator,
        entry: ConfigEntry,
        device: DeviceInfo,
        array_name: str,
    ) -> None:
        self._coordinator = coordinator
        self._entry = entry
        self._array_name = array_name
        self._attr_unique_id = f"{entry.entry_id}_{array_name}_enabled"
        self._attr_device_info = device
        self._attr_translation_key = "array_enabled"

    @property
    def is_on(self) -> bool:
        array = self._coordinator.get_array(self._array_name)
        return array.enabled if array is not None else True

    async def async_turn_on(self, **kwargs) -> None:
        self._coordinator.apply_array_config_update(self._array_name, {"enabled": True})
        self.async_write_ha_state()

    async def async_turn_off(self, **kwargs) -> None:
        self._coordinator.apply_array_config_update(self._array_name, {"enabled": False})
        self.async_write_ha_state()
