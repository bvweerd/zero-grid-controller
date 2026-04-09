"""Binary sensor platform for Zero Grid Controller."""

from __future__ import annotations

from homeassistant.components.binary_sensor import BinarySensorEntity
from homeassistant.config_entries import ConfigEntry
from homeassistant.const import EntityCategory
from homeassistant.core import HomeAssistant
from homeassistant.helpers.device_registry import DeviceInfo
from homeassistant.helpers.entity_platform import AddConfigEntryEntitiesCallback
from homeassistant.helpers.update_coordinator import CoordinatorEntity

from .coordinator import ZeroGridCoordinator

PARALLEL_UPDATES = 0


async def async_setup_entry(
    hass: HomeAssistant,
    entry: ConfigEntry,
    async_add_entities: AddConfigEntryEntitiesCallback,
) -> None:
    """Set up binary sensors."""
    coordinator: ZeroGridCoordinator = entry.runtime_data.coordinator
    main_device: DeviceInfo = entry.runtime_data.device

    async_add_entities(
        [ZGCBatteryClippingBinarySensor(coordinator, entry, main_device)]
    )


class ZGCBatteryClippingBinarySensor(
    CoordinatorEntity[ZeroGridCoordinator], BinarySensorEntity
):
    """Battery clipping status."""

    _attr_has_entity_name = True
    _attr_translation_key = "battery_clipping"
    _attr_entity_category = EntityCategory.DIAGNOSTIC
    _attr_entity_registry_enabled_default = False

    def __init__(
        self, coordinator: ZeroGridCoordinator, entry: ConfigEntry, device: DeviceInfo
    ) -> None:
        super().__init__(coordinator)
        self._attr_unique_id = f"{entry.entry_id}_battery_clipping"
        self._attr_device_info = device

    @property
    def is_on(self) -> bool | None:
        if self.coordinator.data is None:
            return None
        return bool(self.coordinator.data.battery_clipping)
