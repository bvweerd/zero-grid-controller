"""Number platform for Zero Grid Controller — tuning parameters."""

from __future__ import annotations

import logging

from homeassistant.components.number import NumberEntity, NumberMode
from homeassistant.config_entries import ConfigEntry
from homeassistant.const import EntityCategory
from homeassistant.core import HomeAssistant
from homeassistant.helpers.device_registry import DeviceInfo
from homeassistant.helpers.entity_platform import AddConfigEntryEntitiesCallback

from .const import (
    CONF_DEADBAND_W,
    CONF_EWM_ALPHA,
    DEADBAND_MAX_W,
    DEADBAND_MIN_W,
    DEADBAND_STEP_W,
    DEFAULT_DEADBAND_W,
    DEFAULT_EWM_ALPHA,
    EWM_ALPHA_MAX,
    EWM_ALPHA_MIN,
    EWM_ALPHA_STEP,
)
from .coordinator import ZeroGridCoordinator

_LOGGER = logging.getLogger(__name__)

PARALLEL_UPDATES = 0


async def async_setup_entry(
    hass: HomeAssistant,
    entry: ConfigEntry,
    async_add_entities: AddConfigEntryEntitiesCallback,
) -> None:
    """Set up number entities."""
    coordinator: ZeroGridCoordinator = entry.runtime_data.coordinator
    main_device: DeviceInfo = entry.runtime_data.device

    async_add_entities(
        [
            ZGCDeadbandNumber(coordinator, entry, main_device),
            ZGCFilterAlphaNumber(coordinator, entry, main_device),
        ]
    )


class _ZGCNumberBase(NumberEntity):
    """Base class for coordinator-backed number entities."""

    _attr_has_entity_name = True
    _attr_entity_category = EntityCategory.CONFIG
    _attr_entity_registry_enabled_default = False
    _attr_mode = NumberMode.BOX

    def __init__(
        self,
        coordinator: ZeroGridCoordinator,
        entry: ConfigEntry,
        device: DeviceInfo,
    ) -> None:
        self._coordinator = coordinator
        self._entry = entry
        self._attr_device_info = device

    async def _persist(self, conf_key: str, value: float) -> None:
        """Write a value back to config entry options and reload coordinator."""
        options = {**self._entry.options, conf_key: value}
        self.hass.config_entries.async_update_entry(self._entry, options=options)
        self._coordinator.reload_config()
        self.async_write_ha_state()


class ZGCDeadbandNumber(_ZGCNumberBase):
    """Deadband — grid error below this is ignored."""

    _attr_translation_key = "deadband_w"
    _attr_native_min_value = DEADBAND_MIN_W
    _attr_native_max_value = DEADBAND_MAX_W
    _attr_native_step = DEADBAND_STEP_W
    _attr_native_unit_of_measurement = "W"

    def __init__(
        self, coordinator: ZeroGridCoordinator, entry: ConfigEntry, device: DeviceInfo
    ) -> None:
        super().__init__(coordinator, entry, device)
        self._attr_unique_id = f"{entry.entry_id}_deadband_w"

    @property
    def native_value(self) -> float:
        data = {**self._entry.data, **self._entry.options}
        return float(data.get(CONF_DEADBAND_W, DEFAULT_DEADBAND_W))

    async def async_set_native_value(self, value: float) -> None:
        await self._persist(CONF_DEADBAND_W, value)


class ZGCFilterAlphaNumber(_ZGCNumberBase):
    """EWM filter smoothing factor (0.05 = heavy smoothing, 1.0 = no filter)."""

    _attr_translation_key = "ewm_alpha"
    _attr_native_min_value = EWM_ALPHA_MIN
    _attr_native_max_value = EWM_ALPHA_MAX
    _attr_native_step = EWM_ALPHA_STEP

    def __init__(
        self, coordinator: ZeroGridCoordinator, entry: ConfigEntry, device: DeviceInfo
    ) -> None:
        super().__init__(coordinator, entry, device)
        self._attr_unique_id = f"{entry.entry_id}_ewm_alpha"

    @property
    def native_value(self) -> float:
        data = {**self._entry.data, **self._entry.options}
        return float(data.get(CONF_EWM_ALPHA, DEFAULT_EWM_ALPHA))

    async def async_set_native_value(self, value: float) -> None:
        await self._persist(CONF_EWM_ALPHA, value)
