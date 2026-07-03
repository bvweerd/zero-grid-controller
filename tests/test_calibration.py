"""Tests for ArrayCalibrator."""

from __future__ import annotations

import contextlib
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from custom_components.zero_grid_controller.array import ArrayConfig
from custom_components.zero_grid_controller.calibrator import ArrayCalibrator


@pytest.fixture(autouse=True)
def auto_enable_custom_integrations(enable_custom_integrations):
    return


def _make_array(
    name="PV West",
    setpoint_max=100.0,
    setpoint_min=0.0,
    power_sensor_entity="sensor.pv_west_power",
    calibration_confidence="estimated",
) -> ArrayConfig:
    return ArrayConfig(
        name=name,
        output_type="percent",
        setpoint_entity="number.pv_west_limit",
        w_per_unit=23.0,
        calibration_confidence=calibration_confidence,
        setpoint_min=setpoint_min,
        setpoint_max=setpoint_max,
        settling_time_s=15,
        power_sensor_entity=power_sensor_entity,
    )


def _calibrator(arrays, sensor_readings, aggressiveness="normal"):
    """Build a calibrator with mock power sensor readings and write function."""
    states = {
        array.power_sensor_entity: SimpleNamespace(state="0")
        for array in arrays
        if array.power_sensor_entity
    }
    hass = MagicMock()
    hass.states.get.side_effect = states.get
    current_setpoints = {a.name: a.setpoint_max for a in arrays}
    read_iters = {
        entity_id: iter(values) for entity_id, values in sensor_readings.items()
    }

    def advance(entity_id: str) -> None:
        if entity_id not in read_iters:
            return
        with contextlib.suppress(StopIteration):
            states[entity_id].state = str(next(read_iters[entity_id]))

    write_setpoint = AsyncMock()
    return (
        ArrayCalibrator(
            hass=hass,
            arrays=arrays,
            current_setpoints=current_setpoints,
            aggressiveness=aggressiveness,
            write_setpoint=write_setpoint,
        ),
        write_setpoint,
        advance,
    )


def _stable_midpoint_readings() -> list[float]:
    return [300.0, 300.0, 300.0, 200.0, 200.0, 200.0, 300.0, 300.0, 300.0]


async def test_calibration_success():
    """Successful calibration returns correct midpoint slope and derived max power."""
    array = _make_array()
    readings = {"sensor.pv_west_power": _stable_midpoint_readings()}
    calibrator, _, advance = _calibrator([array], readings)

    async def fake_sleep(_secs):
        advance("sensor.pv_west_power")

    with patch("asyncio.sleep", new=fake_sleep):
        results = await calibrator.run()

    result = results[0]
    assert result.success is True
    assert result.w_per_unit == pytest.approx(10.0, abs=0.1)
    assert result.derived_max_power_w == pytest.approx(1000.0, abs=0.1)
    assert result.settling_time_s > 0
    assert result.kp == pytest.approx(1.0, rel=0.01)
    assert result.ki == pytest.approx(0.01, rel=0.01)


async def test_calibration_power_sensor_unavailable_fails():
    """Calibration fails when the power sensor cannot be read."""
    array = _make_array()
    calibrator, _, _ = _calibrator([array], {"sensor.pv_west_power": []})
    with patch("asyncio.sleep", new=AsyncMock()):
        results = await calibrator.run()
    assert results[0].success is False


async def test_calibration_requires_power_sensor():
    """Numeric arrays without a power sensor are not eligible."""
    array = _make_array(power_sensor_entity=None)
    calibrator, _, _ = _calibrator([array], {})
    with patch("asyncio.sleep", new=AsyncMock()):
        results = await calibrator.run()
    assert results[0].success is False


async def test_calibration_timeout_fails():
    """Calibration fails when power never settles."""
    array = _make_array()
    readings = {"sensor.pv_west_power": [300.0, 250.0, 310.0, 260.0] * 50}
    calibrator, _, advance = _calibrator([array], readings)

    async def fake_sleep(_secs):
        advance("sensor.pv_west_power")

    with patch("asyncio.sleep", new=fake_sleep):
        results = await calibrator.run()
    assert results[0].success is False


async def test_calibration_tiny_response_fails():
    """Calibration fails when the 30%→20% delta is too small."""
    array = _make_array()
    readings = {
        "sensor.pv_west_power": [
            100.0,
            100.0,
            100.0,
            99.9,
            99.9,
            99.9,
            100.0,
            100.0,
            100.0,
        ]
    }
    calibrator, _, advance = _calibrator([array], readings)

    async def fake_sleep(_secs):
        advance("sensor.pv_west_power")

    with patch("asyncio.sleep", new=fake_sleep):
        results = await calibrator.run()
    assert results[0].success is False


async def test_calibration_restores_setpoint_on_success():
    """Calibration restores the original setpoint after midpoint test."""
    array = _make_array()
    readings = {"sensor.pv_west_power": _stable_midpoint_readings()}
    calibrator, write_sp, advance = _calibrator([array], readings)

    async def fake_sleep(_secs):
        advance("sensor.pv_west_power")

    with patch("asyncio.sleep", new=fake_sleep):
        await calibrator.run()

    assert write_sp.call_args_list[-1].args[1] == pytest.approx(array.setpoint_max)


async def test_calibration_abort_stops_early():
    """abort() prevents calibrating further arrays."""
    arrays = [_make_array("Array1"), _make_array("Array2")]
    readings = {"sensor.pv_west_power": _stable_midpoint_readings()}
    calibrator, _, _ = _calibrator(arrays, readings)

    async def sleep_and_abort(_secs):
        calibrator.abort()

    with patch("asyncio.sleep", new=sleep_and_abort):
        results = await calibrator.run()

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
    calibrator, _, _ = _calibrator([switch_array], {})
    with patch("asyncio.sleep", new=AsyncMock()):
        results = await calibrator.run()
    assert results == []


async def test_calibration_aggressiveness_uses_total_measured_plant_gain():
    """Global gains use all measured arrays instead of the latest array only."""
    arrays = [
        _make_array("Array1", power_sensor_entity="sensor.array1_power"),
        _make_array(
            "Array2",
            power_sensor_entity="sensor.array2_power",
            calibration_confidence="measured",
        ),
    ]
    arrays[1].w_per_unit = 15.0
    readings = {"sensor.array1_power": _stable_midpoint_readings()}
    calibrator, _, advance = _calibrator(arrays, readings)

    async def fake_sleep(_secs):
        advance("sensor.array1_power")

    with patch("asyncio.sleep", new=fake_sleep):
        results = await calibrator.run()

    assert results[0].success
    assert results[0].kp == pytest.approx(1.0, rel=0.01)


async def test_calibration_aggressiveness_fast_higher_kp():
    """Fast aggressiveness gives higher Kp than normal."""
    normal_array = _make_array()
    fast_array = _make_array()
    normal_readings = {"sensor.pv_west_power": _stable_midpoint_readings()}
    fast_readings = {"sensor.pv_west_power": _stable_midpoint_readings()}
    calibrator_normal, _, advance_normal = _calibrator(
        [normal_array], normal_readings, aggressiveness="normal"
    )
    calibrator_fast, _, advance_fast = _calibrator(
        [fast_array], fast_readings, aggressiveness="fast"
    )

    async def sleep_normal(_secs):
        advance_normal("sensor.pv_west_power")

    async def sleep_fast(_secs):
        advance_fast("sensor.pv_west_power")

    with patch("asyncio.sleep", new=sleep_normal):
        result_normal = (await calibrator_normal.run())[0]
    with patch("asyncio.sleep", new=sleep_fast):
        result_fast = (await calibrator_fast.run())[0]

    assert result_fast.kp > result_normal.kp


async def test_calibration_uses_slower_return_direction_for_settling_time():
    """The stored settling time should reflect the slowest direction."""
    array = _make_array()
    readings = {
        "sensor.pv_west_power": [
            300.0,
            300.0,
            300.0,
            200.0,
            200.0,
            200.0,
            240.0,
            270.0,
            300.0,
            300.0,
            300.0,
        ]
    }
    calibrator, _, advance = _calibrator([array], readings)

    async def fake_sleep(_secs):
        advance("sensor.pv_west_power")

    with patch("asyncio.sleep", new=fake_sleep):
        result = (await calibrator.run())[0]

    assert result.success is True
    # Raw slowest settling = 25 s (return direction took 5 samples × 5 s).
    # Stored value includes the 1.5× safety margin: int(25 * 1.5) = 37 s.
    assert result.settling_time_s == pytest.approx(37)


async def test_calibration_aborts_when_grid_limit_exceeded():
    """Calibration fails the array when |grid| exceeds CALIB_MAX_GRID_W."""
    array = _make_array()
    readings = {"sensor.pv_west_power": _stable_midpoint_readings()}
    calibrator, write_setpoint, advance = _calibrator([array], readings)

    async def read_grid() -> float:
        return 5000.0  # way beyond CALIB_MAX_GRID_W

    calibrator._read_grid = read_grid

    async def fake_sleep(_secs):
        advance("sensor.pv_west_power")

    with patch("asyncio.sleep", new=fake_sleep):
        result = (await calibrator.run())[0]

    assert result.success is False
    assert "exceeded" in result.message
    # Original setpoint restored despite the abort
    write_setpoint.assert_awaited_with(array, array.setpoint_max)


async def test_calibration_grid_guard_ignores_unavailable_grid():
    """A grid read returning None must not abort calibration."""
    array = _make_array()
    readings = {"sensor.pv_west_power": _stable_midpoint_readings()}
    calibrator, _, advance = _calibrator([array], readings)

    async def read_grid() -> None:
        return None

    calibrator._read_grid = read_grid

    async def fake_sleep(_secs):
        advance("sensor.pv_west_power")

    with patch("asyncio.sleep", new=fake_sleep):
        result = (await calibrator.run())[0]

    assert result.success is True
