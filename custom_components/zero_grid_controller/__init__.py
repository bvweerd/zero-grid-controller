"""Zero Grid Controller — Home Assistant custom integration.

Keeps net grid power close to 0 W by controlling PV inverter output limits
via a self-tuning PID controller with online RLS parameter estimation.
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

from .calibrator import ArrayCalibrator
from .const import (
    ARRAY_SUBENTRY_TYPE,
    BATTERY_SUBENTRY_TYPE,
    CONF_NAME,
    DOMAIN,
    PLATFORMS,
    SERVICE_OVERRIDE_SETPOINT,
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
        manufacturer="Custom",
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
                manufacturer="Custom",
                model="PV Array",
                via_device=(DOMAIN, entry.entry_id),
            )
        elif subentry.subentry_type == BATTERY_SUBENTRY_TYPE:
            battery_name = subentry.data.get("name", subentry.title)
            battery_devices[subentry.subentry_id] = DeviceInfo(
                identifiers={(DOMAIN, subentry.subentry_id)},
                name=battery_name,
                manufacturer="Custom",
                model="Battery",
                via_device=(DOMAIN, entry.entry_id),
            )

    entry.runtime_data = ZGCData(
        coordinator=coordinator,
        device=main_device,
        array_devices=array_devices,
        battery_devices=battery_devices,
    )

    # Register services (once per domain)
    _register_services(hass)

    # Register options update listener
    entry.async_on_unload(entry.add_update_listener(_async_update_listener))

    # Forward setup to platforms
    await hass.config_entries.async_forward_entry_setups(entry, PLATFORMS)

    # Start coordinator after platforms are set up
    await coordinator.async_config_entry_first_refresh()

    _LOGGER.debug("Zero Grid Controller entry %s ready", entry.entry_id)
    return True


async def async_unload_entry(hass: HomeAssistant, entry: ConfigEntry) -> bool:
    """Unload a config entry."""
    _LOGGER.info("Unloading Zero Grid Controller entry %s", entry.entry_id)
    unload_ok = await hass.config_entries.async_unload_platforms(entry, PLATFORMS)
    if unload_ok:
        entry.runtime_data = None
        remaining = hass.config_entries.async_entries(DOMAIN)
        if not any(e.entry_id != entry.entry_id for e in remaining):
            for service in (
                SERVICE_RESET_PID,
                SERVICE_RECALIBRATE,
                SERVICE_OVERRIDE_SETPOINT,
            ):
                hass.services.async_remove(DOMAIN, service)
    return unload_ok


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
            return False  # Main device — cannot remove while active
        if device_id in config_entry.subentries:
            return False  # Active subentry device
    return True  # Stale device — allow removal


async def _async_update_listener(hass: HomeAssistant, entry: ConfigEntry) -> None:
    """Reload integration on structural config changes."""
    if entry.runtime_data is None:
        return
    _LOGGER.debug("Config entry %s updated, reloading", entry.entry_id)
    await hass.config_entries.async_reload(entry.entry_id)


def _register_services(hass: HomeAssistant) -> None:
    """Register domain services (idempotent — skip if already registered)."""

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
        array_name: str | None = call.data.get("array_name")
        for entry in hass.config_entries.async_entries(DOMAIN):
            if entry.runtime_data is None:
                continue
            coordinator: ZeroGridCoordinator = entry.runtime_data.coordinator
            arrays = [
                a
                for a in coordinator.arrays
                if array_name is None or a.name == array_name
            ]
            if not arrays:
                continue
            calibrator = ArrayCalibrator()
            hass.async_create_task(
                calibrator.run(
                    hass,
                    arrays,
                    coordinator.read_grid_w,
                    lambda msg, pct: _LOGGER.debug(
                        "Calibration: %s (%.0f%%)", msg, pct * 100
                    ),
                )
            )

    hass.services.async_register(
        DOMAIN,
        SERVICE_RECALIBRATE,
        _handle_recalibrate,
        schema=vol.Schema({vol.Optional("array_name"): str}),
    )

    async def _handle_override_setpoint(call: ServiceCall) -> None:
        array_name: str = call.data["array_name"]
        value: float = float(call.data["value"])
        for entry in hass.config_entries.async_entries(DOMAIN):
            if entry.runtime_data:
                entry.runtime_data.coordinator.override_setpoint(array_name, value)

    hass.services.async_register(
        DOMAIN,
        SERVICE_OVERRIDE_SETPOINT,
        _handle_override_setpoint,
        schema=vol.Schema(
            {
                vol.Required("array_name"): str,
                vol.Required("value"): vol.Coerce(float),
            }
        ),
    )
