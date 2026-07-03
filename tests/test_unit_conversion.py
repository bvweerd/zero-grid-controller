"""Tests for power sensor unit conversion (kW/MW/mW → W)."""

from __future__ import annotations

import pytest
from pytest_homeassistant_custom_component.common import MockConfigEntry

from custom_components.zero_grid_controller.const import DOMAIN
from custom_components.zero_grid_controller.coordinator import ZeroGridCoordinator
from custom_components.zero_grid_controller.utils import power_unit_factor


@pytest.fixture(autouse=True)
def auto_enable_custom_integrations(enable_custom_integrations):
    return


def _make_entry() -> MockConfigEntry:
    return MockConfigEntry(
        domain=DOMAIN,
        title="Test",
        data={
            "name": "Test ZGC",
            "grid_import_sensors": ["sensor.grid_import"],
            "grid_export_sensors": ["sensor.grid_export"],
            "kp": 1.0,
            "ki": 0.0,
            "deadband_w": 10.0,
            "ewm_alpha": 1.0,
        },
        options={},
    )


def test_power_unit_factor():
    assert power_unit_factor("W") == 1.0
    assert power_unit_factor("kW") == 1000.0
    assert power_unit_factor("MW") == 1_000_000.0
    assert power_unit_factor("mW") == 0.001
    # Missing or unknown units are assumed to be Watts
    assert power_unit_factor(None) == 1.0
    assert power_unit_factor("hp") == 1.0


async def test_grid_sensor_in_kw_is_converted_to_w(hass):
    """A grid sensor reporting kW must be converted to Watts."""
    entry = _make_entry()
    entry.add_to_hass(hass)
    coordinator = ZeroGridCoordinator(hass, entry)

    hass.states.async_set("sensor.grid_import", "1.5", {"unit_of_measurement": "kW"})
    hass.states.async_set("sensor.grid_export", "200", {"unit_of_measurement": "W"})

    assert await coordinator._read_grid() == pytest.approx(1300.0)


async def test_grid_sensor_without_unit_assumed_w(hass):
    """A grid sensor without a unit attribute is assumed to be in Watts."""
    entry = _make_entry()
    entry.add_to_hass(hass)
    coordinator = ZeroGridCoordinator(hass, entry)

    hass.states.async_set("sensor.grid_import", "750")
    hass.states.async_set("sensor.grid_export", "0")

    assert await coordinator._read_grid() == pytest.approx(750.0)


async def test_grid_sensor_unavailable_returns_none(hass):
    """Unavailable sensors still propagate as None after the conversion change."""
    entry = _make_entry()
    entry.add_to_hass(hass)
    coordinator = ZeroGridCoordinator(hass, entry)

    hass.states.async_set("sensor.grid_import", "unavailable")
    hass.states.async_set("sensor.grid_export", "0")

    assert await coordinator._read_grid() is None
