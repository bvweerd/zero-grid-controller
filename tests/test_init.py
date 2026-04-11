"""Tests for integration setup and teardown."""

from __future__ import annotations

from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from pytest_homeassistant_custom_component.common import MockConfigEntry

from custom_components.zero_grid_controller import (
    _async_update_listener,
    _register_services,
    async_remove_config_entry_device,
    async_setup,
)
from custom_components.zero_grid_controller.const import (
    ARRAY_SUBENTRY_TYPE,
    BATTERY_SUBENTRY_TYPE,
    DOMAIN,
    SERVICE_RECALIBRATE,
    SERVICE_RESET_PID,
)


@pytest.fixture(autouse=True)
def auto_enable_custom_integrations(enable_custom_integrations):
    return


async def test_setup_entry(hass):
    entry = MockConfigEntry(
        domain=DOMAIN,
        title="Test ZGC",
        data={
            "name": "Test ZGC",
            "grid_import_sensors": ["sensor.grid_import"],
            "grid_export_sensors": ["sensor.grid_export"],
        },
        options={},
    )
    entry.add_to_hass(hass)
    hass.states.async_set("sensor.grid_import", "0")
    hass.states.async_set("sensor.grid_export", "0")
    result = await hass.config_entries.async_setup(entry.entry_id)
    assert result is True
    assert entry.runtime_data is not None
    assert entry.runtime_data.coordinator is not None


async def test_unload_entry(hass):
    entry = MockConfigEntry(
        domain=DOMAIN,
        title="Test ZGC",
        data={
            "name": "Test ZGC",
            "grid_import_sensors": ["sensor.grid_import"],
            "grid_export_sensors": ["sensor.grid_export"],
        },
        options={},
    )
    entry.add_to_hass(hass)
    hass.states.async_set("sensor.grid_import", "0")
    hass.states.async_set("sensor.grid_export", "0")
    await hass.config_entries.async_setup(entry.entry_id)
    result = await hass.config_entries.async_unload(entry.entry_id)
    assert result is True


def _entry_with_subentries(title: str = "Test ZGC") -> MockConfigEntry:
    return MockConfigEntry(
        domain=DOMAIN,
        title=title,
        data={
            "name": title,
            "grid_import_sensors": ["sensor.grid_import"],
            "grid_export_sensors": ["sensor.grid_export"],
        },
        options={},
        subentries_data=(
            {
                "subentry_id": "array-1",
                "subentry_type": ARRAY_SUBENTRY_TYPE,
                "title": "Solar",
                "data": {
                    "array_name": "Solar",
                    "output_type": "percent",
                    "setpoint_entity": "number.solar_limit",
                    "setpoint_min": 0.0,
                    "setpoint_max": 100.0,
                },
            },
            {
                "subentry_id": "battery-1",
                "subentry_type": BATTERY_SUBENTRY_TYPE,
                "title": "Battery",
                "data": {
                    "name": "Battery",
                    "battery_sensor": "sensor.battery_power",
                    "battery_max_charge_w": 4000.0,
                    "battery_max_discharge_w": 5000.0,
                    "battery_setpoint_entity": "number.battery_limit",
                },
            },
        ),
    )


async def test_setup_entry_creates_subentry_devices_and_services(hass):
    entry = _entry_with_subentries()
    entry.add_to_hass(hass)
    hass.states.async_set("sensor.grid_import", "0")
    hass.states.async_set("sensor.grid_export", "0")

    result = await hass.config_entries.async_setup(entry.entry_id)

    assert result is True
    assert "array-1" in entry.runtime_data.array_devices
    assert "battery-1" in entry.runtime_data.battery_devices
    assert hass.services.has_service(DOMAIN, SERVICE_RESET_PID)
    assert hass.services.has_service(DOMAIN, SERVICE_RECALIBRATE)

    _register_services(hass)
    assert hass.services.has_service(DOMAIN, SERVICE_RESET_PID)


async def test_reset_pid_service_honors_entry_filter(hass):
    first = _entry_with_subentries("First")
    second = _entry_with_subentries("Second")
    first.add_to_hass(hass)
    second.add_to_hass(hass)
    for sensor in ("sensor.grid_import", "sensor.grid_export"):
        hass.states.async_set(sensor, "0")
    await hass.config_entries.async_setup(first.entry_id)
    if second.state.name == "NOT_LOADED":
        await hass.config_entries.async_setup(second.entry_id)

    first.runtime_data.coordinator._pid.set_integral(12.0)
    second.runtime_data.coordinator._pid.set_integral(15.0)

    await hass.services.async_call(
        DOMAIN,
        SERVICE_RESET_PID,
        {"entry_id": first.entry_id},
        blocking=True,
    )

    assert first.runtime_data.coordinator._pid.integral == 0.0
    assert second.runtime_data.coordinator._pid.integral == 15.0


async def test_recalibrate_service_starts_task_for_matching_entry(hass):
    first = _entry_with_subentries("First")
    second = _entry_with_subentries("Second")
    first.add_to_hass(hass)
    second.add_to_hass(hass)
    for sensor in ("sensor.grid_import", "sensor.grid_export"):
        hass.states.async_set(sensor, "0")
    await hass.config_entries.async_setup(first.entry_id)
    if second.state.name == "NOT_LOADED":
        await hass.config_entries.async_setup(second.entry_id)

    first.runtime_data.coordinator.start_calibration = AsyncMock(return_value=[])
    second.runtime_data.coordinator.start_calibration = AsyncMock(return_value=[])

    await hass.services.async_call(
        DOMAIN,
        SERVICE_RECALIBRATE,
        {"entry_id": second.entry_id},
        blocking=True,
    )
    await hass.async_block_till_done()

    first.runtime_data.coordinator.start_calibration.assert_not_awaited()
    second.runtime_data.coordinator.start_calibration.assert_awaited_once()


async def test_update_listener_reloads_or_refreshes_runtime(hass):
    entry = _entry_with_subentries()
    entry.add_to_hass(hass)
    runtime_data = SimpleNamespace(coordinator=MagicMock())
    entry.runtime_data = runtime_data

    await _async_update_listener(hass, entry)
    runtime_data.coordinator.reload_config.assert_called_once()

    entry.runtime_data = None
    with patch.object(
        hass.config_entries, "async_reload", new=AsyncMock()
    ) as mock_reload:
        await _async_update_listener(hass, entry)
    mock_reload.assert_awaited_once_with(entry.entry_id)


async def test_remove_config_entry_device_rejects_active_devices(hass):
    entry = _entry_with_subentries()

    main_device = SimpleNamespace(identifiers={(DOMAIN, entry.entry_id)})
    active_subentry_device = SimpleNamespace(identifiers={(DOMAIN, "array-1")})
    stale_device = SimpleNamespace(identifiers={(DOMAIN, "stale")})

    assert await async_remove_config_entry_device(hass, entry, main_device) is False
    assert (
        await async_remove_config_entry_device(hass, entry, active_subentry_device)
        is False
    )
    assert await async_remove_config_entry_device(hass, entry, stale_device) is True


async def test_remove_config_entry_device_ignores_other_domains(hass):
    entry = _entry_with_subentries()
    foreign_device = SimpleNamespace(identifiers={("light", "lamp-1")})

    assert await async_remove_config_entry_device(hass, entry, foreign_device) is True


async def test_unload_only_removes_services_for_last_entry(hass):
    first = _entry_with_subentries("First")
    second = _entry_with_subentries("Second")
    first.add_to_hass(hass)
    second.add_to_hass(hass)
    for sensor in ("sensor.grid_import", "sensor.grid_export"):
        hass.states.async_set(sensor, "0")

    await hass.config_entries.async_setup(first.entry_id)
    if second.state.name == "NOT_LOADED":
        await hass.config_entries.async_setup(second.entry_id)

    assert hass.services.has_service(DOMAIN, SERVICE_RESET_PID)
    await hass.config_entries.async_unload(first.entry_id)
    assert hass.services.has_service(DOMAIN, SERVICE_RESET_PID)

    await hass.config_entries.async_unload(second.entry_id)
    assert hass.services.has_service(DOMAIN, SERVICE_RESET_PID)


async def test_async_setup_registers_services_once(hass):
    assert await async_setup(hass, {}) is True
    assert hass.services.has_service(DOMAIN, SERVICE_RESET_PID)

    assert await async_setup(hass, {}) is True
    assert hass.services.has_service(DOMAIN, SERVICE_RECALIBRATE)


async def test_async_migrate_entry_returns_true(hass):
    from custom_components.zero_grid_controller import async_migrate_entry

    entry = _entry_with_subentries()

    assert await async_migrate_entry(hass, entry) is True


async def test_recalibrate_service_skips_entry_without_runtime_data(hass):
    entry = _entry_with_subentries()
    entry.add_to_hass(hass)
    entry.runtime_data = None

    await async_setup(hass, {})
    await hass.services.async_call(
        DOMAIN,
        SERVICE_RECALIBRATE,
        {"entry_id": entry.entry_id},
        blocking=True,
    )
