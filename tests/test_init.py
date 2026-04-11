"""Tests for integration setup and teardown."""

from __future__ import annotations

import pytest
from pytest_homeassistant_custom_component.common import MockConfigEntry

from custom_components.zero_grid_controller.const import DOMAIN


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
