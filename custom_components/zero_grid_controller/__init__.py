"""Zero Grid Controller — Home Assistant custom integration.

Keeps net grid power close to 0 W by controlling PV inverter output limits
via a self-tuning PID controller.
"""

from __future__ import annotations

import json
import logging
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import voluptuous as vol
from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant, ServiceCall
from homeassistant.helpers import config_validation as cv
from homeassistant.helpers import device_registry as dr
from homeassistant.helpers.device_registry import DeviceInfo

from .const import (
    ARRAY_SUBENTRY_TYPE,
    BATTERY_SUBENTRY_TYPE,
    CONF_NAME,
    DOMAIN,
    PLATFORMS,
    SERVICE_RECALIBRATE,
    SERVICE_RESET_PID,
)
from .coordinator import ZeroGridCoordinator

_LOGGER = logging.getLogger(__name__)

CONFIG_SCHEMA = cv.config_entry_only_config_schema(DOMAIN)

_MANIFEST: dict[str, Any] = json.loads(
    (Path(__file__).parent / "manifest.json").read_text(encoding="utf-8")
)


@dataclass
class ZGCData:
    """Runtime data stored on the config entry."""

    coordinator: ZeroGridCoordinator
    device: DeviceInfo
    array_devices: dict[str, DeviceInfo] = field(default_factory=dict)
    battery_devices: dict[str, DeviceInfo] = field(default_factory=dict)


async def async_setup_entry(hass: HomeAssistant, entry: ConfigEntry) -> bool:
    """Set up Zero Grid Controller from a config entry."""
    _LOGGER.info("Setting up Zero Grid Controller entry %s", entry.entry_id)

    name = entry.data.get(CONF_NAME, entry.title)
    version = _MANIFEST.get("version", "unknown")

    main_device = DeviceInfo(
        identifiers={(DOMAIN, entry.entry_id)},
        name=name,
        manufacturer="bvweerd",
        model="Zero Grid Controller",
        sw_version=version,
    )

    coordinator = ZeroGridCoordinator(hass, entry)

    array_devices: dict[str, DeviceInfo] = {}
    battery_devices: dict[str, DeviceInfo] = {}

    for subentry in entry.subentries.values():
        if subentry.subentry_type == ARRAY_SUBENTRY_TYPE:
            array_name = subentry.data.get("array_name", subentry.subentry_id)
            array_devices[subentry.subentry_id] = DeviceInfo(
                identifiers={(DOMAIN, subentry.subentry_id)},
                name=array_name,
                manufacturer="bvweerd",
                model="PV Array",
                via_device=(DOMAIN, entry.entry_id),
            )
        elif subentry.subentry_type == BATTERY_SUBENTRY_TYPE:
            battery_name = subentry.data.get("name", subentry.title)
            battery_devices[subentry.subentry_id] = DeviceInfo(
                identifiers={(DOMAIN, subentry.subentry_id)},
                name=battery_name,
                manufacturer="bvweerd",
                model="Battery",
                via_device=(DOMAIN, entry.entry_id),
            )

    entry.runtime_data = ZGCData(
        coordinator=coordinator,
        device=main_device,
        array_devices=array_devices,
        battery_devices=battery_devices,
    )

    entry.async_on_unload(entry.add_update_listener(_async_update_listener))

    await coordinator.async_config_entry_first_refresh()
    await hass.config_entries.async_forward_entry_setups(entry, PLATFORMS)

    _LOGGER.debug("Zero Grid Controller entry %s ready", entry.entry_id)
    return True


async def async_unload_entry(hass: HomeAssistant, entry: ConfigEntry) -> bool:
    """Unload a config entry."""
    _LOGGER.info("Unloading Zero Grid Controller entry %s", entry.entry_id)
    if entry.runtime_data is not None:
        entry.runtime_data.coordinator.abort_calibration()
    unload_ok = await hass.config_entries.async_unload_platforms(entry, PLATFORMS)
    if unload_ok:
        entry.runtime_data = None
    return unload_ok


async def async_setup(hass: HomeAssistant, config: dict[str, Any]) -> bool:
    """Set up the integration domain."""
    _register_services(hass)
    return True


async def async_migrate_entry(hass: HomeAssistant, config_entry: ConfigEntry) -> bool:
    """Migrate config entries to the current schema version."""
    return True


async def async_remove_config_entry_device(
    hass: HomeAssistant,
    config_entry: ConfigEntry,
    device_entry: dr.DeviceEntry,
) -> bool:
    """Allow removal of stale subentry devices."""
    for identifier in device_entry.identifiers:
        if identifier[0] != DOMAIN:
            continue
        device_id = identifier[1]
        if device_id == config_entry.entry_id:
            return False
        if device_id in config_entry.subentries:
            return False
    return True


async def _async_update_listener(hass: HomeAssistant, entry: ConfigEntry) -> None:
    """Reload integration when config/options change."""
    if entry.runtime_data is None:
        await hass.config_entries.async_reload(entry.entry_id)
        return

    # When subentries are added or removed the device and entity registry need
    # to be updated, which requires a full platform reload.  For plain options
    # changes a lightweight coordinator reload is sufficient.
    current_ids = frozenset(entry.subentries)
    known_ids = frozenset(entry.runtime_data.array_devices) | frozenset(
        entry.runtime_data.battery_devices
    )
    if current_ids != known_ids:
        await hass.config_entries.async_reload(entry.entry_id)
        return

    entry.runtime_data.coordinator.reload_config()


def _register_services(hass: HomeAssistant) -> None:
    """Register domain services (idempotent)."""
    if hass.services.has_service(DOMAIN, SERVICE_RESET_PID):
        return

    async def _handle_reset_pid(call: ServiceCall) -> None:
        entry_id = call.data.get("entry_id")
        for entry in hass.config_entries.async_entries(DOMAIN):
            if entry_id and entry.entry_id != entry_id:
                continue
            if entry.runtime_data:
                entry.runtime_data.coordinator.reset_pid()

    hass.services.async_register(
        DOMAIN,
        SERVICE_RESET_PID,
        _handle_reset_pid,
        schema=vol.Schema({vol.Optional("entry_id"): str}),
    )

    async def _handle_recalibrate(call: ServiceCall) -> None:
        entry_id = call.data.get("entry_id")
        for entry in hass.config_entries.async_entries(DOMAIN):
            if entry_id and entry.entry_id != entry_id:
                continue
            if entry.runtime_data is None:
                continue
            hass.async_create_task(entry.runtime_data.coordinator.start_calibration())

    hass.services.async_register(
        DOMAIN,
        SERVICE_RECALIBRATE,
        _handle_recalibrate,
        schema=vol.Schema({vol.Optional("entry_id"): str}),
    )
