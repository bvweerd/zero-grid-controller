"""Tests for the ZeroGridCoordinator."""

from __future__ import annotations

import time
from unittest.mock import AsyncMock, patch

import pytest
from homeassistant.core import HomeAssistant
from pytest_homeassistant_custom_component.common import MockConfigEntry

from custom_components.zero_grid_controller.const import (
    ARRAY_SUBENTRY_TYPE,
    CONF_ARRAY_NAME,
    CONF_CONTROLLER_ENABLED,
    CONF_GRID_EXPORT_SENSORS,
    CONF_GRID_IMPORT_SENSORS,
    CONF_INVERT_SIGN,
    CONF_OUTPUT_TYPE,
    CONF_SETPOINT_ENTITY,
    DOMAIN,
    OUTPUT_TYPE_PERCENT,
)
from custom_components.zero_grid_controller.coordinator import ZeroGridCoordinator


@pytest.fixture(autouse=True)
def auto_enable_custom_integrations(enable_custom_integrations):
    """Enable custom integrations for all tests in this module."""
    return


def _array_subentry_data(name: str = "Roof South") -> dict:
    return {
        "subentry_type": ARRAY_SUBENTRY_TYPE,
        "title": name,
        "data": {
            CONF_ARRAY_NAME: name,
            CONF_SETPOINT_ENTITY: "number.inverter_limit",
            CONF_OUTPUT_TYPE: OUTPUT_TYPE_PERCENT,
            "settling_time_s": 15,
            "setpoint_min": 0.0,
            "setpoint_max": 100.0,
            "w_per_unit": 10.0,
            "calibration_confidence": "estimated",
            "enabled": True,
        },
        "unique_id": None,
    }


def _make_entry(
    hass: HomeAssistant,
    data: dict | None = None,
    options: dict | None = None,
    subentries_data=None,
) -> MockConfigEntry:
    """Create and register a MockConfigEntry with the new import/export sensor config."""
    entry = MockConfigEntry(
        domain=DOMAIN,
        unique_id=DOMAIN,
        data=data
        or {
            "name": "Test ZGC",
            CONF_GRID_IMPORT_SENSORS: ["sensor.grid_import"],
            CONF_INVERT_SIGN: False,
        },
        options=options or {},
        subentries_data=subentries_data or (),
    )
    entry.add_to_hass(hass)
    return entry


# ---------------------------------------------------------------------------
# Test 1: Coordinator instantiates — arrays empty without subentries
# ---------------------------------------------------------------------------


async def test_coordinator_init(hass: HomeAssistant) -> None:
    """Test that a coordinator can be instantiated and arrays list is empty."""
    entry = _make_entry(hass)
    coordinator = ZeroGridCoordinator(hass, entry)
    assert coordinator.arrays == []


# ---------------------------------------------------------------------------
# Test 2: _read_grid — single import sensor
# ---------------------------------------------------------------------------


async def test_read_grid_single_import(hass: HomeAssistant) -> None:
    """Test that _read_grid returns the import sensor value."""
    entry = _make_entry(hass)
    hass.states.async_set("sensor.grid_import", "150.0")
    coordinator = ZeroGridCoordinator(hass, entry)
    assert coordinator._read_grid() == 150.0


# ---------------------------------------------------------------------------
# Test 3: _read_grid — inverted sign
# ---------------------------------------------------------------------------


async def test_read_grid_inverted(hass: HomeAssistant) -> None:
    """Test that _read_grid inverts sign when invert_sign=True."""
    entry = _make_entry(
        hass,
        data={
            "name": "Test ZGC",
            CONF_GRID_IMPORT_SENSORS: ["sensor.grid_import"],
            CONF_INVERT_SIGN: True,
        },
    )
    hass.states.async_set("sensor.grid_import", "150.0")
    coordinator = ZeroGridCoordinator(hass, entry)
    assert coordinator._read_grid() == -150.0


# ---------------------------------------------------------------------------
# Test 4: _read_grid — import minus export
# ---------------------------------------------------------------------------


async def test_read_grid_import_minus_export(hass: HomeAssistant) -> None:
    """Test _read_grid with separate import and export sensors."""
    entry = _make_entry(
        hass,
        data={
            "name": "Test ZGC",
            CONF_GRID_IMPORT_SENSORS: ["sensor.grid_import"],
            CONF_GRID_EXPORT_SENSORS: ["sensor.grid_export"],
            CONF_INVERT_SIGN: False,
        },
    )
    hass.states.async_set("sensor.grid_import", "300.0")
    hass.states.async_set("sensor.grid_export", "100.0")
    coordinator = ZeroGridCoordinator(hass, entry)
    assert coordinator._read_grid() == 200.0


# ---------------------------------------------------------------------------
# Test 5: _read_grid — multiple import sensors summed
# ---------------------------------------------------------------------------


async def test_read_grid_multiple_import_sensors(hass: HomeAssistant) -> None:
    """Test _read_grid sums multiple import sensors."""
    entry = _make_entry(
        hass,
        data={
            "name": "Test ZGC",
            CONF_GRID_IMPORT_SENSORS: ["sensor.import1", "sensor.import2"],
            CONF_INVERT_SIGN: False,
        },
    )
    hass.states.async_set("sensor.import1", "200.0")
    hass.states.async_set("sensor.import2", "50.0")
    coordinator = ZeroGridCoordinator(hass, entry)
    assert coordinator._read_grid() == 250.0


# ---------------------------------------------------------------------------
# Test 6: _read_grid — unavailable state returns 0.0 (default)
# ---------------------------------------------------------------------------


async def test_read_grid_unavailable_returns_zero(hass: HomeAssistant) -> None:
    """Test that an unavailable import sensor contributes 0 W."""
    entry = _make_entry(hass)
    hass.states.async_set("sensor.grid_import", "unavailable")
    coordinator = ZeroGridCoordinator(hass, entry)
    assert coordinator._read_grid() == 0.0


# ---------------------------------------------------------------------------
# Test 7: apply_array_config_update changes array parameter
# ---------------------------------------------------------------------------


async def test_apply_array_config_update(hass: HomeAssistant) -> None:
    """Test that apply_array_config_update modifies the array config in-place."""
    entry = _make_entry(hass, subentries_data=[_array_subentry_data()])
    coordinator = ZeroGridCoordinator(hass, entry)
    assert coordinator.arrays[0].settling_time_s == 15

    coordinator.apply_array_config_update("Roof South", {"settling_time_s": 30})
    assert coordinator.arrays[0].settling_time_s == 30


# ---------------------------------------------------------------------------
# Test 8: override_setpoint populates _override_setpoints
# ---------------------------------------------------------------------------


async def test_override_setpoint(hass: HomeAssistant) -> None:
    """Test that async_override_setpoint stores a value with expiry."""
    entry = _make_entry(hass, subentries_data=[_array_subentry_data()])
    coordinator = ZeroGridCoordinator(hass, entry)

    with patch.object(coordinator, "_write_setpoint", new=AsyncMock()):
        await coordinator.async_override_setpoint("Roof South", 75.0, duration_s=60.0)
    assert "Roof South" in coordinator._override_setpoints
    value, expires_at = coordinator._override_setpoints["Roof South"]
    assert value == 75.0
    assert expires_at > time.monotonic()
    assert coordinator._current_setpoints["Roof South"] == 75.0


# ---------------------------------------------------------------------------
# Test 9: reset_pid zeros the integral
# ---------------------------------------------------------------------------


async def test_reset_pid(hass: HomeAssistant) -> None:
    """Test that reset_pid clears the PID integrator."""
    entry = _make_entry(hass)
    coordinator = ZeroGridCoordinator(hass, entry)

    coordinator.pid.compute(100.0, 5.0)
    assert coordinator.pid.integral != 0.0

    coordinator.reset_pid()
    assert coordinator.pid.integral == 0.0


# ---------------------------------------------------------------------------
# Test 10: _is_grid_sensor_unavailable — returns True for unavailable
# ---------------------------------------------------------------------------


async def test_is_grid_sensor_unavailable_true(hass: HomeAssistant) -> None:
    """_is_grid_sensor_unavailable returns True when sensor is unavailable."""
    entry = _make_entry(hass)
    hass.states.async_set("sensor.grid_import", "unavailable")
    coordinator = ZeroGridCoordinator(hass, entry)
    assert coordinator._is_grid_sensor_unavailable() is True


# ---------------------------------------------------------------------------
# Test 11: _is_grid_sensor_unavailable — returns False for valid state
# ---------------------------------------------------------------------------


async def test_is_grid_sensor_unavailable_false(hass: HomeAssistant) -> None:
    """_is_grid_sensor_unavailable returns False when sensor is available."""
    entry = _make_entry(hass)
    hass.states.async_set("sensor.grid_import", "150.0")
    coordinator = ZeroGridCoordinator(hass, entry)
    assert coordinator._is_grid_sensor_unavailable() is False


# ---------------------------------------------------------------------------
# Test 12: _is_grid_sensor_unavailable — no sensors configured → False
# ---------------------------------------------------------------------------


async def test_is_grid_sensor_unavailable_no_sensors(hass: HomeAssistant) -> None:
    """_is_grid_sensor_unavailable returns False when no import sensors configured."""
    entry = _make_entry(
        hass,
        data={
            "name": "Test ZGC",
            CONF_INVERT_SIGN: False,
        },
    )
    coordinator = ZeroGridCoordinator(hass, entry)
    assert coordinator._is_grid_sensor_unavailable() is False


# ---------------------------------------------------------------------------
# Test 13: _read_sensor_safe — empty entity_id returns default
# ---------------------------------------------------------------------------


async def test_read_sensor_safe_empty_entity(hass: HomeAssistant) -> None:
    """_read_sensor_safe returns the default value when entity_id is empty."""
    entry = _make_entry(hass)
    coordinator = ZeroGridCoordinator(hass, entry)
    assert coordinator._read_sensor_safe("", 42.0) == 42.0


# ---------------------------------------------------------------------------
# Test 14: _read_sensor_safe — non-numeric state returns default
# ---------------------------------------------------------------------------


async def test_read_sensor_safe_value_error(hass: HomeAssistant) -> None:
    """_read_sensor_safe returns default when state.state is not a number."""
    entry = _make_entry(hass)
    hass.states.async_set("sensor.bad", "not_a_number")
    coordinator = ZeroGridCoordinator(hass, entry)
    assert coordinator._read_sensor_safe("sensor.bad", 99.0) == 99.0


# ---------------------------------------------------------------------------
# Test 15: reload_config — preserves setpoints
# ---------------------------------------------------------------------------


async def test_reload_config_preserves_setpoints(hass: HomeAssistant) -> None:
    """reload_config preserves previously applied setpoints."""
    entry = _make_entry(hass, subentries_data=[_array_subentry_data()])
    coordinator = ZeroGridCoordinator(hass, entry)
    coordinator._current_setpoints["Roof South"] = 65.0

    coordinator.reload_config()

    assert coordinator._current_setpoints.get("Roof South") == 65.0


# ---------------------------------------------------------------------------
# Test 16: set_ewm_alpha updates the filter coefficient
# ---------------------------------------------------------------------------


async def test_set_ewm_alpha(hass: HomeAssistant) -> None:
    """set_ewm_alpha updates _ewm_alpha on the coordinator."""
    entry = _make_entry(hass)
    coordinator = ZeroGridCoordinator(hass, entry)
    coordinator.set_ewm_alpha(0.5)
    assert coordinator._ewm_alpha == 0.5


# ---------------------------------------------------------------------------
# Test 17: set_deadband updates the deadband threshold
# ---------------------------------------------------------------------------


async def test_set_deadband(hass: HomeAssistant) -> None:
    """set_deadband updates _deadband_w on the coordinator."""
    entry = _make_entry(hass)
    coordinator = ZeroGridCoordinator(hass, entry)
    coordinator.set_deadband(100.0)
    assert coordinator._deadband_w == 100.0


# ---------------------------------------------------------------------------
# Test 18: Property accessors
# ---------------------------------------------------------------------------


async def test_property_accessors(hass: HomeAssistant) -> None:
    """Test coordinator property accessors."""
    entry = _make_entry(hass)
    coordinator = ZeroGridCoordinator(hass, entry)

    assert coordinator.expert_mode is False
    assert coordinator.get_estimator("nonexistent") is None
    assert coordinator.get_array("nonexistent") is None
    assert coordinator.controller_enabled is True


async def test_controller_enabled_restored_from_options(hass: HomeAssistant) -> None:
    """Controller enabled state is restored from config entry options."""
    entry = _make_entry(hass, options={CONF_CONTROLLER_ENABLED: False})
    coordinator = ZeroGridCoordinator(hass, entry)
    assert coordinator.controller_enabled is False


# ---------------------------------------------------------------------------
# Test 19: read_grid_w — returns None when sensor unavailable
# ---------------------------------------------------------------------------


async def test_read_grid_w_unavailable(hass: HomeAssistant) -> None:
    """read_grid_w returns None when import sensor is unavailable."""
    entry = _make_entry(hass)
    hass.states.async_set("sensor.grid_import", "unavailable")
    coordinator = ZeroGridCoordinator(hass, entry)
    assert coordinator.read_grid_w() is None


# ---------------------------------------------------------------------------
# Test 20: Estimator state restored from saved options
# ---------------------------------------------------------------------------


async def test_estimator_state_restored(hass: HomeAssistant) -> None:
    """Coordinator restores estimator state from CONF_ESTIMATOR_STATE in options."""
    from custom_components.zero_grid_controller.const import CONF_ESTIMATOR_STATE
    from custom_components.zero_grid_controller.estimator import RLSEstimator

    est = RLSEstimator(settling_time_s=15)
    for _ in range(10):
        est.update(-50.0, 50.0)
    saved_state = est.to_dict()

    entry = MockConfigEntry(
        domain=DOMAIN,
        unique_id=DOMAIN + "_est",
        data={
            "name": "Test ZGC",
            CONF_GRID_IMPORT_SENSORS: ["sensor.grid_import"],
            CONF_INVERT_SIGN: False,
        },
        options={CONF_ESTIMATOR_STATE: {"Roof South": saved_state}},
        subentries_data=[_array_subentry_data()],
    )
    entry.add_to_hass(hass)
    coordinator = ZeroGridCoordinator(hass, entry)

    restored = coordinator.get_estimator("Roof South")
    assert restored is not None
    assert restored.n_updates == 10


# ---------------------------------------------------------------------------
# Test 21: EWM cold-start — first cycle seeds filter from raw_w, not 0
# ---------------------------------------------------------------------------


async def test_ewm_cold_start_seeds_from_raw(hass: HomeAssistant) -> None:
    """On the very first update cycle the EWM filter must be seeded from raw_w.

    Without cold-start handling the filter initialises at 0 and ramps slowly
    toward the actual grid reading, causing spurious PID output for several
    cycles after startup.
    """
    entry = _make_entry(hass)
    hass.states.async_set("sensor.grid_import", "400.0")
    coordinator = ZeroGridCoordinator(hass, entry)

    assert not coordinator._filtered_w_initialized

    # Run one control-loop tick directly
    with (
        patch.object(
            coordinator, "_distribute_and_write", new=AsyncMock(return_value={})
        ),
        patch.object(
            coordinator, "_apply_battery_targets", new=AsyncMock(return_value={})
        ),
        patch.object(coordinator, "async_enter_safe_state", new=AsyncMock()),
    ):
        await coordinator._run_control_loop(dt=5.0, now=0.0)

    # Filter must be seeded from the real grid reading, not from 0
    assert coordinator._filtered_w_initialized
    assert coordinator._filtered_w == pytest.approx(400.0, abs=1.0)


# ---------------------------------------------------------------------------
# Test 22: async_start_calibration — concurrent guard returns False
# ---------------------------------------------------------------------------


async def test_concurrent_calibration_guard(hass: HomeAssistant) -> None:
    """async_start_calibration must refuse a second request while one is running."""
    entry = _make_entry(hass, subentries_data=[_array_subentry_data()])
    hass.states.async_set("sensor.grid_import", "0.0")
    coordinator = ZeroGridCoordinator(hass, entry)

    # Simulate an already-running calibration task
    import asyncio as _asyncio

    async def _never_completes() -> None:
        await _asyncio.sleep(3600)

    coordinator._calibration_task = hass.async_create_task(_never_completes())

    assert coordinator.is_calibrating is True

    # Second call must be rejected
    result = coordinator.async_start_calibration(
        coordinator.arrays, lambda msg, p: None
    )
    assert result is False

    # Clean up the background task
    coordinator._calibration_task.cancel()
    import contextlib as _contextlib

    with _contextlib.suppress(_asyncio.CancelledError):
        await coordinator._calibration_task
