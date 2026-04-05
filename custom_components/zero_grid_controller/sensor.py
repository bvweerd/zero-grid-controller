"""Sensor platform for Zero Grid Controller."""

from __future__ import annotations

import logging
from typing import Any

from homeassistant.components.sensor import SensorDeviceClass, SensorEntity, SensorStateClass
from homeassistant.config_entries import ConfigEntry
from homeassistant.const import EntityCategory, PERCENTAGE, UnitOfPower
from homeassistant.core import HomeAssistant
from homeassistant.helpers.device_registry import DeviceInfo
from homeassistant.helpers.entity_platform import AddEntitiesCallback
from homeassistant.helpers.update_coordinator import CoordinatorEntity

from .const import ARRAY_SUBENTRY_TYPE, DOMAIN, OUTPUT_TYPE_PERCENT, OUTPUT_TYPE_WATT
from .coordinator import ZeroGridCoordinator, ZGCResult

_LOGGER = logging.getLogger(__name__)

PARALLEL_UPDATES = 0


async def async_setup_entry(
    hass: HomeAssistant,
    entry: ConfigEntry,
    async_add_entities: AddEntitiesCallback,
) -> None:
    """Set up sensor entities for the main device and per-array subentry devices."""
    coordinator: ZeroGridCoordinator = entry.runtime_data.coordinator
    main_device: DeviceInfo = entry.runtime_data.device
    array_devices: dict[str, DeviceInfo] = entry.runtime_data.array_devices

    entities: list[SensorEntity] = [
        # Main device sensors
        ZGCGridRawSensor(coordinator, entry, main_device),
        ZGCGridFilteredSensor(coordinator, entry, main_device),
        ZGCPIDOutputSensor(coordinator, entry, main_device),
        ZGCPIDComponentSensor(coordinator, entry, main_device, "p"),
        ZGCPIDComponentSensor(coordinator, entry, main_device, "i"),
        ZGCPIDComponentSensor(coordinator, entry, main_device, "d"),
        ZGCModeSensor(coordinator, entry, main_device),
        ZGCBatteryClippingSensor(coordinator, entry, main_device),
        ZGCLearningSensor(coordinator, entry, main_device),
    ]

    # Per-array sensors
    for subentry in entry.subentries.values():
        if subentry.subentry_type != ARRAY_SUBENTRY_TYPE:
            continue
        array_name = subentry.data.get("array_name", subentry.subentry_id)
        device = array_devices.get(subentry.subentry_id)
        if device is None:
            continue
        entities += [
            ZGCArraySetpointSensor(coordinator, entry, device, array_name),
            ZGCArrayClippingSensor(coordinator, entry, device, array_name),
            ZGCArrayGainSensor(coordinator, entry, device, array_name),
            ZGCArrayCalibrationSensor(coordinator, entry, device, array_name),
        ]

    async_add_entities(entities)


# ---------------------------------------------------------------------------
# Base class
# ---------------------------------------------------------------------------

class ZGCSensorBase(CoordinatorEntity[ZeroGridCoordinator], SensorEntity):
    """Base for all ZGC sensor entities."""

    _attr_has_entity_name = True

    def __init__(
        self,
        coordinator: ZeroGridCoordinator,
        entry: ConfigEntry,
        device: DeviceInfo,
        key: str,
    ) -> None:
        super().__init__(coordinator)
        self._entry = entry
        self._key = key
        self._attr_unique_id = f"{entry.entry_id}_{key}"
        self._attr_device_info = device
        self._attr_translation_key = key


# ---------------------------------------------------------------------------
# Main device sensors
# ---------------------------------------------------------------------------

class ZGCGridRawSensor(ZGCSensorBase):
    """Raw (unfiltered) grid power sensor."""

    _attr_native_unit_of_measurement = UnitOfPower.WATT
    _attr_device_class = SensorDeviceClass.POWER
    _attr_state_class = SensorStateClass.MEASUREMENT
    _attr_entity_category = EntityCategory.DIAGNOSTIC
    _attr_entity_registry_enabled_default = False

    def __init__(self, coordinator: ZeroGridCoordinator, entry: ConfigEntry, device: DeviceInfo) -> None:
        super().__init__(coordinator, entry, device, "grid_raw_w")

    @property
    def native_value(self) -> float | None:
        if self.coordinator.data is None:
            return None
        return round(self.coordinator.data.grid_raw_w, 1)


class ZGCGridFilteredSensor(ZGCSensorBase):
    """EWM-filtered grid power sensor (primary)."""

    _attr_native_unit_of_measurement = UnitOfPower.WATT
    _attr_device_class = SensorDeviceClass.POWER
    _attr_state_class = SensorStateClass.MEASUREMENT

    def __init__(self, coordinator: ZeroGridCoordinator, entry: ConfigEntry, device: DeviceInfo) -> None:
        super().__init__(coordinator, entry, device, "grid_filtered_w")

    @property
    def native_value(self) -> float | None:
        if self.coordinator.data is None:
            return None
        return round(self.coordinator.data.grid_filtered_w, 1)


class ZGCPIDOutputSensor(ZGCSensorBase):
    """Total PID output in Watts."""

    _attr_native_unit_of_measurement = UnitOfPower.WATT
    _attr_device_class = SensorDeviceClass.POWER
    _attr_state_class = SensorStateClass.MEASUREMENT
    _attr_entity_category = EntityCategory.DIAGNOSTIC
    _attr_entity_registry_enabled_default = False

    def __init__(self, coordinator: ZeroGridCoordinator, entry: ConfigEntry, device: DeviceInfo) -> None:
        super().__init__(coordinator, entry, device, "pid_output_w")

    @property
    def native_value(self) -> float | None:
        if self.coordinator.data is None:
            return None
        return round(self.coordinator.data.pid_output_w, 1)


class ZGCPIDComponentSensor(ZGCSensorBase):
    """P, I or D component sensor."""

    _attr_native_unit_of_measurement = UnitOfPower.WATT
    _attr_device_class = SensorDeviceClass.POWER
    _attr_state_class = SensorStateClass.MEASUREMENT
    _attr_entity_category = EntityCategory.DIAGNOSTIC
    _attr_entity_registry_enabled_default = False

    def __init__(
        self,
        coordinator: ZeroGridCoordinator,
        entry: ConfigEntry,
        device: DeviceInfo,
        component: str,  # "p", "i" or "d"
    ) -> None:
        super().__init__(coordinator, entry, device, f"pid_{component}_w")
        self._component = component

    @property
    def native_value(self) -> float | None:
        if self.coordinator.data is None:
            return None
        val = {
            "p": self.coordinator.data.pid_p_w,
            "i": self.coordinator.data.pid_i_w,
            "d": self.coordinator.data.pid_d_w,
        }.get(self._component)
        return round(val, 2) if val is not None else None


class ZGCModeSensor(ZGCSensorBase):
    """Controller mode sensor."""

    def __init__(self, coordinator: ZeroGridCoordinator, entry: ConfigEntry, device: DeviceInfo) -> None:
        super().__init__(coordinator, entry, device, "mode")

    @property
    def native_value(self) -> str | None:
        if self.coordinator.data is None:
            return None
        return self.coordinator.data.mode


class ZGCBatteryClippingSensor(ZGCSensorBase):
    """Battery clipping status sensor."""

    _attr_entity_category = EntityCategory.DIAGNOSTIC
    _attr_entity_registry_enabled_default = False

    def __init__(self, coordinator: ZeroGridCoordinator, entry: ConfigEntry, device: DeviceInfo) -> None:
        super().__init__(coordinator, entry, device, "battery_clipping")

    @property
    def native_value(self) -> str | None:
        if self.coordinator.data is None:
            return None
        return "on" if self.coordinator.data.battery_clipping else "off"


class ZGCLearningSensor(ZGCSensorBase):
    """Self-tuning learning status sensor."""

    def __init__(self, coordinator: ZeroGridCoordinator, entry: ConfigEntry, device: DeviceInfo) -> None:
        super().__init__(coordinator, entry, device, "learning_status")

    @property
    def native_value(self) -> str | None:
        if self.coordinator.data is None:
            return None
        return self.coordinator.data.learning_status


# ---------------------------------------------------------------------------
# Per-array sensors
# ---------------------------------------------------------------------------

class ZGCArraySensorBase(ZGCSensorBase):
    """Base for per-array sensors."""

    def __init__(
        self,
        coordinator: ZeroGridCoordinator,
        entry: ConfigEntry,
        device: DeviceInfo,
        key: str,
        array_name: str,
    ) -> None:
        super().__init__(coordinator, entry, device, f"{array_name}_{key}")
        self._array_name = array_name
        # Override translation key to use the generic per-array key
        self._attr_translation_key = key


class ZGCArraySetpointSensor(ZGCArraySensorBase):
    """Current setpoint for an array."""

    _attr_state_class = SensorStateClass.MEASUREMENT
    _attr_entity_registry_enabled_default = True

    def __init__(self, coordinator: ZeroGridCoordinator, entry: ConfigEntry, device: DeviceInfo, array_name: str) -> None:
        super().__init__(coordinator, entry, device, "array_setpoint", array_name)

    @property
    def native_unit_of_measurement(self) -> str | None:
        """Return unit based on the array's output type."""
        array = self.coordinator.get_array(self._array_name)
        if array is None:
            return None
        if array.output_type == OUTPUT_TYPE_PERCENT:
            return PERCENTAGE
        if array.output_type == OUTPUT_TYPE_WATT:
            return UnitOfPower.WATT
        return None  # switch type has no numeric unit

    @property
    def native_value(self) -> float | None:
        if self.coordinator.data is None:
            return None
        sp = self.coordinator.data.setpoints.get(self._array_name)
        return round(sp, 1) if sp is not None else None


class ZGCArrayClippingSensor(ZGCArraySensorBase):
    """PV clipping status for an array."""

    _attr_entity_category = EntityCategory.DIAGNOSTIC
    _attr_entity_registry_enabled_default = False

    def __init__(self, coordinator: ZeroGridCoordinator, entry: ConfigEntry, device: DeviceInfo, array_name: str) -> None:
        super().__init__(coordinator, entry, device, "array_clipping", array_name)

    @property
    def native_value(self) -> str | None:
        if self.coordinator.data is None:
            return None
        clipping = self.coordinator.data.array_clipping.get(self._array_name)
        return "on" if clipping else "off"


class ZGCArrayGainSensor(ZGCArraySensorBase):
    """Estimated system gain K for an array."""

    _attr_entity_category = EntityCategory.DIAGNOSTIC
    _attr_entity_registry_enabled_default = False
    _attr_state_class = SensorStateClass.MEASUREMENT

    def __init__(self, coordinator: ZeroGridCoordinator, entry: ConfigEntry, device: DeviceInfo, array_name: str) -> None:
        super().__init__(coordinator, entry, device, "array_gain_k", array_name)

    @property
    def native_value(self) -> float | None:
        if self.coordinator.data is None:
            return None
        k = self.coordinator.data.array_gain_k.get(self._array_name)
        return round(k, 3) if k is not None else None


class ZGCArrayCalibrationSensor(ZGCArraySensorBase):
    """Calibration confidence level for an array."""

    _attr_entity_category = EntityCategory.DIAGNOSTIC
    _attr_entity_registry_enabled_default = False

    def __init__(self, coordinator: ZeroGridCoordinator, entry: ConfigEntry, device: DeviceInfo, array_name: str) -> None:
        super().__init__(coordinator, entry, device, "array_calibration", array_name)

    @property
    def native_value(self) -> str | None:
        if self.coordinator.data is None:
            return None
        return self.coordinator.data.array_calibration.get(self._array_name)
