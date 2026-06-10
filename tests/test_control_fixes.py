"""Tests for control fixes: unit conversion, failsafe debounce/mode,
switch feedforward of array curtailment, and the calibration grid guard."""

from __future__ import annotations

import contextlib
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from pytest_homeassistant_custom_component.common import MockConfigEntry

from custom_components.zero_grid_controller.array import ArrayConfig
from custom_components.zero_grid_controller.calibrator import ArrayCalibrator
from custom_components.zero_grid_controller.const import (
    DOMAIN,
    FAILSAFE_MODE_CURTAIL,
    GRID_UNAVAILABLE_TOLERANCE_CYCLES,
    LOAD_TYPE_SWITCH,
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


# ---------------------------------------------------------------------------
# Power unit conversion
# ---------------------------------------------------------------------------


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


# ---------------------------------------------------------------------------
# Grid-unavailable debounce and failsafe mode
# ---------------------------------------------------------------------------


async def test_grid_dropout_held_before_failsafe(hass):
    """Short sensor dropouts hold state instead of jumping to failsafe."""
    entry = _make_entry()
    entry.add_to_hass(hass)
    coordinator = ZeroGridCoordinator(hass, entry)
    coordinator.arrays = [_numeric_array()]
    coordinator._engine._current_setpoints["Solar"] = 50.0

    hass.states.async_set("sensor.grid_import", "100")
    hass.states.async_set("sensor.grid_export", "0")

    with patch.object(
        coordinator._actuators, "write_setpoint", new=AsyncMock()
    ) as mock_write:
        first = await coordinator._async_update_data()
        coordinator.async_set_updated_data(first)

        hass.states.async_set("sensor.grid_import", "unavailable")
        mock_write.reset_mock()

        # Cycles within the tolerance window: hold, no failsafe writes
        for _ in range(GRID_UNAVAILABLE_TOLERANCE_CYCLES - 1):
            held = await coordinator._async_update_data()
            assert held is first, "State must be held during the dropout window"
        mock_write.assert_not_awaited()

        # Tolerance exceeded → failsafe (default: PV to max)
        result = await coordinator._async_update_data()

    assert result.status == "disabled"
    mock_write.assert_awaited()
    assert mock_write.call_args[0][1] == 100.0  # setpoint_max


async def test_failsafe_curtail_mode_moves_pv_to_min(hass):
    """failsafe_mode=curtail sends PV to minimum instead of maximum."""
    entry = _make_entry(failsafe_mode=FAILSAFE_MODE_CURTAIL)
    entry.add_to_hass(hass)
    coordinator = ZeroGridCoordinator(hass, entry)
    coordinator.arrays = [_numeric_array()]

    hass.states.async_set("sensor.grid_import", "unavailable")
    hass.states.async_set("sensor.grid_export", "0")

    with patch.object(
        coordinator._actuators, "write_setpoint", new=AsyncMock()
    ) as mock_write:
        result = await coordinator._async_update_data()

    assert result.status == "disabled"
    mock_write.assert_awaited()
    assert mock_write.call_args[0][1] == 0.0  # setpoint_min


# ---------------------------------------------------------------------------
# Switch loads must not double-take corrections already made by arrays
# ---------------------------------------------------------------------------


async def test_switch_load_not_turned_on_against_curtailed_export(hass):
    """An export fully absorbed by array curtailment must not also turn on a
    switch load (that would flip the grid to import)."""
    entry = _make_entry()
    entry.add_to_hass(hass)
    coordinator = ZeroGridCoordinator(hass, entry)
    coordinator.arrays = [_numeric_array()]
    coordinator.loads = [
        LoadConfig(
            name="Boiler",
            load_type=LOAD_TYPE_SWITCH,
            setpoint_entity="switch.boiler",
            priority=1,
            power_w=2000.0,
            switch_debounce_s=0,
        )
    ]
    coordinator._engine._current_setpoints["Solar"] = 100.0
    coordinator._engine._current_load_setpoints["Boiler"] = 0.0

    # 3000 W export — the array can absorb all of it by curtailing
    hass.states.async_set("sensor.grid_import", "0")
    hass.states.async_set("sensor.grid_export", "3000")
    hass.states.async_set("switch.boiler", "off")

    with (
        patch.object(coordinator._actuators, "write_setpoint", new=AsyncMock()),
        patch.object(
            coordinator._actuators, "write_switch_entity", new=AsyncMock()
        ) as mock_switch,
    ):
        await coordinator._async_update_data()

    mock_switch.assert_not_awaited()
    assert coordinator._engine._current_load_setpoints["Boiler"] == 0.0
    # The array took the full correction
    assert coordinator._engine._current_setpoints["Solar"] == 40.0


# ---------------------------------------------------------------------------
# Calibration grid guard
# ---------------------------------------------------------------------------


async def test_calibration_aborts_when_grid_limit_exceeded():
    """Calibration fails the array when |grid| exceeds CALIB_MAX_GRID_W."""
    array = ArrayConfig(
        name="PV West",
        output_type="percent",
        setpoint_entity="number.pv_west_limit",
        w_per_unit=23.0,
        calibration_confidence="estimated",
        setpoint_min=0.0,
        setpoint_max=100.0,
        settling_time_s=15,
        power_sensor_entity="sensor.pv_west_power",
    )
    states = {array.power_sensor_entity: SimpleNamespace(state="300")}
    hass = MagicMock()
    hass.states.get.side_effect = states.get

    async def read_grid() -> float:
        return 5000.0  # way beyond CALIB_MAX_GRID_W

    write_setpoint = AsyncMock()
    calibrator = ArrayCalibrator(
        hass=hass,
        arrays=[array],
        current_setpoints={array.name: array.setpoint_max},
        aggressiveness="normal",
        write_setpoint=write_setpoint,
        read_grid=read_grid,
    )

    async def fake_sleep(_secs):
        return None

    with patch("asyncio.sleep", new=fake_sleep), contextlib.suppress(Exception):
        results = await calibrator.run()

    assert len(results) == 1
    assert results[0].success is False
    assert "exceeded" in results[0].message
    # Original setpoint restored despite the abort
    write_setpoint.assert_awaited_with(array, array.setpoint_max)
