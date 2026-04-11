"""Tests for diagnostics output."""

from __future__ import annotations

from types import SimpleNamespace

from pytest_homeassistant_custom_component.common import MockConfigEntry

from custom_components.zero_grid_controller import ZGCData
from custom_components.zero_grid_controller.array import ArrayConfig
from custom_components.zero_grid_controller.battery import BatteryConfig
from custom_components.zero_grid_controller.const import DOMAIN
from custom_components.zero_grid_controller.coordinator import ZGCResult, ZeroGridCoordinator
from custom_components.zero_grid_controller.diagnostics import (
    async_get_config_entry_diagnostics,
)


async def test_diagnostics_returns_runtime_snapshot(hass):
    entry = MockConfigEntry(
        domain=DOMAIN,
        title="Zero Grid",
        data={
            "name": "Zero Grid",
            "grid_import_sensors": ["sensor.grid_import"],
            "grid_export_sensors": ["sensor.grid_export"],
        },
        options={},
    )
    entry.add_to_hass(hass)
    coordinator = ZeroGridCoordinator(hass, entry)
    coordinator.arrays = [
        ArrayConfig(
            name="Solar",
            output_type="percent",
            setpoint_entity="number.solar_limit",
            w_per_unit=12.5,
            calibration_confidence="measured",
            setpoint_min=0.0,
            setpoint_max=100.0,
            settling_time_s=15,
        )
    ]
    coordinator.batteries = [
        BatteryConfig(
            subentry_id="bat-1",
            name="Battery",
            sensor_entity="sensor.battery_power",
            max_charge_w=4000.0,
            max_discharge_w=5000.0,
            setpoint_entity="number.battery_limit",
        )
    ]
    coordinator._pid.set_integral(1.2345)
    coordinator.data = ZGCResult(
        grid_raw_w=123.0,
        grid_filtered_w=100.0,
        pid_output_w=-50.0,
        status="active",
        setpoints={"Solar": 82.0},
        battery_setpoints={"Battery": -400.0},
    )
    entry.runtime_data = ZGCData(
        coordinator=coordinator,
        device=SimpleNamespace(),
    )

    diagnostics = await async_get_config_entry_diagnostics(hass, entry)

    assert diagnostics["status"] == "active"
    assert diagnostics["setpoints"]["Solar"] == 82.0
    assert diagnostics["battery_setpoints"]["Battery"] == -400.0
    assert diagnostics["pid"]["integral"] == 1.234
    assert diagnostics["arrays"][0]["calibration_confidence"] == "measured"
    assert diagnostics["batteries"][0]["max_discharge_w"] == 5000.0
