"""Sensor platform for Zero Grid Controller."""

from __future__ import annotations

import logging

from homeassistant.components.sensor import (
    SensorDeviceClass,
    SensorEntity,
    SensorStateClass,
)
from homeassistant.config_entries import ConfigEntry
from homeassistant.const import EntityCategory, UnitOfPower
from homeassistant.core import HomeAssistant
from homeassistant.helpers.device_registry import DeviceInfo
from homeassistant.helpers.entity_platform import AddConfigEntryEntitiesCallback
from homeassistant.helpers.update_coordinator import CoordinatorEntity

from .const import ARRAY_SUBENTRY_TYPE, BATTERY_SUBENTRY_TYPE
from .coordinator import ZeroGridCoordinator, ZGCResult

_LOGGER = logging.getLogger(__name__)

PARALLEL_UPDATES = 0


async def async_setup_entry(
    hass: HomeAssistant,
    entry: ConfigEntry,
    async_add_entities: AddConfigEntryEntitiesCallback,
) -> None:
    """Set up sensor entities."""
    coordinator: ZeroGridCoordinator = entry.runtime_data.coordinator
    main_device: DeviceInfo = entry.runtime_data.device
    array_devices: dict[str, DeviceInfo] = entry.runtime_data.array_devices
    battery_devices: dict[str, DeviceInfo] = entry.runtime_data.battery_devices

    async_add_entities(
        [
            ZGCGridRawSensor(coordinator, entry, main_device),
            ZGCGridFilteredSensor(coordinator, entry, main_device),
            ZGCPIDOutputSensor(coordinator, entry, main_device),
            ZGCStatusSensor(coordinator, entry, main_device),
        ]
    )

    for subentry in entry.subentries.values():
        if subentry.subentry_type == ARRAY_SUBENTRY_TYPE:
            array_name = subentry.data.get("array_name", subentry.subentry_id)
            device = array_devices.get(subentry.subentry_id)
            if device is None:
                continue
            async_add_entities(
                [
                    ZGCArraySetpointSensor(
                        coordinator, entry, device, subentry.subentry_id, array_name
                    ),
                ],
                config_subentry_id=subentry.subentry_id,
            )
        elif subentry.subentry_type == BATTERY_SUBENTRY_TYPE:
            battery_name = subentry.data.get("name", subentry.title)
            device = battery_devices.get(subentry.subentry_id)
            if device is None:
                continue
            async_add_entities(
                [
                    ZGCBatterySetpointSensor(
                        coordinator, entry, device, subentry.subentry_id, battery_name
                    ),
                ],
                config_subentry_id=subentry.subentry_id,
            )


# ---------------------------------------------------------------------------
# Base class
# ---------------------------------------------------------------------------


class ZGCSensorBase(CoordinatorEntity[ZeroGridCoordinator], SensorEntity):
    """Base class for Zero Grid Controller sensors."""

    _attr_has_entity_name = True
    _attr_entity_category = EntityCategory.DIAGNOSTIC

    def __init__(
        self,
        coordinator: ZeroGridCoordinator,
        entry: ConfigEntry,
        device: DeviceInfo,
    ) -> None:
        super().__init__(coordinator)
        self._entry = entry
        self._attr_device_info = device


# ---------------------------------------------------------------------------
# Main device sensors
# ---------------------------------------------------------------------------


class ZGCGridRawSensor(ZGCSensorBase):
    """Raw (unfiltered) grid power sensor."""

    _attr_translation_key = "grid_raw_w"
    _attr_device_class = SensorDeviceClass.POWER
    _attr_state_class = SensorStateClass.MEASUREMENT
    _attr_native_unit_of_measurement = UnitOfPower.WATT

    def __init__(self, coordinator: ZeroGridCoordinator, entry: ConfigEntry, device: DeviceInfo) -> None:
        super().__init__(coordinator, entry, device)
        self._attr_unique_id = f"{entry.entry_id}_grid_raw_w"

    @property
    def native_value(self) -> float | None:
        result: ZGCResult | None = self.coordinator.data
        return round(result.grid_raw_w, 1) if result else None


class ZGCGridFilteredSensor(ZGCSensorBase):
    """Filtered grid power sensor."""

    _attr_translation_key = "grid_filtered_w"
    _attr_device_class = SensorDeviceClass.POWER
    _attr_state_class = SensorStateClass.MEASUREMENT
    _attr_native_unit_of_measurement = UnitOfPower.WATT

    def __init__(self, coordinator: ZeroGridCoordinator, entry: ConfigEntry, device: DeviceInfo) -> None:
        super().__init__(coordinator, entry, device)
        self._attr_unique_id = f"{entry.entry_id}_grid_filtered_w"

    @property
    def native_value(self) -> float | None:
        result: ZGCResult | None = self.coordinator.data
        return round(result.grid_filtered_w, 1) if result else None


class ZGCPIDOutputSensor(ZGCSensorBase):
    """PID output (setpoint adjustment) sensor."""

    _attr_translation_key = "pid_output_w"
    _attr_device_class = SensorDeviceClass.POWER
    _attr_state_class = SensorStateClass.MEASUREMENT
    _attr_native_unit_of_measurement = UnitOfPower.WATT

    def __init__(self, coordinator: ZeroGridCoordinator, entry: ConfigEntry, device: DeviceInfo) -> None:
        super().__init__(coordinator, entry, device)
        self._attr_unique_id = f"{entry.entry_id}_pid_output_w"

    @property
    def native_value(self) -> float | None:
        result: ZGCResult | None = self.coordinator.data
        return round(result.pid_output_w, 1) if result else None


class ZGCStatusSensor(ZGCSensorBase):
    """Controller status sensor."""

    _attr_translation_key = "status"

    def __init__(self, coordinator: ZeroGridCoordinator, entry: ConfigEntry, device: DeviceInfo) -> None:
        super().__init__(coordinator, entry, device)
        self._attr_unique_id = f"{entry.entry_id}_status"

    @property
    def native_value(self) -> str | None:
        result: ZGCResult | None = self.coordinator.data
        return result.status if result else None


# ---------------------------------------------------------------------------
# Per-array sensors
# ---------------------------------------------------------------------------


class ZGCArraySetpointSensor(ZGCSensorBase):
    """Current setpoint for a PV array."""

    _attr_translation_key = "array_setpoint"
    _attr_state_class = SensorStateClass.MEASUREMENT

    def __init__(
        self,
        coordinator: ZeroGridCoordinator,
        entry: ConfigEntry,
        device: DeviceInfo,
        subentry_id: str,
        array_name: str,
    ) -> None:
        super().__init__(coordinator, entry, device)
        self._array_name = array_name
        self._attr_unique_id = f"{entry.entry_id}_{subentry_id}_setpoint"

    @property
    def native_value(self) -> float | None:
        result: ZGCResult | None = self.coordinator.data
        if result is None:
            return None
        sp = result.setpoints.get(self._array_name)
        return round(sp, 2) if sp is not None else None


# ---------------------------------------------------------------------------
# Per-battery sensors
# ---------------------------------------------------------------------------


class ZGCBatterySetpointSensor(ZGCSensorBase):
    """Current setpoint written to a battery."""

    _attr_translation_key = "battery_setpoint"
    _attr_device_class = SensorDeviceClass.POWER
    _attr_state_class = SensorStateClass.MEASUREMENT
    _attr_native_unit_of_measurement = UnitOfPower.WATT

    def __init__(
        self,
        coordinator: ZeroGridCoordinator,
        entry: ConfigEntry,
        device: DeviceInfo,
        subentry_id: str,
        battery_name: str,
    ) -> None:
        super().__init__(coordinator, entry, device)
        self._battery_name = battery_name
        self._attr_unique_id = f"{entry.entry_id}_{subentry_id}_battery_setpoint"

    @property
    def native_value(self) -> float | None:
        result: ZGCResult | None = self.coordinator.data
        if result is None:
            return None
        sp = result.battery_setpoints.get(self._battery_name)
        return round(sp, 1) if sp is not None else None
