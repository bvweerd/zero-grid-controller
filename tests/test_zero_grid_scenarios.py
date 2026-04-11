"""Scenario tests for battery and switch array control."""

from __future__ import annotations

from unittest.mock import AsyncMock, patch

import pytest
from pytest_homeassistant_custom_component.common import MockConfigEntry

from custom_components.zero_grid_controller.array import ArrayConfig
from custom_components.zero_grid_controller.battery import BatteryConfig
from custom_components.zero_grid_controller.const import DOMAIN, STATUS_ACTIVE
from custom_components.zero_grid_controller.coordinator import ZeroGridCoordinator


@pytest.fixture(autouse=True)
def auto_enable_custom_integrations(enable_custom_integrations):
    return


def _entry(kp=1.0, ki=0.0, deadband_w=10.0, ewm_alpha=1.0):
    return MockConfigEntry(
        domain=DOMAIN,
        title="Test",
        data={
            "name": "Test ZGC",
            "grid_import_sensors": ["sensor.grid_import"],
            "grid_export_sensors": ["sensor.grid_export"],
            "kp": kp,
            "ki": ki,
            "deadband_w": deadband_w,
            "ewm_alpha": ewm_alpha,
        },
        options={},
    )


def _make_array(name="Solar", settling_time_s=0):
    return ArrayConfig(
        name=name,
        output_type="percent",
        setpoint_entity="number.solar_sp",
        w_per_unit=10.0,
        calibration_confidence="estimated",
        setpoint_min=0.0,
        setpoint_max=100.0,
        settling_time_s=settling_time_s,
    )


def _make_battery(name="Battery", max_charge=5000.0, max_discharge=5000.0):
    return BatteryConfig(
        subentry_id="sub_bat",
        name=name,
        sensor_entity="sensor.battery_power",
        max_charge_w=max_charge,
        max_discharge_w=max_discharge,
        setpoint_entity="number.battery_sp",
    )


async def test_export_charges_batteries(hass):
    """When exporting, batteries should be commanded to charge."""
    entry = _entry(deadband_w=10.0, ewm_alpha=1.0)
    entry.add_to_hass(hass)
    coordinator = ZeroGridCoordinator(hass, entry)
    coordinator.batteries = [_make_battery(max_charge=3000.0)]
    hass.states.async_set("sensor.grid_import", "0")
    hass.states.async_set("sensor.grid_export", "500")

    with patch.object(coordinator._actuators, "write_numeric_entity", new=AsyncMock()):
        result = await coordinator._async_update_data()

    assert result.status == STATUS_ACTIVE
    battery_sp = result.battery_setpoints.get("Battery", 0.0)
    assert battery_sp < 0  # negative = charging


async def test_import_pv_at_max_discharges_batteries(hass):
    """When importing and PV is at max, batteries should discharge."""
    entry = _entry(deadband_w=10.0, ewm_alpha=1.0)
    entry.add_to_hass(hass)
    coordinator = ZeroGridCoordinator(hass, entry)
    array = _make_array(settling_time_s=0)
    coordinator.arrays = [array]
    coordinator.batteries = [_make_battery(max_discharge=2000.0)]
    coordinator._current_setpoints["Solar"] = 100.0  # PV at max

    hass.states.async_set("sensor.grid_import", "300")
    hass.states.async_set("sensor.grid_export", "0")

    with (
        patch.object(coordinator._actuators, "write_numeric_entity", new=AsyncMock()),
        patch.object(coordinator._actuators, "write_setpoint", new=AsyncMock()),
    ):
        result = await coordinator._async_update_data()

    assert result.status == STATUS_ACTIVE
    battery_sp = result.battery_setpoints.get("Battery", 0.0)
    assert battery_sp > 0  # positive = discharging


async def test_switch_array_turns_on_when_importing(hass):
    """Switch array turns on when import exceeds on_threshold."""
    entry = _entry(deadband_w=10.0, ewm_alpha=1.0)
    entry.add_to_hass(hass)
    coordinator = ZeroGridCoordinator(hass, entry)
    switch_array = ArrayConfig(
        name="SwitchArray",
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
    coordinator.arrays = [switch_array]
    coordinator._current_setpoints["SwitchArray"] = 0.0  # off

    hass.states.async_set("sensor.grid_import", "200")
    hass.states.async_set("sensor.grid_export", "0")
    hass.states.async_set("switch.solar_sw", "off")

    with patch.object(coordinator._actuators, "write_setpoint", new=AsyncMock()):
        await coordinator._async_update_data()

    assert coordinator._current_setpoints.get("SwitchArray") == 1.0


async def test_switch_array_turns_off_when_exporting(hass):
    """Switch array turns off when export exceeds off_threshold."""
    entry = _entry(deadband_w=10.0, ewm_alpha=1.0)
    entry.add_to_hass(hass)
    coordinator = ZeroGridCoordinator(hass, entry)
    switch_array = ArrayConfig(
        name="SwitchArray",
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
    coordinator.arrays = [switch_array]
    coordinator._current_setpoints["SwitchArray"] = 1.0  # on

    hass.states.async_set("sensor.grid_import", "0")
    hass.states.async_set("sensor.grid_export", "200")
    hass.states.async_set("switch.solar_sw", "on")

    with patch.object(coordinator._actuators, "write_setpoint", new=AsyncMock()):
        await coordinator._async_update_data()

    assert coordinator._current_setpoints.get("SwitchArray") == 0.0
