"""Tests for ZeroGridCoordinator control loop."""

from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock, patch
from datetime import timedelta

import pytest
from pytest_homeassistant_custom_component.common import MockConfigEntry

from custom_components.zero_grid_controller.const import (
    ARRAY_SUBENTRY_TYPE,
    BATTERY_SUBENTRY_TYPE,
    DOMAIN,
    STATUS_ACTIVE,
    STATUS_DEADBAND,
    STATUS_DISABLED,
)
from custom_components.zero_grid_controller.coordinator import ZeroGridCoordinator


@pytest.fixture(autouse=True)
def auto_enable_custom_integrations(enable_custom_integrations):
    return


def _make_entry(
    import_sensors=None,
    export_sensors=None,
    deadband_w=20.0,
    ewm_alpha=1.0,  # no smoothing for tests
    kp=1.0,
    ki=0.0,
    kd=0.0,
    controller_enabled=True,
    subentries=None,
):
    data = {
        "name": "Test ZGC",
        "grid_import_sensors": import_sensors or ["sensor.grid_import"],
        "grid_export_sensors": export_sensors or ["sensor.grid_export"],
        "deadband_w": deadband_w,
        "ewm_alpha": ewm_alpha,
        "kp": kp,
        "ki": ki,
        "kd": kd,
        "controller_enabled": controller_enabled,
    }
    entry = MockConfigEntry(domain=DOMAIN, title="Test", data=data, options={})
    if subentries:
        # MockConfigEntry uses a dict for subentries
        entry._subentries = subentries
    return entry


def _set_state(hass, entity_id, value):
    hass.states.async_set(entity_id, str(value))


async def _run_once(coordinator):
    """Trigger one coordinator update and return the result."""
    await coordinator._async_update_data()
    return coordinator.data


async def test_grid_unavailable_returns_disabled(hass):
    entry = _make_entry()
    entry.add_to_hass(hass)
    coordinator = ZeroGridCoordinator(hass, entry)
    # Don't set grid sensor states → unavailable
    result = await coordinator._async_update_data()
    assert result.status == STATUS_DISABLED


async def test_grid_zero_within_deadband(hass):
    entry = _make_entry(deadband_w=20.0, ewm_alpha=1.0)
    entry.add_to_hass(hass)
    coordinator = ZeroGridCoordinator(hass, entry)
    _set_state(hass, "sensor.grid_import", 0)
    _set_state(hass, "sensor.grid_export", 0)
    result = await coordinator._async_update_data()
    assert result.status == STATUS_DEADBAND


async def test_grid_small_error_within_deadband(hass):
    entry = _make_entry(deadband_w=20.0, ewm_alpha=1.0)
    entry.add_to_hass(hass)
    coordinator = ZeroGridCoordinator(hass, entry)
    _set_state(hass, "sensor.grid_import", 10)
    _set_state(hass, "sensor.grid_export", 0)
    result = await coordinator._async_update_data()
    assert result.status == STATUS_DEADBAND


async def test_grid_above_deadband_returns_active(hass):
    entry = _make_entry(deadband_w=20.0, ewm_alpha=1.0)
    entry.add_to_hass(hass)
    coordinator = ZeroGridCoordinator(hass, entry)
    _set_state(hass, "sensor.grid_import", 100)
    _set_state(hass, "sensor.grid_export", 0)
    result = await coordinator._async_update_data()
    assert result.status == STATUS_ACTIVE


async def test_controller_disabled_returns_disabled(hass):
    entry = _make_entry(controller_enabled=False)
    entry.add_to_hass(hass)
    coordinator = ZeroGridCoordinator(hass, entry)
    _set_state(hass, "sensor.grid_import", 100)
    _set_state(hass, "sensor.grid_export", 0)
    result = await coordinator._async_update_data()
    assert result.status == STATUS_DISABLED


async def test_controller_enabled_allows_control(hass):
    entry = _make_entry(controller_enabled=True, deadband_w=20.0, ewm_alpha=1.0)
    entry.add_to_hass(hass)
    coordinator = ZeroGridCoordinator(hass, entry)
    _set_state(hass, "sensor.grid_import", 100)
    _set_state(hass, "sensor.grid_export", 0)
    result = await coordinator._async_update_data()
    assert result.status == STATUS_ACTIVE


async def test_grid_reading_import_minus_export(hass):
    entry = _make_entry(deadband_w=5.0, ewm_alpha=1.0)
    entry.add_to_hass(hass)
    coordinator = ZeroGridCoordinator(hass, entry)
    _set_state(hass, "sensor.grid_import", 300)
    _set_state(hass, "sensor.grid_export", 100)
    result = await coordinator._async_update_data()
    assert result.grid_raw_w == pytest.approx(200.0)


async def test_reload_config_preserves_pid_integral(hass):
    entry = _make_entry(kp=1.0, ki=0.1, deadband_w=5.0, ewm_alpha=1.0)
    entry.add_to_hass(hass)
    coordinator = ZeroGridCoordinator(hass, entry)
    # Force some integral accumulation
    coordinator._pid.set_integral(42.0)
    coordinator._filtered_w = 100.0
    coordinator.reload_config()
    assert coordinator._pid.integral == pytest.approx(42.0)
    assert coordinator._filtered_w == pytest.approx(100.0)
