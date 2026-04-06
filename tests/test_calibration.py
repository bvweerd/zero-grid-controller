"""Realistic calibration scenario tests.

System context (from live battery_controller diagnostics):
  House in Amsterdam — 4 PV arrays on one SolarEdge inverter plus a shed inverter:
    PV Zuid     1.0 kWp  south (180°)  tilt 45°  ~960 W peak  → ~10 W/%
    PV West     2.4 kWp  west  (270°)  tilt 45°  ~2304 W peak → ~23 W/%
    PV Oost     2.4 kWp  east  (90°)   tilt 45°  ~2304 W peak → ~23 W/%
    PV Prieeltje 0.5 kWp south (180°)  tilt 20°  ~480 W peak  →  ~5 W/%
  Setpoint entities: number.pv_*_limit (0–100 %)
  Grid sensor: positive = importing, negative = exporting
  Normal settling time: 15 s (inverter_speed="normal")

Calibration procedure:
  1. Wait for stable PV (or skip wait when no PV sensor).
  2. Measure baseline grid over 10 s.
  3. Apply a −10 % step on the limit.
  4. Wait for grid to settle (3 consecutive readings within 5 W).
  5. Compute w_per_unit = Δgrid / 10.
  6. Restore original setpoint.
"""
from __future__ import annotations

import asyncio
import time
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from custom_components.zero_grid_controller.array import ArrayConfig
from custom_components.zero_grid_controller.calibrator import ArrayCalibrator


# ---------------------------------------------------------------------------
# Minimal fake HA infrastructure
# ---------------------------------------------------------------------------

class _State:
    def __init__(self, value: str) -> None:
        self.state = value
        self.attributes: dict = {}


class _SequenceStates:
    """hass.states mock that serves per-entity value sequences."""

    def __init__(self) -> None:
        self._fixed: dict[str, str] = {}
        self._seqs: dict[str, list[str]] = {}
        self._idx: dict[str, int] = {}

    def set(self, entity_id: str, value: str) -> None:
        self._fixed[entity_id] = value

    def set_sequence(self, entity_id: str, values: list[str]) -> None:
        self._seqs[entity_id] = values
        self._idx[entity_id] = 0

    def get(self, entity_id: str) -> _State | None:
        if entity_id in self._seqs:
            idx = self._idx.get(entity_id, 0)
            seq = self._seqs[entity_id]
            self._idx[entity_id] = idx + 1
            return _State(seq[min(idx, len(seq) - 1)])
        if entity_id in self._fixed:
            return _State(self._fixed[entity_id])
        return None


def _make_hass(states: _SequenceStates) -> MagicMock:
    hass = MagicMock()
    hass.states.get = states.get
    hass.services.async_call = AsyncMock()
    return hass


class _FakeMonotonic:
    """Fake time.monotonic that increments by 1.0 on each call."""

    def __init__(self, start: float = 0.0) -> None:
        self._t = start

    def __call__(self) -> float:
        t = self._t
        self._t += 1.0
        return t


def _written_setpoints(hass: MagicMock) -> list[float]:
    """Return all 'value' arguments passed to number.set_value calls."""
    return [
        call.args[2]["value"]
        for call in hass.services.async_call.call_args_list
        if len(call.args) > 2
        and isinstance(call.args[2], dict)
        and "value" in call.args[2]
    ]


# ---------------------------------------------------------------------------
# Array fixtures
# ---------------------------------------------------------------------------

def _pv_west(pv_sensor: bool = False) -> ArrayConfig:
    """PV West 2.4 kWp — west-facing, ~23 W/% at full output."""
    return ArrayConfig(
        name="PV West",
        enabled=True,
        output_type="percent",
        setpoint_entity="number.pv_west_limit",
        pv_power_entity="sensor.pv_west_power" if pv_sensor else None,
        w_per_unit=23.0,
        calibration_confidence="estimated",
        setpoint_min=0.0,
        setpoint_max=100.0,
        settling_time_s=15,
        priority=1,
    )


def _pv_zuidarray() -> ArrayConfig:
    """PV Zuid 1.0 kWp — south-facing, ~10 W/%."""
    return ArrayConfig(
        name="PV Zuid",
        enabled=True,
        output_type="percent",
        setpoint_entity="number.pv_zuidlimit",
        pv_power_entity=None,
        w_per_unit=10.0,
        calibration_confidence="estimated",
        setpoint_min=0.0,
        setpoint_max=100.0,
        settling_time_s=15,
        priority=2,
    )


# ---------------------------------------------------------------------------
# Test 1: Successful calibration — PV West without PV power sensor
#
# Afternoon scenario: west roof at 80 % limit, slight grid import of +50 W
# from the battery controller's zero-grid offset. After tightening to 70 %,
# the grid rises to ~280 W (ΔP ≈ 230 W / 10 % = 23 W/%).
# ---------------------------------------------------------------------------

async def test_calibration_pv_west_success() -> None:
    states = _SequenceStates()
    # Current setpoint: 80 %
    states.set("number.pv_west_limit", "80.0")
    # Grid: 30 stability reads + 10 baseline reads at +50 W, then rises as PV is curtailed
    # Settling detection needs elapsed >= 15 s AND 5 stable readings (within 5 W of avg)
    states.set_sequence(
        "sensor.grid_power",
        ["50.0"] * 40
        + ["120.0", "150.0", "180.0", "210.0", "230.0", "245.0", "255.0",
           "262.0", "267.0", "270.0", "273.0", "275.0", "277.0", "279.0", "280.0"]
        + ["280.0"] * 10,
    )

    hass = _make_hass(states)
    calibrator = ArrayCalibrator()

    with pytest.MonkeyPatch().context() as mp:
        mp.setattr(asyncio, "sleep", AsyncMock())
        with patch("custom_components.zero_grid_controller.calibrator.time.monotonic", _FakeMonotonic()):
            results = await calibrator.run(
                hass, [_pv_west()], "sensor.grid_power", False, lambda *_: None
            )

    result = results["PV West"]
    assert result.confidence == "measured", result.notes
    # Δgrid = 280 − 50 = 230 W, step = 10 % → w_per_unit ≈ 23 W/%  (±5)
    assert 18.0 <= result.w_per_unit <= 28.0
    assert result.settling_time_s >= 3
    # Setpoint must be restored to 80 % after the measurement
    assert 80.0 in _written_setpoints(hass)


# ---------------------------------------------------------------------------
# Test 2: Evening — no sun, calibration returns confidence="failed"
#
# Diagnostics captured at 20:00 local (SOC 98 %, production 0 W).
# With a PV power sensor the stability check detects avg < 100 W and bails.
# ---------------------------------------------------------------------------

async def test_calibration_no_sun_fails() -> None:
    states = _SequenceStates()
    states.set("number.pv_west_limit", "80.0")
    # PV power sensor returns 0 W throughout
    states.set_sequence("sensor.pv_west_power", ["0.0"] * 120)
    states.set("sensor.grid_power", "161.0")  # 161 W import as in diagnostics

    hass = _make_hass(states)
    calibrator = ArrayCalibrator()

    with pytest.MonkeyPatch().context() as mp:
        mp.setattr(asyncio, "sleep", AsyncMock())
        results = await calibrator.run(
            hass, [_pv_west(pv_sensor=True)], "sensor.grid_power", False, lambda *_: None
        )

    assert results["PV West"].confidence == "failed"


# ---------------------------------------------------------------------------
# Test 3: Inverter at setpoint_min — no room to step, returns defaults
#
# Can happen when a previous calibration already drove the limit to 0 %
# and the user triggers recalibration without first opening the limit.
# ---------------------------------------------------------------------------

async def test_calibration_no_step_room_returns_defaults() -> None:
    states = _SequenceStates()
    states.set("number.pv_west_limit", "0.0")   # at minimum already
    states.set("sensor.grid_power", "50.0")

    hass = _make_hass(states)
    array = _pv_west()
    array.setpoint_min = 0.0
    array.setpoint_max = 0.0   # range collapsed — no room
    calibrator = ArrayCalibrator()

    with pytest.MonkeyPatch().context() as mp:
        mp.setattr(asyncio, "sleep", AsyncMock())
        results = await calibrator.run(
            hass, [array], "sensor.grid_power", False, lambda *_: None
        )

    result = results["PV West"]
    assert result.confidence in ("estimated", "failed")
    assert result.w_per_unit == pytest.approx(10.0)   # safe default


# ---------------------------------------------------------------------------
# Test 4: Grid safety limit — aborts when |grid| > 3000 W
#
# Simulates the Marstek battery suddenly starting a full-power discharge
# (1210 W) while calibration is running, pushing an already-exporting grid
# well past the ±3000 W safety threshold.
# ---------------------------------------------------------------------------

async def test_calibration_grid_safety_abort_restores_setpoint() -> None:
    states = _SequenceStates()
    states.set("number.pv_west_limit", "80.0")
    # 40 stable reads (stability + baseline), then a huge spike during response measurement
    states.set_sequence(
        "sensor.grid_power",
        ["50.0"] * 40 + ["3500.0"] + ["3500.0"] * 30,
    )

    hass = _make_hass(states)
    calibrator = ArrayCalibrator()

    with pytest.MonkeyPatch().context() as mp:
        mp.setattr(asyncio, "sleep", AsyncMock())
        with patch("custom_components.zero_grid_controller.calibrator.time.monotonic", _FakeMonotonic()):
            results = await calibrator.run(
                hass, [_pv_west()], "sensor.grid_power", False, lambda *_: None
            )

    assert results["PV West"].confidence in ("estimated", "failed")
    # Original setpoint (80 %) must be written back after the abort
    assert 80.0 in _written_setpoints(hass)


# ---------------------------------------------------------------------------
# Test 5: Multi-array — abort() stops after the first array is calibrated
#
# Real-world use: user presses "Stop calibration" mid-run while PV West is
# being measured; PV Zuid and later arrays must not be touched.
# ---------------------------------------------------------------------------

async def test_calibration_abort_stops_at_first_array() -> None:
    states = _SequenceStates()
    for eid in ("number.pv_west_limit", "number.pv_zuidlimit"):
        states.set(eid, "80.0")
    states.set("sensor.grid_power", "50.0")

    hass = _make_hass(states)
    arrays = [_pv_west(), _pv_zuidarray()]
    calibrator = ArrayCalibrator()

    sleep_count = 0

    async def _aborting_sleep(t: float) -> None:
        nonlocal sleep_count
        sleep_count += 1
        # Signal abort on the very first sleep (stability wait for PV West)
        if sleep_count == 1:
            calibrator.abort()

    with pytest.MonkeyPatch().context() as mp:
        mp.setattr(asyncio, "sleep", _aborting_sleep)
        results = await calibrator.run(
            hass, arrays, "sensor.grid_power", False, lambda *_: None
        )

    # Only PV West should appear; PV Zuid must have been skipped
    assert "PV Zuid" not in results


# ---------------------------------------------------------------------------
# Test 6: Inverted sign — grid sensor reports export as positive
#
# Some meters (e.g. P1 DSMR) report net grid as "delivered to network" =
# export positive.  The invert_sign flag must flip the reading so calibration
# still measures the correct Δgrid.
# ---------------------------------------------------------------------------

async def test_calibration_inverted_sign_measures_correctly() -> None:
    states = _SequenceStates()
    states.set("number.pv_west_limit", "80.0")
    # Sensor reports −50 W (export-positive convention).  After curtailing,
    # less is exported so the reading moves towards 0.
    # 30 stability reads + 10 baseline reads at -50 W, then response (with invert_sign=True).
    # Settling: elapsed >= 15 s AND 5 stable readings within 5 W of avg.
    states.set_sequence(
        "sensor.grid_power",
        ["-50.0"] * 40
        + ["-150.0", "-200.0", "-240.0", "-260.0", "-270.0",
           "-275.0", "-277.0", "-279.0", "-280.0", "-280.0"]
        + ["-280.0"] * 10,
    )

    hass = _make_hass(states)
    calibrator = ArrayCalibrator()

    with pytest.MonkeyPatch().context() as mp:
        mp.setattr(asyncio, "sleep", AsyncMock())
        with patch("custom_components.zero_grid_controller.calibrator.time.monotonic", _FakeMonotonic()):
            # invert_sign=True flips reading: −50 → +50, −280 → +280
            results = await calibrator.run(
                hass, [_pv_west()], "sensor.grid_power", True, lambda *_: None
            )

    result = results["PV West"]
    assert result.confidence == "measured", result.notes
    # Δgrid (after inversion) = 280 − 50 = 230 W / 10 % ≈ 23 W/%
    assert 18.0 <= result.w_per_unit <= 28.0


# ---------------------------------------------------------------------------
# Test 7: Unavailable setpoint — returns default when setpoint entity is unavailable
# ---------------------------------------------------------------------------

async def test_calibration_setpoint_unavailable_returns_default() -> None:
    states = _SequenceStates()
    # Setpoint entity is unavailable
    states.set("number.pv_west_limit", "unavailable")
    states.set("sensor.grid_power", "50.0")

    hass = _make_hass(states)
    calibrator = ArrayCalibrator()

    with pytest.MonkeyPatch().context() as mp:
        mp.setattr(asyncio, "sleep", AsyncMock())
        with patch("custom_components.zero_grid_controller.calibrator.time.monotonic", _FakeMonotonic()):
            results = await calibrator.run(
                hass, [_pv_west()], "sensor.grid_power", False, lambda *_: None
            )

    assert results["PV West"].confidence in ("estimated",)


# ---------------------------------------------------------------------------
# Test 8: Grid unavailable during baseline → returns default
# ---------------------------------------------------------------------------

async def test_calibration_baseline_unavailable_returns_default() -> None:
    states = _SequenceStates()
    # Setpoint is fine but grid becomes unavailable during baseline measurement
    states.set("number.pv_west_limit", "80.0")
    # Stability reads return valid values, but baseline reads return None/unavailable
    states.set_sequence(
        "sensor.grid_power",
        ["50.0"] * 30 + ["unavailable"] * 20,
    )

    hass = _make_hass(states)
    calibrator = ArrayCalibrator()

    with pytest.MonkeyPatch().context() as mp:
        mp.setattr(asyncio, "sleep", AsyncMock())
        with patch("custom_components.zero_grid_controller.calibrator.time.monotonic", _FakeMonotonic()):
            results = await calibrator.run(
                hass, [_pv_west()], "sensor.grid_power", False, lambda *_: None
            )

    assert results["PV West"].confidence in ("estimated",)


# ---------------------------------------------------------------------------
# Test 9: Grid reads None during response loop (continue)
# ---------------------------------------------------------------------------

async def test_calibration_grid_none_during_loop() -> None:
    states = _SequenceStates()
    states.set("number.pv_west_limit", "80.0")
    # 30 stability + 10 baseline, then None values then recovery
    states.set_sequence(
        "sensor.grid_power",
        ["50.0"] * 40
        + ["unavailable"] * 5  # these return None in _read_grid
        + ["280.0"] * 25,
    )

    hass = _make_hass(states)
    calibrator = ArrayCalibrator()

    with pytest.MonkeyPatch().context() as mp:
        mp.setattr(asyncio, "sleep", AsyncMock())
        with patch("custom_components.zero_grid_controller.calibrator.time.monotonic", _FakeMonotonic()):
            results = await calibrator.run(
                hass, [_pv_west()], "sensor.grid_power", False, lambda *_: None
            )

    # Should complete (either measured or failed, but not crash)
    assert "PV West" in results


# ---------------------------------------------------------------------------
# Test 10: Small response → w_per_unit < 0.5 → confidence "failed"
# ---------------------------------------------------------------------------

async def test_calibration_tiny_response_fails() -> None:
    states = _SequenceStates()
    states.set("number.pv_west_limit", "80.0")
    # Baseline = 50 W, after step = 50.1 W (barely any response → < 0.5 W/unit)
    states.set_sequence(
        "sensor.grid_power",
        ["50.0"] * 40 + ["50.1"] * 30,
    )

    hass = _make_hass(states)
    calibrator = ArrayCalibrator()

    with pytest.MonkeyPatch().context() as mp:
        mp.setattr(asyncio, "sleep", AsyncMock())
        with patch("custom_components.zero_grid_controller.calibrator.time.monotonic", _FakeMonotonic()):
            results = await calibrator.run(
                hass, [_pv_west()], "sensor.grid_power", False, lambda *_: None
            )

    assert results["PV West"].confidence == "failed"
    assert "response too small" in results["PV West"].notes


# ---------------------------------------------------------------------------
# Test 11: Multi-array without abort — inter-array sleep called
# ---------------------------------------------------------------------------

async def test_calibration_multi_array_inter_array_sleep() -> None:
    states = _SequenceStates()
    for eid in ("number.pv_west_limit", "number.pv_zuidlimit"):
        states.set(eid, "80.0")
    # Simple: both grids stable, small response (will fail or estimate, doesn't matter)
    states.set("sensor.grid_power", "50.0")

    hass = _make_hass(states)
    arrays = [_pv_west(), _pv_zuidarray()]
    calibrator = ArrayCalibrator()

    sleep_times = []

    async def _record_sleep(t: float) -> None:
        sleep_times.append(t)

    with pytest.MonkeyPatch().context() as mp:
        mp.setattr(asyncio, "sleep", _record_sleep)
        with patch("custom_components.zero_grid_controller.calibrator.time.monotonic", _FakeMonotonic()):
            results = await calibrator.run(
                hass, arrays, "sensor.grid_power", False, lambda *_: None
            )

    # Both arrays should be in results
    assert "PV West" in results
    assert "PV Zuid" in results
    # The 30-second inter-array sleep should have been called (value == 30)
    assert 30 in sleep_times


# ---------------------------------------------------------------------------
# Test 12: _wait_for_stable with PV sensor — stable (early return = True)
# ---------------------------------------------------------------------------

async def test_calibration_wait_stable_pv_sensor_stable() -> None:
    states = _SequenceStates()
    states.set("number.pv_west_limit", "80.0")
    # PV at steady 1000 W for enough readings → avg > 100, variance < 5%
    states.set_sequence(
        "sensor.pv_west_power",
        ["1000.0"] * 100,
    )
    # Baseline and response: stable grid
    states.set_sequence(
        "sensor.grid_power",
        ["50.0"] * 10
        + ["280.0"] * 30,
    )

    hass = _make_hass(states)
    calibrator = ArrayCalibrator()

    with pytest.MonkeyPatch().context() as mp:
        mp.setattr(asyncio, "sleep", AsyncMock())
        with patch("custom_components.zero_grid_controller.calibrator.time.monotonic", _FakeMonotonic(start=0.0)):
            results = await calibrator.run(
                hass, [_pv_west(pv_sensor=True)], "sensor.grid_power", False, lambda *_: None
            )

    # Calibration completed (may be measured or failed, but did not abort early)
    assert "PV West" in results


# ---------------------------------------------------------------------------
# Test 13: _wait_for_stable with PV sensor — deadline expires, enough samples
# ---------------------------------------------------------------------------

async def test_calibration_wait_stable_deadline_expires() -> None:
    states = _SequenceStates()
    states.set("number.pv_west_limit", "80.0")
    # PV: varied readings that never satisfy stability (too variable)
    pv_values = []
    for i in range(120):
        pv_values.append(str(200 + (i % 20) * 100))  # oscillates 200-2100
    states.set_sequence("sensor.pv_west_power", pv_values)
    states.set("sensor.grid_power", "50.0")

    hass = _make_hass(states)
    calibrator = ArrayCalibrator()
    # monotonic always returns a large value so deadline is immediately past
    import time as _time
    large_t = [10000.0]

    def _always_past():
        t = large_t[0]
        large_t[0] += 1.0
        return t

    with pytest.MonkeyPatch().context() as mp:
        mp.setattr(asyncio, "sleep", AsyncMock())
        with patch("custom_components.zero_grid_controller.calibrator.time.monotonic", _always_past):
            results = await calibrator.run(
                hass, [_pv_west(pv_sensor=True)], "sensor.grid_power", False, lambda *_: None
            )

    assert "PV West" in results


# ---------------------------------------------------------------------------
# Test 14: _read_grid with ValueError → returns None
# ---------------------------------------------------------------------------

async def test_calibration_read_grid_value_error() -> None:
    states = _SequenceStates()
    states.set("sensor.grid_power", "not_a_number")

    hass = _make_hass(states)
    calibrator = ArrayCalibrator()

    result = calibrator._read_grid(hass, "sensor.grid_power", False)
    assert result is None


# ---------------------------------------------------------------------------
# Test 15: _read_grid with unavailable state → returns None
# ---------------------------------------------------------------------------

async def test_calibration_read_grid_unavailable() -> None:
    states = _SequenceStates()
    states.set("sensor.grid_power", "unavailable")

    hass = _make_hass(states)
    calibrator = ArrayCalibrator()

    result = calibrator._read_grid(hass, "sensor.grid_power", False)
    assert result is None


# ---------------------------------------------------------------------------
# Test 16: _read_grid with None state → returns None
# ---------------------------------------------------------------------------

async def test_calibration_read_grid_no_state() -> None:
    states = _SequenceStates()
    # Don't set sensor.missing → state.get() returns None

    hass = _make_hass(states)
    calibrator = ArrayCalibrator()

    result = calibrator._read_grid(hass, "sensor.missing", False)
    assert result is None


# ---------------------------------------------------------------------------
# Test 17: _read_setpoint with unavailable → returns None
# ---------------------------------------------------------------------------

async def test_calibration_read_setpoint_unavailable() -> None:
    states = _SequenceStates()
    states.set("number.sp", "unavailable")

    hass = _make_hass(states)
    calibrator = ArrayCalibrator()

    array = _pv_west()
    array.setpoint_entity = "number.sp"
    result = calibrator._read_setpoint(hass, array)
    assert result is None


# ---------------------------------------------------------------------------
# Test 18: _read_setpoint with non-numeric → returns None
# ---------------------------------------------------------------------------

async def test_calibration_read_setpoint_value_error() -> None:
    states = _SequenceStates()
    states.set("number.sp", "not_a_number")

    hass = _make_hass(states)
    calibrator = ArrayCalibrator()

    array = _pv_west()
    array.setpoint_entity = "number.sp"
    result = calibrator._read_setpoint(hass, array)
    assert result is None


# ---------------------------------------------------------------------------
# Test 19: _write_setpoint with OUTPUT_TYPE_SWITCH — calls switch service
# ---------------------------------------------------------------------------

async def test_calibration_write_setpoint_switch_type() -> None:
    states = _SequenceStates()
    hass = _make_hass(states)
    calibrator = ArrayCalibrator()

    from custom_components.zero_grid_controller.const import OUTPUT_TYPE_SWITCH

    switch_array = ArrayConfig(
        name="Load Switch",
        enabled=True,
        output_type=OUTPUT_TYPE_SWITCH,
        setpoint_entity="switch.load",
        pv_power_entity=None,
        w_per_unit=500.0,
        calibration_confidence="estimated",
        setpoint_min=0.0,
        setpoint_max=1.0,
        settling_time_s=5,
        priority=1,
    )

    # Write value > 0 → turn_on
    await calibrator._write_setpoint(hass, switch_array, 1.0)
    # Write value == 0 → turn_off
    await calibrator._write_setpoint(hass, switch_array, 0.0)

    calls = hass.services.async_call.call_args_list
    services = [c.args[1] for c in calls]
    assert "turn_on" in services
    assert "turn_off" in services


# ---------------------------------------------------------------------------
# Test 20: _wait_for_stable without PV sensor, insufficient grid samples
# ---------------------------------------------------------------------------

async def test_calibration_wait_stable_no_pv_insufficient_samples() -> None:
    states = _SequenceStates()
    states.set("number.pv_west_limit", "80.0")
    # Only a few valid readings (rest unavailable) → len < STABLE_WINDOW_S // 2
    # STABLE_WINDOW_S = 30, so need < 15 valid readings
    states.set_sequence(
        "sensor.grid_power",
        ["unavailable"] * 25 + ["50.0"] * 5,
    )

    hass = _make_hass(states)
    calibrator = ArrayCalibrator()

    with pytest.MonkeyPatch().context() as mp:
        mp.setattr(asyncio, "sleep", AsyncMock())
        results = await calibrator.run(
            hass, [_pv_west()], "sensor.grid_power", False, lambda *_: None
        )

    assert results["PV West"].confidence == "failed"


# ---------------------------------------------------------------------------
# Test 21: _read_setpoint with None state → returns None
# ---------------------------------------------------------------------------

async def test_calibration_read_setpoint_none_state() -> None:
    states = _SequenceStates()
    # Don't set the setpoint entity

    hass = _make_hass(states)
    calibrator = ArrayCalibrator()

    array = _pv_west()
    array.setpoint_entity = "number.missing"
    result = calibrator._read_setpoint(hass, array)
    assert result is None


# ---------------------------------------------------------------------------
# Test 22: _wait_for_stable with PV sensor — ValueError in float parsing
# ---------------------------------------------------------------------------

async def test_calibration_wait_stable_pv_value_error() -> None:
    """ValueError when parsing PV sensor state is silently handled."""
    states = _SequenceStates()
    states.set("number.pv_west_limit", "80.0")
    # PV returns non-numeric values
    states.set_sequence(
        "sensor.pv_west_power",
        ["not_a_number"] * 60 + ["1000.0"] * 60,
    )
    states.set("sensor.grid_power", "50.0")

    hass = _make_hass(states)
    calibrator = ArrayCalibrator()

    with pytest.MonkeyPatch().context() as mp:
        mp.setattr(asyncio, "sleep", AsyncMock())
        with patch("custom_components.zero_grid_controller.calibrator.time.monotonic", _FakeMonotonic(start=0.0)):
            # Should not raise
            results = await calibrator.run(
                hass, [_pv_west(pv_sensor=True)], "sensor.grid_power", False, lambda *_: None
            )

    assert "PV West" in results
