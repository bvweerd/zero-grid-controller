"""Binary sensor platform for Zero Grid Controller."""

from __future__ import annotations

from homeassistant.components.binary_sensor import BinarySensorEntity
from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant
from homeassistant.helpers.device_registry import DeviceInfo
from homeassistant.helpers.entity_platform import AddConfigEntryEntitiesCallback
from homeassistant.helpers.update_coordinator import CoordinatorEntity

from .const import ARRAY_SUBENTRY_TYPE, OUTPUT_TYPE_SWITCH
from .coordinator import ZeroGridCoordinator, ZGCResult


async def async_setup_entry(
    hass: HomeAssistant,
    entry: ConfigEntry,
    async_add_entities: AddConfigEntryEntitiesCallback,
) -> None:
    """Set up binary sensor entities."""
    coordinator: ZeroGridCoordinator = entry.runtime_data.coordinator
    array_devices: dict[str, DeviceInfo] = entry.runtime_data.array_devices

    for subentry in entry.subentries.values():
        if subentry.subentry_type != ARRAY_SUBENTRY_TYPE:
            continue
        if subentry.data.get("output_type") != OUTPUT_TYPE_SWITCH:
            continue
        device = array_devices.get(subentry.subentry_id)
        if device is None:
            continue
        array_name = subentry.data.get("array_name", subentry.subentry_id)
        async_add_entities(
            [
                ZGCArraySwitchStateBinarySensor(
                    coordinator, entry, device, subentry.subentry_id, array_name
                )
            ],
            config_subentry_id=subentry.subentry_id,
        )


class ZGCArraySwitchStateBinarySensor(
    CoordinatorEntity[ZeroGridCoordinator], BinarySensorEntity
):
    """Current commanded state for a switch-based PV array."""

    _attr_has_entity_name = True
    _attr_translation_key = "array_switch_state"

    def __init__(
        self,
        coordinator: ZeroGridCoordinator,
        entry: ConfigEntry,
        device: DeviceInfo,
        subentry_id: str,
        array_name: str,
    ) -> None:
        super().__init__(coordinator)
        self._array_name = array_name
        self._attr_unique_id = f"{entry.entry_id}_{subentry_id}_switch_state"
        self._attr_device_info = device

    @property
    def is_on(self) -> bool | None:
        result: ZGCResult | None = self.coordinator.data
        if result is None:
            return None
        value = result.setpoints.get(self._array_name)
        if value is None:
            return None
        return value > 0
