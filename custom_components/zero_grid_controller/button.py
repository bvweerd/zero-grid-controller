"""Button platform for Zero Grid Controller."""

from __future__ import annotations

import logging

from homeassistant.components.button import ButtonEntity
from homeassistant.config_entries import ConfigEntry
from homeassistant.const import EntityCategory
from homeassistant.core import HomeAssistant
from homeassistant.helpers.device_registry import DeviceInfo
from homeassistant.helpers.entity_platform import AddConfigEntryEntitiesCallback

from .coordinator import ZeroGridCoordinator

_LOGGER = logging.getLogger(__name__)

PARALLEL_UPDATES = 0


async def async_setup_entry(
    hass: HomeAssistant,
    entry: ConfigEntry,
    async_add_entities: AddConfigEntryEntitiesCallback,
) -> None:
    """Set up button entities."""
    coordinator: ZeroGridCoordinator = entry.runtime_data.coordinator
    main_device: DeviceInfo = entry.runtime_data.device

    async_add_entities(
        [
            ZGCResetPIDButton(coordinator, entry, main_device),
            ZGCRecalibrateButton(coordinator, entry, main_device),
        ]
    )


class ZGCResetPIDButton(ButtonEntity):
    """Button to reset the PID integrator."""

    _attr_has_entity_name = True
    _attr_entity_category = EntityCategory.CONFIG
    _attr_translation_key = "reset_pid"

    def __init__(
        self,
        coordinator: ZeroGridCoordinator,
        entry: ConfigEntry,
        device: DeviceInfo,
    ) -> None:
        self._coordinator = coordinator
        self._attr_unique_id = f"{entry.entry_id}_reset_pid"
        self._attr_device_info = device

    async def async_press(self) -> None:
        self._coordinator._pid.reset()


class ZGCRecalibrateButton(ButtonEntity):
    """Button to run calibration on all numeric arrays."""

    _attr_has_entity_name = True
    _attr_entity_category = EntityCategory.CONFIG
    _attr_translation_key = "recalibrate"

    def __init__(
        self,
        coordinator: ZeroGridCoordinator,
        entry: ConfigEntry,
        device: DeviceInfo,
    ) -> None:
        self._coordinator = coordinator
        self._attr_unique_id = f"{entry.entry_id}_recalibrate"
        self._attr_device_info = device

    async def async_press(self) -> None:
        if not self._coordinator.arrays:
            _LOGGER.warning("No arrays to calibrate")
            return
        self._coordinator.hass.async_create_task(
            self._coordinator.start_calibration()
        )
