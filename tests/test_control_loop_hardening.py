"""Tests for control-loop hardening: switch feedforward and anti-windup."""

from __future__ import annotations

from unittest.mock import AsyncMock, patch

import pytest
from pytest_homeassistant_custom_component.common import MockConfigEntry

from custom_components.zero_grid_controller.array import ArrayConfig
from custom_components.zero_grid_controller.const import DOMAIN
from custom_components.zero_grid_controller.coordinator import ZeroGridCoordinator


@pytest.fixture(autouse=True)
def auto_enable_custom_integrations(enable_custom_integrations):
    return


def _make_entry(**extra) -> MockConfigEntry:
    data = {
        "name": "Test ZGC",
        "grid_import_sensors": ["sensor.grid_import"],
        "grid_export_sensors": ["sensor.grid_export"],
        "kp": 1.0,
        "ki": 0.0,
        "deadband_w": 10.0,
        "ewm_alpha": 1.0,
        **extra,
    }
    return MockConfigEntry(domain=DOMAIN, title="Test", data=data, options={})


def _numeric_array(name="Solar", w_per_unit=50.0) -> ArrayConfig:
    return ArrayConfig(
        name=name,
        output_type="percent",
        setpoint_entity="number.solar_limit",
        w_per_unit=w_per_unit,
        calibration_confidence="estimated",
        setpoint_min=0.0,
        setpoint_max=100.0,
        settling_time_s=0,
    )


def _switch_array(name="SwitchArray") -> ArrayConfig:
    return ArrayConfig(
        name=name,
        output_type="switch",
        setpoint_entity="switch.solar_sw",
        w_per_unit=10.0,
        calibration_confidence="estimated",
        setpoint_min=0.0,
        setpoint_max=1.0,
        settling_time_s=0,
        switch_on_threshold_w=100.0,
        switch_off_threshold_w=50.0,
        switch_debounce_s=0,
    )


async def test_switch_array_not_toggled_against_curtailed_export(hass):
    """An export fully absorbed by numeric curtailment must not also turn the
    switch array off (double compensation → import swing)."""
    entry = _make_entry()
    entry.add_to_hass(hass)
    coordinator = ZeroGridCoordinator(hass, entry)
    coordinator.arrays = [_numeric_array(), _switch_array()]
    coordinator._current_setpoints["Solar"] = 100.0
    coordinator._current_setpoints["SwitchArray"] = 1.0  # on

    # 3000 W export — the numeric array can absorb all of it by curtailing
    hass.states.async_set("sensor.grid_import", "0")
    hass.states.async_set("sensor.grid_export", "3000")
    hass.states.async_set("switch.solar_sw", "on")

    with patch.object(coordinator._actuators, "write_setpoint", new=AsyncMock()):
        await coordinator._async_update_data()

    assert coordinator._current_setpoints["SwitchArray"] == 1.0, (
        "Switch array must stay on; the numeric array already took the correction"
    )
    assert coordinator._current_setpoints["Solar"] == 40.0


async def test_switch_array_still_toggles_on_unabsorbed_export(hass):
    """When nothing else can absorb the export, the switch array turns off."""
    entry = _make_entry()
    entry.add_to_hass(hass)
    coordinator = ZeroGridCoordinator(hass, entry)
    coordinator.arrays = [_switch_array()]
    coordinator._current_setpoints["SwitchArray"] = 1.0  # on

    hass.states.async_set("sensor.grid_import", "0")
    hass.states.async_set("sensor.grid_export", "200")
    hass.states.async_set("switch.solar_sw", "on")

    with patch.object(coordinator._actuators, "write_setpoint", new=AsyncMock()):
        await coordinator._async_update_data()

    assert coordinator._current_setpoints["SwitchArray"] == 0.0


async def test_integrator_frozen_when_nothing_can_absorb(hass):
    """With PV saturated and nothing to actuate, the integrator must not wind up."""
    # kp=0 so only the integrator could act; ki large to make windup visible
    entry = _make_entry(kp=0.0, ki=0.5)
    entry.add_to_hass(hass)
    coordinator = ZeroGridCoordinator(hass, entry)
    coordinator.arrays = [_numeric_array()]
    coordinator._current_setpoints["Solar"] = 100.0  # at max → no headroom

    # Persistent import that nothing can correct (PV maxed, no battery)
    hass.states.async_set("sensor.grid_import", "1000")
    hass.states.async_set("sensor.grid_export", "0")

    with patch.object(coordinator._actuators, "write_setpoint", new=AsyncMock()):
        for _ in range(5):
            await coordinator._async_update_data()

    # One cycle of accumulation is allowed (freeze applies from the next
    # compute), but it must not keep growing cycle after cycle.
    assert coordinator._pid.integral <= 1000.0 * 10.0, (
        "Integrator must freeze while no actuator can absorb the PID output"
    )


async def test_integrator_resumes_when_headroom_returns(hass):
    """The freeze is per-cycle: once an array can absorb again, control resumes."""
    entry = _make_entry(kp=1.0, ki=0.0)
    entry.add_to_hass(hass)
    coordinator = ZeroGridCoordinator(hass, entry)
    coordinator.arrays = [_numeric_array()]
    coordinator._current_setpoints["Solar"] = 100.0

    hass.states.async_set("sensor.grid_import", "0")
    hass.states.async_set("sensor.grid_export", "500")
    hass.states.async_set("number.solar_limit", "100")

    with patch.object(
        coordinator._actuators, "write_setpoint", new=AsyncMock()
    ) as mock_write:
        await coordinator._async_update_data()

    mock_write.assert_awaited()
    assert coordinator._current_setpoints["Solar"] == 90.0
