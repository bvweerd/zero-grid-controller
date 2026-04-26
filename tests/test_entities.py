"""Tests for sensor and number entities."""

from __future__ import annotations

import pytest
from homeassistant.helpers import entity_registry as er
from pytest_homeassistant_custom_component.common import MockConfigEntry

from custom_components.zero_grid_controller.const import DOMAIN


@pytest.fixture(autouse=True)
def auto_enable_custom_integrations(enable_custom_integrations):
    return


async def _setup(hass):
    entry = MockConfigEntry(
        domain=DOMAIN,
        title="Test ZGC",
        data={
            "name": "Test ZGC",
            "grid_import_sensors": ["sensor.grid_import"],
            "grid_export_sensors": ["sensor.grid_export"],
            "deadband_w": 20.0,
            "ewm_alpha": 1.0,
        },
        options={},
    )
    entry.add_to_hass(hass)
    hass.states.async_set("sensor.grid_import", "0")
    hass.states.async_set("sensor.grid_export", "0")
    await hass.config_entries.async_setup(entry.entry_id)
    await hass.async_block_till_done()
    return entry


async def test_sensors_registered(hass):
    await _setup(hass)
    # grid_raw and pid_output are disabled by default; count via entity registry.
    registry = er.async_get(hass)
    sensor_entries = [
        e
        for e in registry.entities.values()
        if e.platform == DOMAIN and e.domain == "sensor"
    ]
    assert len(sensor_entries) >= 4  # grid_raw, grid_filtered, pid_output, status


async def test_numbers_registered(hass):
    await _setup(hass)
    # Number entities are disabled by default; check the entity registry, not states.
    registry = er.async_get(hass)
    number_entries = [
        e
        for e in registry.entities.values()
        if e.platform == DOMAIN and e.domain == "number"
    ]
    assert len(number_entries) >= 2  # deadband, ewm_alpha


async def test_buttons_registered(hass):
    await _setup(hass)
    all_states = hass.states.async_all("button")
    assert len(all_states) >= 2  # reset_pid, recalibrate
