"""Number platform for Zero Grid Controller — expert mode tuning parameters."""

from __future__ import annotations

import logging

from homeassistant.components.number import NumberEntity, NumberMode
from homeassistant.config_entries import ConfigEntry
from homeassistant.const import EntityCategory
from homeassistant.core import HomeAssistant
from homeassistant.helpers.device_registry import DeviceInfo
from homeassistant.helpers.entity_platform import AddEntitiesCallback

from .const import (
    ARRAY_SUBENTRY_TYPE,
    CONF_DEADBAND_W,
    CONF_EWM_ALPHA,
    CONF_EXPERT_MODE,
    CONF_KD,
    CONF_KI,
    CONF_KP,
    CONF_OUTPUT_MAX_W,
    CONF_SETTLING_TIME_S,
    CONF_W_PER_UNIT,
    DEADBAND_MAX_W,
    DEADBAND_MIN_W,
    DEADBAND_STEP_W,
    DEFAULT_DEADBAND_W,
    DEFAULT_EWM_ALPHA,
    DEFAULT_KD,
    DEFAULT_KI,
    DEFAULT_KP,
    DEFAULT_OUTPUT_MAX_W,
    DEFAULT_SETTLING_TIME_S,
    DEFAULT_W_PER_UNIT,
    EWM_ALPHA_MAX,
    EWM_ALPHA_MIN,
    EWM_ALPHA_STEP,
    KD_MAX,
    KD_MIN,
    KD_STEP,
    KI_MAX,
    KI_MIN,
    KI_STEP,
    KP_MAX,
    KP_MIN,
    KP_STEP,
    OUTPUT_MAX_MAX_W,
    OUTPUT_MAX_MIN_W,
    OUTPUT_MAX_STEP_W,
    SETTLING_TIME_MAX_S,
    SETTLING_TIME_MIN_S,
    SETTLING_TIME_STEP_S,
    W_PER_UNIT_MAX,
    W_PER_UNIT_MIN,
    W_PER_UNIT_STEP,
)
from .coordinator import ZeroGridCoordinator

_LOGGER = logging.getLogger(__name__)

PARALLEL_UPDATES = 0


async def async_setup_entry(
    hass: HomeAssistant,
    entry: ConfigEntry,
    async_add_entities: AddEntitiesCallback,
) -> None:
    """Set up number entities (expert mode parameters)."""
    coordinator: ZeroGridCoordinator = entry.runtime_data.coordinator
    main_device: DeviceInfo = entry.runtime_data.device
    array_devices: dict[str, DeviceInfo] = entry.runtime_data.array_devices

    entities: list[NumberEntity] = [
        ZGCKpNumber(coordinator, entry, main_device),
        ZGCKiNumber(coordinator, entry, main_device),
        ZGCKdNumber(coordinator, entry, main_device),
        ZGCEwmAlphaNumber(coordinator, entry, main_device),
        ZGCDeadbandNumber(coordinator, entry, main_device),
        ZGCOutputMaxNumber(coordinator, entry, main_device),
    ]

    for subentry in entry.subentries.values():
        if subentry.subentry_type != ARRAY_SUBENTRY_TYPE:
            continue
        array_name = subentry.data.get("array_name", subentry.subentry_id)
        device = array_devices.get(subentry.subentry_id)
        if device is None:
            continue
        entities += [
            ZGCArraySettlingTimeNumber(coordinator, entry, device, array_name),
            ZGCArrayWPerUnitNumber(coordinator, entry, device, array_name),
        ]

    async_add_entities(entities)


# ---------------------------------------------------------------------------
# Base class
# ---------------------------------------------------------------------------


class ZGCNumberBase(NumberEntity):
    """Base class for ZGC number entities."""

    _attr_has_entity_name = True
    _attr_entity_category = EntityCategory.CONFIG
    _attr_mode = NumberMode.BOX

    def __init__(
        self,
        coordinator: ZeroGridCoordinator,
        entry: ConfigEntry,
        device: DeviceInfo,
        key: str,
        default: float,
    ) -> None:
        self._coordinator = coordinator
        self._entry = entry
        self._key = key
        self._default = default
        self._attr_unique_id = f"{entry.entry_id}_{key}"
        self._attr_device_info = device
        self._attr_translation_key = key

    @property
    def entity_registry_enabled_default(self) -> bool:
        """Show only in expert mode."""
        return bool(self._entry.options.get(CONF_EXPERT_MODE, False))

    @property
    def native_value(self) -> float:
        return float(
            self._entry.options.get(
                self._key, self._entry.data.get(self._key, self._default)
            )
        )

    async def async_set_native_value(self, value: float) -> None:
        """Persist the new value and notify the coordinator."""
        self.hass.config_entries.async_update_entry(
            self._entry,
            options={**self._entry.options, self._key: value},
        )
        await self._on_value_changed(value)
        self.async_write_ha_state()

    async def _on_value_changed(self, value: float) -> None:
        """Hook for subclasses to propagate changes to the coordinator."""


# ---------------------------------------------------------------------------
# Main device number entities
# ---------------------------------------------------------------------------


class ZGCKpNumber(ZGCNumberBase):
    _attr_native_min_value = KP_MIN
    _attr_native_max_value = KP_MAX
    _attr_native_step = KP_STEP

    def __init__(
        self, coordinator: ZeroGridCoordinator, entry: ConfigEntry, device: DeviceInfo
    ) -> None:
        super().__init__(coordinator, entry, device, CONF_KP, DEFAULT_KP)

    async def _on_value_changed(self, value: float) -> None:
        self._coordinator.pid.set_gains(
            value, self._coordinator.pid.ki, self._coordinator.pid.kd
        )


class ZGCKiNumber(ZGCNumberBase):
    _attr_native_min_value = KI_MIN
    _attr_native_max_value = KI_MAX
    _attr_native_step = KI_STEP

    def __init__(
        self, coordinator: ZeroGridCoordinator, entry: ConfigEntry, device: DeviceInfo
    ) -> None:
        super().__init__(coordinator, entry, device, CONF_KI, DEFAULT_KI)

    async def _on_value_changed(self, value: float) -> None:
        self._coordinator.pid.set_gains(
            self._coordinator.pid.kp, value, self._coordinator.pid.kd
        )


class ZGCKdNumber(ZGCNumberBase):
    _attr_native_min_value = KD_MIN
    _attr_native_max_value = KD_MAX
    _attr_native_step = KD_STEP

    def __init__(
        self, coordinator: ZeroGridCoordinator, entry: ConfigEntry, device: DeviceInfo
    ) -> None:
        super().__init__(coordinator, entry, device, CONF_KD, DEFAULT_KD)

    async def _on_value_changed(self, value: float) -> None:
        self._coordinator.pid.set_gains(
            self._coordinator.pid.kp, self._coordinator.pid.ki, value
        )


class ZGCEwmAlphaNumber(ZGCNumberBase):
    _attr_native_min_value = EWM_ALPHA_MIN
    _attr_native_max_value = EWM_ALPHA_MAX
    _attr_native_step = EWM_ALPHA_STEP

    def __init__(
        self, coordinator: ZeroGridCoordinator, entry: ConfigEntry, device: DeviceInfo
    ) -> None:
        super().__init__(coordinator, entry, device, CONF_EWM_ALPHA, DEFAULT_EWM_ALPHA)

    async def _on_value_changed(self, value: float) -> None:
        self._coordinator.set_ewm_alpha(value)


class ZGCDeadbandNumber(ZGCNumberBase):
    _attr_native_min_value = DEADBAND_MIN_W
    _attr_native_max_value = DEADBAND_MAX_W
    _attr_native_step = DEADBAND_STEP_W

    def __init__(
        self, coordinator: ZeroGridCoordinator, entry: ConfigEntry, device: DeviceInfo
    ) -> None:
        super().__init__(
            coordinator, entry, device, CONF_DEADBAND_W, DEFAULT_DEADBAND_W
        )

    async def _on_value_changed(self, value: float) -> None:
        self._coordinator.set_deadband(value)


class ZGCOutputMaxNumber(ZGCNumberBase):
    _attr_native_min_value = OUTPUT_MAX_MIN_W
    _attr_native_max_value = OUTPUT_MAX_MAX_W
    _attr_native_step = OUTPUT_MAX_STEP_W

    def __init__(
        self, coordinator: ZeroGridCoordinator, entry: ConfigEntry, device: DeviceInfo
    ) -> None:
        super().__init__(
            coordinator, entry, device, CONF_OUTPUT_MAX_W, DEFAULT_OUTPUT_MAX_W
        )

    async def _on_value_changed(self, value: float) -> None:
        self._coordinator.pid.set_output_limits(-value, value)


# ---------------------------------------------------------------------------
# Per-array number entities
# ---------------------------------------------------------------------------


class ZGCArrayNumberBase(ZGCNumberBase):
    """Base for per-array number entities."""

    def __init__(
        self,
        coordinator: ZeroGridCoordinator,
        entry: ConfigEntry,
        device: DeviceInfo,
        key: str,
        default: float,
        array_name: str,
    ) -> None:
        super().__init__(coordinator, entry, device, f"{array_name}_{key}", default)
        self._array_name = array_name
        self._param_key = key
        self._attr_translation_key = key


class ZGCArraySettlingTimeNumber(ZGCArrayNumberBase):
    _attr_native_min_value = SETTLING_TIME_MIN_S
    _attr_native_max_value = SETTLING_TIME_MAX_S
    _attr_native_step = SETTLING_TIME_STEP_S

    def __init__(
        self,
        coordinator: ZeroGridCoordinator,
        entry: ConfigEntry,
        device: DeviceInfo,
        array_name: str,
    ) -> None:
        super().__init__(
            coordinator,
            entry,
            device,
            CONF_SETTLING_TIME_S,
            float(DEFAULT_SETTLING_TIME_S),
            array_name,
        )

    async def _on_value_changed(self, value: float) -> None:
        self._coordinator.apply_array_config_update(
            self._array_name, {CONF_SETTLING_TIME_S: int(value)}
        )


class ZGCArrayWPerUnitNumber(ZGCArrayNumberBase):
    _attr_native_min_value = W_PER_UNIT_MIN
    _attr_native_max_value = W_PER_UNIT_MAX
    _attr_native_step = W_PER_UNIT_STEP

    def __init__(
        self,
        coordinator: ZeroGridCoordinator,
        entry: ConfigEntry,
        device: DeviceInfo,
        array_name: str,
    ) -> None:
        super().__init__(
            coordinator, entry, device, CONF_W_PER_UNIT, DEFAULT_W_PER_UNIT, array_name
        )

    async def _on_value_changed(self, value: float) -> None:
        self._coordinator.apply_array_config_update(
            self._array_name, {CONF_W_PER_UNIT: value}
        )
