"""Tests for ArrayCalibrator."""

from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from custom_components.zero_grid_controller.array import ArrayConfig
from custom_components.zero_grid_controller.calibrator import ArrayCalibrator


@pytest.fixture(autouse=True)
def auto_enable_custom_integrations(enable_custom_integrations):
    return


def _make_array(name="PV West", setpoint_max=100.0, setpoint_min=0.0) -> ArrayConfig:
    return ArrayConfig(
        name=name,
        output_type="percent",
        setpoint_entity="number.pv_west_limit",
        w_per_unit=23.0,
        calibration_confidence="estimated",
        setpoint_min=setpoint_min,
        setpoint_max=setpoint_max,
        settling_time_s=15,
    )


def _calibrator(arrays, grid_readings, aggressiveness="normal"):
    """Build a calibrator with mock grid and write functions."""
    hass = MagicMock()
    current_setpoints = {a.name: a.setpoint_max for a in arrays}
    grid_iter = iter(grid_readings)

    async def read_grid():
        try:
            return next(grid_iter)
        except StopIteration:
            return grid_readings[-1]

    write_setpoint = AsyncMock()
    return (
        ArrayCalibrator(
            hass=hass,
            arrays=arrays,
            current_setpoints=current_setpoints,
            aggressiveness=aggressiveness,
            read_grid=read_grid,
            write_setpoint=write_setpoint,
        ),
        write_setpoint,
    )


async def test_calibration_success():
    """Successful calibration returns correct w_per_unit."""
    array = _make_array()
    # Baseline: 10 readings at 50 W, then settled response at 280 W (Δ = 230 W)
    # step = 10 % of 100 = 10 units → w_per_unit = 230 / 10 = 23 W/%
    baseline = [50.0] * 10
    # During settling: grid rises and stabilises at 280 W
    response = [50.0, 80.0, 150.0, 210.0, 270.0, 278.0, 280.0, 280.0, 280.0]
    grid_readings = baseline + response

    calibrator, write_sp = _calibrator([array], grid_readings)
    with patch("asyncio.sleep", new=AsyncMock()):
        results = await calibrator.run()

    assert len(results) == 1
    result = results[0]
    assert result.success is True
    assert result.w_per_unit == pytest.approx(23.0, abs=2.0)
    assert result.settling_time_s > 0
    assert result.kp > 0
    assert result.ki > 0


async def test_calibration_grid_unavailable_fails():
    """Calibration fails when grid sensor returns None."""
    array = _make_array()
    calibrator, _ = _calibrator([array], [None] * 20)
    with patch("asyncio.sleep", new=AsyncMock()):
        results = await calibrator.run()
    assert results[0].success is False


async def test_calibration_grid_too_far_from_zero_fails():
    """Calibration aborts when |grid| > CALIB_MAX_GRID_W."""
    array = _make_array()
    # Grid far from zero during baseline
    calibrator, _ = _calibrator([array], [5000.0] * 20)
    with patch("asyncio.sleep", new=AsyncMock()):
        results = await calibrator.run()
    assert results[0].success is False


async def test_calibration_setpoint_at_min_fails():
    """Calibration fails when setpoint is already at minimum."""
    array = _make_array(setpoint_min=0.0, setpoint_max=100.0)
    calibrator, _ = _calibrator([array], [50.0] * 20)
    # Force setpoint to min so step is impossible
    calibrator._current_setpoints["PV West"] = 0.0
    with patch("asyncio.sleep", new=AsyncMock()):
        results = await calibrator.run()
    assert results[0].success is False


async def test_calibration_timeout_fails():
    """Calibration fails when inverter never settles."""
    array = _make_array()
    # Response never reaches stable state — grid stays at baseline
    baseline = [50.0] * 10
    no_response = [50.0] * 200  # no change after step
    calibrator, _ = _calibrator([array], baseline + no_response)
    with patch("asyncio.sleep", new=AsyncMock()):
        results = await calibrator.run()
    assert results[0].success is False


async def test_calibration_tiny_response_fails():
    """Calibration fails when w_per_unit < CALIB_MIN_W_PER_UNIT."""
    array = _make_array()
    # Step = 10 units, delta_grid = 0.1 W → w_per_unit = 0.01 < 0.5
    baseline = [50.0] * 10
    response = [50.1, 50.1, 50.1, 50.1, 50.1, 50.1]
    calibrator, _ = _calibrator([array], baseline + response)
    with patch("asyncio.sleep", new=AsyncMock()):
        results = await calibrator.run()
    assert results[0].success is False


async def test_calibration_restores_setpoint_on_success():
    """Calibration restores original setpoint after completing."""
    array = _make_array()
    original_sp = array.setpoint_max
    baseline = [50.0] * 10
    response = [50.0, 100.0, 200.0, 270.0, 278.0, 280.0, 280.0, 280.0, 280.0]
    calibrator, write_sp = _calibrator([array], baseline + response)
    with patch("asyncio.sleep", new=AsyncMock()):
        await calibrator.run()
    # Last write should restore original setpoint
    last_write_value = write_sp.call_args_list[-1].args[1]
    assert last_write_value == pytest.approx(original_sp)


async def test_calibration_abort_stops_early():
    """abort() prevents calibrating further arrays."""
    arrays = [_make_array("Array1"), _make_array("Array2")]
    baseline = [50.0] * 10
    response = [50.0, 100.0, 200.0, 278.0, 280.0, 280.0, 280.0, 280.0, 280.0]
    calibrator, _ = _calibrator(arrays, baseline + response + baseline + response)

    async def sleep_and_abort(secs):
        calibrator.abort()

    with patch("asyncio.sleep", new=sleep_and_abort):
        results = await calibrator.run()
    # Only first array was calibrated
    assert len(results) == 1


async def test_calibration_switch_arrays_skipped():
    """Switch-type arrays are not calibrated."""
    from custom_components.zero_grid_controller.const import OUTPUT_TYPE_SWITCH

    switch_array = ArrayConfig(
        name="SwitchArray",
        output_type=OUTPUT_TYPE_SWITCH,
        setpoint_entity="switch.solar",
        w_per_unit=500.0,
        calibration_confidence="estimated",
        setpoint_min=0.0,
        setpoint_max=1.0,
        settling_time_s=30,
    )
    calibrator, _ = _calibrator([switch_array], [50.0] * 20)
    with patch("asyncio.sleep", new=AsyncMock()):
        results = await calibrator.run()
    assert results == []  # no numeric arrays → no results


async def test_calibration_aggressiveness_normal():
    """Normal aggressiveness gives reasonable gains."""
    array = _make_array()
    baseline = [50.0] * 10
    response = [50.0, 100.0, 200.0, 278.0, 280.0, 280.0, 280.0, 280.0, 280.0]
    calibrator, _ = _calibrator([array], baseline + response, aggressiveness="normal")
    with patch("asyncio.sleep", new=AsyncMock()):
        results = await calibrator.run()
    assert results[0].success
    # kp ≈ 1.0 / 23.0 ≈ 0.043
    assert results[0].kp == pytest.approx(1.0 / results[0].w_per_unit, rel=0.01)


async def test_calibration_aggressiveness_fast_higher_kp():
    """Fast aggressiveness gives higher Kp than normal."""
    array = _make_array()
    baseline = [50.0] * 10
    response = [50.0, 100.0, 200.0, 278.0, 280.0, 280.0, 280.0, 280.0, 280.0]
    calibrator_normal, _ = _calibrator(
        [array], baseline + response, aggressiveness="normal"
    )
    calibrator_fast, _ = _calibrator(
        [array], baseline + response, aggressiveness="fast"
    )
    with patch("asyncio.sleep", new=AsyncMock()):
        result_normal = (await calibrator_normal.run())[0]
        result_fast = (await calibrator_fast.run())[0]
    assert result_fast.kp > result_normal.kp
