"""Robustness tests: write failures, windup consistency, init edge cases."""

from __future__ import annotations

from unittest.mock import AsyncMock, patch

import pytest
from pytest_homeassistant_custom_component.common import MockConfigEntry

from custom_components.zero_grid_controller.array import ArrayConfig
from custom_components.zero_grid_controller.battery import BatteryConfig
from custom_components.zero_grid_controller.const import (
    DOMAIN,
    LOAD_TYPE_NUMERIC,
    STATUS_ACTIVE,
)
from custom_components.zero_grid_controller.coordinator import ZeroGridCoordinator
from custom_components.zero_grid_controller.load import LoadConfig


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


def _switch_array() -> ArrayConfig:
    return ArrayConfig(
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


def _make_battery(**kwargs) -> BatteryConfig:
    return BatteryConfig(
        subentry_id="bat-1",
        name="Battery",
        sensor_entity="sensor.battery_power",
        max_charge_w=5000.0,
        max_discharge_w=5000.0,
        setpoint_entity="number.battery_sp",
        **kwargs,
    )


async def test_switch_array_write_failure_does_not_abort_cycle(hass):
    """A service exception while toggling a switch array is contained."""
    entry = _make_entry()
    entry.add_to_hass(hass)
    coordinator = ZeroGridCoordinator(hass, entry)
    coordinator.arrays = [_switch_array()]
    coordinator._engine._current_setpoints["SwitchArray"] = 0.0  # off

    hass.states.async_set("sensor.grid_import", "300")
    hass.states.async_set("sensor.grid_export", "0")
    hass.states.async_set("switch.solar_sw", "off")

    with patch.object(
        coordinator._actuators,
        "write_setpoint",
        new=AsyncMock(side_effect=RuntimeError("boom")),
    ):
        result = await coordinator._async_update_data()

    assert result.status == STATUS_ACTIVE
    # State not updated on failure: the toggle will be retried next cycle
    assert coordinator._engine._current_setpoints["SwitchArray"] == 0.0


async def test_discharge_blocked_by_running_but_uninitialised_load(hass):
    """A load running at restart (unseen by the engine) blocks battery
    discharge until it has been reduced — discharge is the last resort."""
    entry = _make_entry()
    entry.add_to_hass(hass)
    coordinator = ZeroGridCoordinator(hass, entry)
    coordinator.batteries = [_make_battery()]
    coordinator.loads = [
        LoadConfig(
            name="EV",
            load_type=LOAD_TYPE_NUMERIC,
            setpoint_entity="number.ev",
            priority=1,
            setpoint_min=0.0,
            setpoint_max=16.0,
            w_per_unit=230.0,
            settling_time_s=300,  # still settling → not reducible this cycle
        )
    ]
    coordinator._engine._load_settling_until["EV"] = 10_000_000.0
    # EV is charging at 16 A but the engine has not initialised it yet
    hass.states.async_set("number.ev", "16")
    hass.states.async_set("sensor.grid_import", "1000")
    hass.states.async_set("sensor.grid_export", "0")

    with patch.object(coordinator._actuators, "write_numeric_entity", new=AsyncMock()):
        result = await coordinator._async_update_data()

    assert result.battery_setpoints.get("Battery", 0.0) <= 0.0, (
        "Battery must not discharge while a reducible load is still running"
    )


async def test_soc_sensor_unavailable_logs_warning_once(hass, caplog):
    """A configured-but-unavailable SoC sensor logs one warning (fail-open)."""
    entry = _make_entry()
    entry.add_to_hass(hass)
    coordinator = ZeroGridCoordinator(hass, entry)
    coordinator.batteries = [_make_battery(soc_sensor_entity="sensor.battery_soc")]
    # SoC sensor never set → unavailable; grid exporting → charge attempt
    hass.states.async_set("sensor.grid_import", "0")
    hass.states.async_set("sensor.grid_export", "500")

    with patch.object(coordinator._actuators, "write_numeric_entity", new=AsyncMock()):
        await coordinator._async_update_data()
        await coordinator._async_update_data()

    warnings = [
        r
        for r in caplog.records
        if r.levelname == "WARNING" and "SoC sensor" in r.message
    ]
    assert len(warnings) == 1, "SoC-unavailable warning must be logged exactly once"
