"""Tests for grid-dropout debouncing and the configurable failsafe mode."""

from __future__ import annotations

from unittest.mock import AsyncMock, patch

import pytest
from pytest_homeassistant_custom_component.common import MockConfigEntry

from custom_components.zero_grid_controller.array import ArrayConfig
from custom_components.zero_grid_controller.const import (
    DOMAIN,
    FAILSAFE_MODE_CURTAIL,
    GRID_UNAVAILABLE_TOLERANCE_CYCLES,
    STATUS_DISABLED,
)
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


def _numeric_array(name="Solar") -> ArrayConfig:
    return ArrayConfig(
        name=name,
        output_type="percent",
        setpoint_entity="number.solar_limit",
        w_per_unit=50.0,
        calibration_confidence="estimated",
        setpoint_min=0.0,
        setpoint_max=100.0,
        settling_time_s=0,
    )


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

    assert result.status == STATUS_DISABLED
    mock_write.assert_awaited()
    assert mock_write.call_args[0][1] == 100.0  # setpoint_max


async def test_grid_recovery_resets_dropout_counter(hass):
    """A successful read resets the dropout counter (no creeping failsafe)."""
    entry = _make_entry()
    entry.add_to_hass(hass)
    coordinator = ZeroGridCoordinator(hass, entry)

    hass.states.async_set("sensor.grid_import", "100")
    hass.states.async_set("sensor.grid_export", "0")
    first = await coordinator._async_update_data()
    coordinator.async_set_updated_data(first)

    hass.states.async_set("sensor.grid_import", "unavailable")
    await coordinator._async_update_data()
    assert coordinator._grid_unavail_count == 1

    hass.states.async_set("sensor.grid_import", "100")
    await coordinator._async_update_data()
    assert coordinator._grid_unavail_count == 0


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

    assert result.status == STATUS_DISABLED
    mock_write.assert_awaited()
    assert mock_write.call_args[0][1] == 0.0  # setpoint_min


async def test_disabled_controller_uses_failsafe_mode(hass):
    """The configured failsafe mode also applies when the controller is disabled."""
    entry = _make_entry(failsafe_mode=FAILSAFE_MODE_CURTAIL, controller_enabled=False)
    entry.add_to_hass(hass)
    coordinator = ZeroGridCoordinator(hass, entry)
    coordinator.arrays = [_numeric_array()]

    hass.states.async_set("sensor.grid_import", "100")
    hass.states.async_set("sensor.grid_export", "0")

    with patch.object(
        coordinator._actuators, "write_setpoint", new=AsyncMock()
    ) as mock_write:
        result = await coordinator._async_update_data()

    assert result.status == STATUS_DISABLED
    mock_write.assert_awaited()
    assert mock_write.call_args[0][1] == 0.0  # setpoint_min


async def test_ewm_filter_reset_after_outage(hass):
    """The EWM filter restarts after an outage instead of resuming stale state."""
    entry = _make_entry(ewm_alpha=0.3)
    entry.add_to_hass(hass)
    coordinator = ZeroGridCoordinator(hass, entry)

    hass.states.async_set("sensor.grid_import", "1000")
    hass.states.async_set("sensor.grid_export", "0")
    first = await coordinator._async_update_data()
    coordinator.async_set_updated_data(first)
    assert coordinator._engine.filtered_w == pytest.approx(1000.0)

    # Full outage (beyond the tolerance window)
    hass.states.async_set("sensor.grid_import", "unavailable")
    for _ in range(GRID_UNAVAILABLE_TOLERANCE_CYCLES):
        await coordinator._async_update_data()
    assert coordinator._engine.filtered_w is None

    # Recovery: the filter re-seeds from the fresh reading
    hass.states.async_set("sensor.grid_import", "300")
    result = await coordinator._async_update_data()
    assert result.grid_filtered_w == pytest.approx(300.0)
