"""Tests for ZeroGridCoordinator control loop."""

from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from pytest_homeassistant_custom_component.common import MockConfigEntry

from custom_components.zero_grid_controller.array import ArrayConfig
from custom_components.zero_grid_controller.battery import BatteryConfig
from custom_components.zero_grid_controller.calibrator import CalibrationResult
from custom_components.zero_grid_controller.const import (
    ARRAY_SUBENTRY_TYPE,
    BATTERY_SUBENTRY_TYPE,
    DOMAIN,
    STATUS_ACTIVE,
    STATUS_DEADBAND,
    STATUS_DISABLED,
)
from custom_components.zero_grid_controller.coordinator import ZeroGridCoordinator


@pytest.fixture(autouse=True)
def auto_enable_custom_integrations(enable_custom_integrations):
    return


def _make_entry(
    import_sensors=None,
    export_sensors=None,
    deadband_w=20.0,
    ewm_alpha=1.0,  # no smoothing for tests
    kp=1.0,
    ki=0.0,
    kd=0.0,
    controller_enabled=True,
    subentries=None,
):
    data = {
        "name": "Test ZGC",
        "grid_import_sensors": import_sensors or ["sensor.grid_import"],
        "grid_export_sensors": export_sensors or ["sensor.grid_export"],
        "deadband_w": deadband_w,
        "ewm_alpha": ewm_alpha,
        "kp": kp,
        "ki": ki,
        "kd": kd,
        "controller_enabled": controller_enabled,
    }
    entry = MockConfigEntry(domain=DOMAIN, title="Test", data=data, options={})
    if subentries:
        # MockConfigEntry uses a dict for subentries
        entry._subentries = subentries
    return entry


def _set_state(hass, entity_id, value):
    hass.states.async_set(entity_id, str(value))


async def _run_once(coordinator):
    """Trigger one coordinator update and return the result."""
    await coordinator._async_update_data()
    return coordinator.data


async def test_grid_unavailable_returns_disabled(hass):
    entry = _make_entry()
    entry.add_to_hass(hass)
    coordinator = ZeroGridCoordinator(hass, entry)
    # Don't set grid sensor states → unavailable
    result = await coordinator._async_update_data()
    assert result.status == STATUS_DISABLED


async def test_grid_zero_within_deadband(hass):
    entry = _make_entry(deadband_w=20.0, ewm_alpha=1.0)
    entry.add_to_hass(hass)
    coordinator = ZeroGridCoordinator(hass, entry)
    _set_state(hass, "sensor.grid_import", 0)
    _set_state(hass, "sensor.grid_export", 0)
    result = await coordinator._async_update_data()
    assert result.status == STATUS_DEADBAND


async def test_grid_small_error_within_deadband(hass):
    entry = _make_entry(deadband_w=20.0, ewm_alpha=1.0)
    entry.add_to_hass(hass)
    coordinator = ZeroGridCoordinator(hass, entry)
    _set_state(hass, "sensor.grid_import", 10)
    _set_state(hass, "sensor.grid_export", 0)
    result = await coordinator._async_update_data()
    assert result.status == STATUS_DEADBAND


async def test_grid_above_deadband_returns_active(hass):
    entry = _make_entry(deadband_w=20.0, ewm_alpha=1.0)
    entry.add_to_hass(hass)
    coordinator = ZeroGridCoordinator(hass, entry)
    _set_state(hass, "sensor.grid_import", 100)
    _set_state(hass, "sensor.grid_export", 0)
    result = await coordinator._async_update_data()
    assert result.status == STATUS_ACTIVE


async def test_controller_disabled_returns_disabled(hass):
    entry = _make_entry(controller_enabled=False)
    entry.add_to_hass(hass)
    coordinator = ZeroGridCoordinator(hass, entry)
    _set_state(hass, "sensor.grid_import", 100)
    _set_state(hass, "sensor.grid_export", 0)
    result = await coordinator._async_update_data()
    assert result.status == STATUS_DISABLED


async def test_controller_enabled_allows_control(hass):
    entry = _make_entry(controller_enabled=True, deadband_w=20.0, ewm_alpha=1.0)
    entry.add_to_hass(hass)
    coordinator = ZeroGridCoordinator(hass, entry)
    _set_state(hass, "sensor.grid_import", 100)
    _set_state(hass, "sensor.grid_export", 0)
    result = await coordinator._async_update_data()
    assert result.status == STATUS_ACTIVE


async def test_grid_reading_import_minus_export(hass):
    entry = _make_entry(deadband_w=5.0, ewm_alpha=1.0)
    entry.add_to_hass(hass)
    coordinator = ZeroGridCoordinator(hass, entry)
    _set_state(hass, "sensor.grid_import", 300)
    _set_state(hass, "sensor.grid_export", 100)
    result = await coordinator._async_update_data()
    assert result.grid_raw_w == pytest.approx(200.0)


async def test_grid_unavailable_when_export_sensor_invalid(hass):
    entry = _make_entry(
        import_sensors=["sensor.grid_import"], export_sensors=["sensor.grid_export"]
    )
    entry.add_to_hass(hass)
    coordinator = ZeroGridCoordinator(hass, entry)
    _set_state(hass, "sensor.grid_import", 100)
    hass.states.async_set("sensor.grid_export", "unknown")

    result = await coordinator._async_update_data()

    assert result.status == STATUS_DISABLED


async def test_reload_config_preserves_pid_integral(hass):
    entry = _make_entry(kp=1.0, ki=0.1, deadband_w=5.0, ewm_alpha=1.0)
    entry.add_to_hass(hass)
    coordinator = ZeroGridCoordinator(hass, entry)
    # Force some integral accumulation
    coordinator._pid.set_integral(42.0)
    coordinator._filtered_w = 100.0
    coordinator.reload_config()
    assert coordinator._pid.integral == pytest.approx(42.0)
    assert coordinator._filtered_w == pytest.approx(100.0)


async def test_start_calibration_returns_empty_when_already_running(hass):
    entry = _make_entry()
    entry.add_to_hass(hass)
    coordinator = ZeroGridCoordinator(hass, entry)
    coordinator._calibrator = MagicMock()

    assert await coordinator.start_calibration() == []


async def test_start_calibration_runs_and_persists_results(hass):
    entry = _make_entry()
    entry.add_to_hass(hass)
    coordinator = ZeroGridCoordinator(hass, entry)
    result = CalibrationResult(
        array_name="Solar",
        success=True,
        w_per_unit=12.0,
        settling_time_s=8,
        kp=0.4,
        ki=0.02,
        message="ok",
    )

    fake_calibrator = MagicMock()
    fake_calibrator.run = AsyncMock(return_value=[result])
    with (
        patch(
            "custom_components.zero_grid_controller.coordinator.ArrayCalibrator",
            return_value=fake_calibrator,
        ),
        patch.object(
            coordinator, "_persist_calibration_results", new=AsyncMock()
        ) as mock_persist,
    ):
        results = await coordinator.start_calibration()

    assert results == [result]
    mock_persist.assert_awaited_once_with([result])
    assert coordinator._calibrator is None


async def test_abort_calibration_calls_abort(hass):
    entry = _make_entry()
    entry.add_to_hass(hass)
    coordinator = ZeroGridCoordinator(hass, entry)
    coordinator._calibrator = MagicMock()

    coordinator.abort_calibration()

    coordinator._calibrator.abort.assert_called_once()


def test_read_sensor_safe_returns_none_for_invalid_state(hass):
    entry = _make_entry()
    entry.add_to_hass(hass)
    coordinator = ZeroGridCoordinator(hass, entry)

    hass.states.async_set("sensor.grid_import", "not-a-number")
    assert coordinator._read_sensor_safe("sensor.grid_import") is None
    assert coordinator._read_sensor_safe("sensor.missing") is None


async def test_distribute_to_numeric_arrays_respects_headroom_and_settling(hass):
    entry = _make_entry()
    entry.add_to_hass(hass)
    coordinator = ZeroGridCoordinator(hass, entry)
    coordinator.arrays = [
        ArrayConfig(
            name="A",
            output_type="percent",
            setpoint_entity="number.a",
            w_per_unit=10.0,
            calibration_confidence="estimated",
            setpoint_min=0.0,
            setpoint_max=100.0,
            settling_time_s=15,
        ),
        ArrayConfig(
            name="B",
            output_type="percent",
            setpoint_entity="number.b",
            w_per_unit=10.0,
            calibration_confidence="estimated",
            setpoint_min=0.0,
            setpoint_max=100.0,
            settling_time_s=15,
        ),
    ]
    coordinator._current_setpoints = {"A": 80.0, "B": 100.0}
    coordinator._settling_until = {"B": 200.0}

    with patch.object(
        coordinator._actuators, "write_setpoint", new=AsyncMock()
    ) as mock_write:
        await coordinator._distribute_to_numeric_arrays(50.0, now=100.0)
        await coordinator._distribute_to_numeric_arrays(0.0, now=100.0)

    mock_write.assert_awaited_once()
    assert coordinator._current_setpoints["A"] == 85.0
    assert coordinator._settling_until["A"] == 115.0


async def test_distribute_to_numeric_arrays_handles_curtailment_and_zero_headroom(hass):
    entry = _make_entry()
    entry.add_to_hass(hass)
    coordinator = ZeroGridCoordinator(hass, entry)
    coordinator.arrays = [
        ArrayConfig(
            name="A",
            output_type="percent",
            setpoint_entity="number.a",
            w_per_unit=10.0,
            calibration_confidence="estimated",
            setpoint_min=0.0,
            setpoint_max=100.0,
            settling_time_s=10,
        )
    ]
    coordinator._current_setpoints = {"A": 20.0}

    with patch.object(
        coordinator._actuators, "write_setpoint", new=AsyncMock()
    ) as mock_write:
        await coordinator._distribute_to_numeric_arrays(-40.0, now=10.0)
        await coordinator._distribute_to_numeric_arrays(-40.0, now=11.0)

    mock_write.assert_awaited_once()
    assert coordinator._current_setpoints["A"] == 16.0


async def test_distribute_to_numeric_arrays_skips_zero_delta_units_and_zero_headroom(
    hass,
):
    entry = _make_entry()
    entry.add_to_hass(hass)
    coordinator = ZeroGridCoordinator(hass, entry)
    coordinator.arrays = [
        ArrayConfig(
            name="A",
            output_type="percent",
            setpoint_entity="number.a",
            w_per_unit=100.0,
            calibration_confidence="estimated",
            setpoint_min=0.0,
            setpoint_max=100.0,
            settling_time_s=10,
        )
    ]
    coordinator._current_setpoints = {"A": 100.0}

    with patch.object(
        coordinator._actuators, "write_setpoint", new=AsyncMock()
    ) as mock_write:
        await coordinator._distribute_to_numeric_arrays(50.0, now=10.0)
        coordinator._current_setpoints["A"] = 50.0
        await coordinator._distribute_to_numeric_arrays(10.0, now=10.0)

    mock_write.assert_not_awaited()


async def test_apply_switch_hysteresis_honors_debounce(hass):
    entry = _make_entry()
    entry.add_to_hass(hass)
    coordinator = ZeroGridCoordinator(hass, entry)
    coordinator.arrays = [
        ArrayConfig(
            name="Switch",
            output_type="switch",
            setpoint_entity="switch.a",
            w_per_unit=10.0,
            calibration_confidence="estimated",
            setpoint_min=0.0,
            setpoint_max=1.0,
            settling_time_s=0,
            switch_on_threshold_w=100.0,
            switch_off_threshold_w=50.0,
            switch_debounce_s=30,
        )
    ]
    coordinator._current_setpoints = {"Switch": 0.0}

    with patch.object(
        coordinator._actuators, "write_setpoint", new=AsyncMock()
    ) as mock_write:
        await coordinator._apply_switch_hysteresis(150.0, now=10.0)
        await coordinator._apply_switch_hysteresis(150.0, now=15.0)
        await coordinator._apply_switch_hysteresis(-60.0, now=50.0)

    assert mock_write.await_count == 2
    assert coordinator._current_setpoints["Switch"] == 0.0


async def test_update_data_resets_negative_battery_target_and_freezes_when_settling(
    hass,
):
    entry = _make_entry(deadband_w=5.0, ewm_alpha=1.0)
    entry.add_to_hass(hass)
    coordinator = ZeroGridCoordinator(hass, entry)
    coordinator.batteries = [
        BatteryConfig(
            subentry_id="battery-1",
            name="Battery",
            sensor_entity="sensor.battery_power",
            max_charge_w=3000.0,
            max_discharge_w=5000.0,
            setpoint_entity="number.battery_sp",
        )
    ]
    coordinator.arrays = [
        ArrayConfig(
            name="Solar",
            output_type="percent",
            setpoint_entity="number.solar_limit",
            w_per_unit=10.0,
            calibration_confidence="estimated",
            setpoint_min=0.0,
            setpoint_max=100.0,
            settling_time_s=20,
        )
    ]
    coordinator._current_battery_setpoints["Battery"] = -500.0
    coordinator._settling_until["Solar"] = 9999999999.0

    hass.states.async_set("sensor.grid_import", "200")
    hass.states.async_set("sensor.grid_export", "0")

    with (
        patch.object(
            coordinator._actuators, "write_numeric_entity", new=AsyncMock()
        ) as mock_write,
        patch.object(coordinator._pid, "freeze_integrator") as mock_freeze,
        patch.object(coordinator._actuators, "write_setpoint", new=AsyncMock()),
    ):
        result = await coordinator._async_update_data()

    assert result.status == STATUS_ACTIVE
    mock_write.assert_awaited()
    mock_freeze.assert_called()


async def test_apply_switch_hysteresis_noop_below_threshold(hass):
    entry = _make_entry()
    entry.add_to_hass(hass)
    coordinator = ZeroGridCoordinator(hass, entry)
    coordinator.arrays = [
        ArrayConfig(
            name="Switch",
            output_type="switch",
            setpoint_entity="switch.a",
            w_per_unit=10.0,
            calibration_confidence="estimated",
            setpoint_min=0.0,
            setpoint_max=1.0,
            settling_time_s=0,
            switch_on_threshold_w=100.0,
            switch_off_threshold_w=50.0,
            switch_debounce_s=30,
        )
    ]

    with patch.object(
        coordinator._actuators, "write_setpoint", new=AsyncMock()
    ) as mock_write:
        await coordinator._apply_switch_hysteresis(20.0, now=10.0)

    mock_write.assert_not_awaited()


async def test_persist_calibration_results_updates_matching_subentry(hass):
    subentries = (
        {
            "subentry_id": "array-1",
            "subentry_type": ARRAY_SUBENTRY_TYPE,
            "title": "Solar",
            "data": {
                "array_name": "Solar",
                "output_type": "percent",
                "setpoint_entity": "number.solar_limit",
                "setpoint_min": 0.0,
                "setpoint_max": 100.0,
                "w_per_unit": 10.0,
                "settling_time_s": 15,
            },
        },
    )
    entry = MockConfigEntry(
        domain=DOMAIN,
        title="Test",
        data=_make_entry().data,
        options={},
        subentries_data=subentries,
    )
    entry.add_to_hass(hass)
    coordinator = ZeroGridCoordinator(hass, entry)
    result = CalibrationResult(
        array_name="Solar",
        success=True,
        w_per_unit=15.0,
        settling_time_s=9,
        kp=0.7,
        ki=0.03,
        message="ok",
    )

    with (
        patch.object(
            hass.config_entries, "async_update_subentry"
        ) as mock_update_subentry,
        patch.object(hass.config_entries, "async_update_entry") as mock_update_entry,
    ):
        await coordinator._persist_calibration_results([result])

    mock_update_subentry.assert_called_once()
    mock_update_entry.assert_called_once()
    assert coordinator.arrays[0].w_per_unit == 15.0
    assert coordinator.arrays[0].settling_time_s == 9
    assert coordinator._pid.kp == pytest.approx(0.7)


async def test_persist_calibration_results_skips_unsuccessful_or_unknown_arrays(hass):
    entry = _make_entry()
    entry.add_to_hass(hass)
    coordinator = ZeroGridCoordinator(hass, entry)

    with patch.object(
        hass.config_entries, "async_update_subentry"
    ) as mock_update_subentry:
        await coordinator._persist_calibration_results(
            [
                CalibrationResult("Missing", True, 10.0, 5, 0.5, 0.01, "ok"),
                CalibrationResult("Missing", False, 10.0, 5, 0.5, 0.01, "failed"),
            ]
        )

    mock_update_subentry.assert_not_called()


async def test_persist_calibration_results_skips_non_array_subentries(hass):
    entry = MockConfigEntry(
        domain=DOMAIN,
        title="Test",
        data=_make_entry().data,
        options={},
        subentries_data=(
            {
                "subentry_id": "battery-1",
                "subentry_type": BATTERY_SUBENTRY_TYPE,
                "title": "Battery",
                "data": {
                    "name": "Battery",
                    "battery_sensor": "sensor.battery_power",
                    "battery_max_charge_w": 4000.0,
                    "battery_max_discharge_w": 5000.0,
                    "battery_setpoint_entity": "number.battery_limit",
                },
            },
        ),
    )
    entry.add_to_hass(hass)
    coordinator = ZeroGridCoordinator(hass, entry)
    coordinator.arrays = [
        ArrayConfig(
            name="Solar",
            output_type="percent",
            setpoint_entity="number.solar_limit",
            w_per_unit=10.0,
            calibration_confidence="estimated",
            setpoint_min=0.0,
            setpoint_max=100.0,
            settling_time_s=15,
        )
    ]

    with patch.object(
        hass.config_entries, "async_update_subentry"
    ) as mock_update_subentry:
        await coordinator._persist_calibration_results(
            [CalibrationResult("Solar", True, 15.0, 9, 0.7, 0.03, "ok")]
        )

    mock_update_subentry.assert_not_called()
