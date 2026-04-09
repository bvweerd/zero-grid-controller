"""Automatic step-response calibration for PV arrays."""

from __future__ import annotations

import asyncio
import logging
import time
from collections import deque
from collections.abc import Awaitable, Callable
from dataclasses import dataclass

from homeassistant.core import HomeAssistant

from .array import ArrayConfig
from .const import (
    CALIB_BASELINE_SAMPLES,
    CALIB_DEFAULT_FAIL_SETTLING_S,
    CALIB_GRID_VARIANCE_FACTOR,
    CALIB_INTER_ARRAY_SLEEP_S,
    CALIB_MAX_GRID_W,
    CALIB_MAX_TIME_S,
    CALIB_MIN_PV_W,
    CALIB_MIN_W_PER_UNIT,
    CALIB_NO_PV_SENSOR_NOTE,
    CALIB_PV_SENSOR_MAX_WAIT_S,
    CALIB_SETTLING_CONFIRM_COUNT,
    CALIB_SETTLING_MAX_S,
    CALIB_SETTLING_MIN_S,
    CALIB_SETTLING_THRESHOLD_W,
    CALIB_STABLE_VARIANCE_PCT,
    CALIB_STABLE_WINDOW_S,
    CALIB_STEP_MAX,
    CALIB_STEP_MIN,
    CALIB_STEP_RATIO,
    CALIBRATION_CONFIDENCE_ESTIMATED,
    DEFAULT_SETTLING_TIME_S,
    DEFAULT_W_PER_UNIT,
    OUTPUT_TYPE_SWITCH,
)

_LOGGER = logging.getLogger(__name__)


@dataclass
class CalibrationResult:
    """Result from a single array calibration run."""

    w_per_unit: float
    settling_time_s: int
    confidence: str  # "measured" | "estimated" | "failed"
    notes: str  # human-readable explanation for the user


class ArrayCalibrator:
    """Automatic step-response measurement for PV arrays.

    Procedure per array:
    1. Wait for stable conditions (PV variance < 5% over 30 s, or simply 30 s).
    2. Measure baseline grid_w (average of 10 samples at 1 s intervals).
    3. Send a test step: setpoint − STEP_SIZE units.
       Always clamped within [setpoint_min, setpoint_max].
    4. Measure grid_w every second.
    5. Detect settling: |grid_w_avg − new_baseline| < 5 W for 3 consecutive samples.
    6. Calculate:
         w_per_unit = abs(new_baseline − old_baseline) / STEP_SIZE
         settling_time_s = time to settling (clamped to [3, 60])
    7. Restore original setpoint, wait 30 s before next array.

    Safety:
    - Never step outside configured min/max.
    - Maximum 3 minutes per array.
    - Abort if grid_w goes outside ±3000 W.
    - On timeout/error: conservative defaults, confidence = "estimated".
    """

    def __init__(self) -> None:
        self._abort = False

    def abort(self) -> None:
        """Signal that calibration should stop at the next safe point."""
        self._abort = True

    async def run(
        self,
        hass: HomeAssistant,
        arrays: list[ArrayConfig],
        read_grid: Callable[[], float | None],
        write_setpoint: Callable[[ArrayConfig, float], Awaitable[None]],
        progress_callback: Callable[[str, float], None],
        *,
        calib_max_grid_w: float = CALIB_MAX_GRID_W,
    ) -> dict[str, CalibrationResult]:
        """Run calibration for all arrays and return results keyed by array name."""
        results: dict[str, CalibrationResult] = {}
        n = len(arrays)

        for i, array in enumerate(arrays):
            if self._abort:
                break

            base_progress = i / n
            progress_callback(
                f"calibration_measuring:{array.name}:{i + 1}/{n}",
                base_progress,
            )

            result = await self._calibrate_array(
                hass,
                array,
                read_grid,
                write_setpoint,
                lambda msg, p, bp=base_progress: progress_callback(msg, bp + p / n),  # type: ignore[misc]
                calib_max_grid_w=calib_max_grid_w,
            )
            results[array.name] = result
            _LOGGER.info(
                "Calibration %s: %s — %.0f W/unit, %d s settling (confidence: %s)",
                array.name,
                result.notes,
                result.w_per_unit,
                result.settling_time_s,
                result.confidence,
            )

            # Wait before next array to let grid stabilise
            if i < n - 1 and not self._abort:
                await asyncio.sleep(CALIB_INTER_ARRAY_SLEEP_S)

        progress_callback("calibration_complete", 1.0)
        return results

    # ------------------------------------------------------------------
    # Internal helpers
    # ------------------------------------------------------------------

    async def _calibrate_array(
        self,
        hass: HomeAssistant,
        array: ArrayConfig,
        read_grid: Callable[[], float | None],
        write_setpoint: Callable[[ArrayConfig, float], Awaitable[None]],
        progress_callback: Callable[[str, float], None],
        *,
        calib_max_grid_w: float = CALIB_MAX_GRID_W,
    ) -> CalibrationResult:
        """Calibrate a single array. Returns a CalibrationResult.

        When array.pv_power_entity is configured the step response is measured
        directly on the PV sensor (low noise, σ ≈ 5 W).  Without it the grid
        sensor is used (high noise, σ ≈ 30 W), which often prevents settling
        detection and yields a "failed" result.
        """
        if array.output_type == OUTPUT_TYPE_SWITCH:
            return CalibrationResult(
                w_per_unit=DEFAULT_W_PER_UNIT,
                settling_time_s=array.settling_time_s,
                confidence="failed",
                notes=f"{array.name}: switch outputs are not numerically calibratable.",
            )

        default = CalibrationResult(
            w_per_unit=DEFAULT_W_PER_UNIT,
            settling_time_s=DEFAULT_SETTLING_TIME_S,
            confidence=CALIBRATION_CONFIDENCE_ESTIMATED,
            notes="Using default values (calibration not run or insufficient solar output).",
        )

        use_pv_sensor = array.pv_power_entity is not None

        # --- Step 1: wait for stable conditions ---
        progress_callback("calibration_waiting_sun", 0.0)
        stable = await self._wait_for_stable(hass, array, read_grid)
        if not stable:
            return CalibrationResult(
                w_per_unit=DEFAULT_W_PER_UNIT,
                settling_time_s=DEFAULT_SETTLING_TIME_S,
                confidence="failed",
                notes=(
                    "Calibration not possible (insufficient or unstable solar output). "
                    "Integration starts with default values and learns automatically. "
                    "Tip: re-run calibration on a sunny day via Settings."
                ),
            )

        # --- Step 2: read original setpoint ---
        original_setpoint = self._read_setpoint(hass, array)
        if original_setpoint is None:
            return default

        # --- Step 3: measure baseline ---
        progress_callback("calibration_measuring_baseline", 0.1)
        if use_pv_sensor:
            assert (
                array.pv_power_entity is not None
            )  # guaranteed by use_pv_sensor check
            baseline = await self._measure_pv_avg(
                hass,
                array.pv_power_entity,
                samples=CALIB_BASELINE_SAMPLES,
            )
            # If PV sensor appears to be frozen (slow API), fall back to grid
            if baseline is None:
                _LOGGER.warning(
                    "Array %s: PV sensor did not update during baseline; falling back to grid signal",
                    array.name,
                )
                use_pv_sensor = False
                baseline = await self._measure_grid_avg(
                    read_grid, samples=CALIB_BASELINE_SAMPLES
                )
        else:
            baseline = await self._measure_grid_avg(
                read_grid, samples=CALIB_BASELINE_SAMPLES
            )

        if baseline is None:
            return default

        # --- Step 4: compute test step — adaptive: 10 % of usable range, min 2, max 20 units ---
        usable_range = array.setpoint_max - array.setpoint_min
        step = max(
            CALIB_STEP_MIN, min(CALIB_STEP_MAX, int(usable_range * CALIB_STEP_RATIO))
        )
        test_setpoint = max(
            array.setpoint_min,
            min(array.setpoint_max, original_setpoint - step),
        )
        if abs(test_setpoint - original_setpoint) < 1:
            # No room to tighten — try opening instead
            test_setpoint = min(
                array.setpoint_max,
                max(array.setpoint_min, original_setpoint + step),
            )
        if abs(test_setpoint - original_setpoint) < 1:
            _LOGGER.warning(
                "Array %s has no room to step (min=%.0f max=%.0f current=%.0f)",
                array.name,
                array.setpoint_min,
                array.setpoint_max,
                original_setpoint,
            )
            return default

        # Apply test step
        progress_callback("calibration_step_sent", 0.3)
        await write_setpoint(array, test_setpoint)
        step_size = abs(test_setpoint - original_setpoint)

        # --- Step 5: measure response ---
        start_t = time.monotonic()
        recent: deque[float] = deque(maxlen=CALIB_SETTLING_CONFIRM_COUNT)
        settled = False
        new_baseline = baseline
        elapsed = 0.0

        while elapsed < CALIB_MAX_TIME_S and not self._abort:
            await asyncio.sleep(1.0)
            elapsed = time.monotonic() - start_t

            # Safety check always uses grid
            grid_w = read_grid()
            if grid_w is not None and abs(grid_w) > calib_max_grid_w:
                _LOGGER.warning(
                    "Grid measurement %.0f W exceeds safety limit; aborting calibration for %s",
                    grid_w,
                    array.name,
                )
                await write_setpoint(array, original_setpoint)
                return default

            # Choose measurement signal
            if use_pv_sensor:
                signal = self._read_pv_safe(hass, array.pv_power_entity)  # type: ignore[arg-type]
            else:
                signal = grid_w

            if signal is None:
                continue

            recent.append(signal)
            # Only start checking after the inverter settling time has elapsed
            if (
                elapsed >= array.settling_time_s
                and len(recent) == CALIB_SETTLING_CONFIRM_COUNT
            ):
                avg = sum(recent) / len(recent)
                if all(abs(v - avg) < CALIB_SETTLING_THRESHOLD_W for v in recent):
                    new_baseline = avg
                    settled = True
                    break

            progress_callback(
                "calibration_step_sent", 0.3 + 0.6 * (elapsed / CALIB_MAX_TIME_S)
            )

        # --- Step 6: restore and compute results ---
        await write_setpoint(array, original_setpoint)

        if not settled:
            return CalibrationResult(
                w_per_unit=DEFAULT_W_PER_UNIT,
                settling_time_s=CALIB_DEFAULT_FAIL_SETTLING_S,
                confidence="failed",
                notes=f"{array.name}: no response detected, using default values.",
            )

        w_per_unit = abs(new_baseline - baseline) / step_size
        settling_time_s = max(
            CALIB_SETTLING_MIN_S, min(CALIB_SETTLING_MAX_S, int(elapsed))
        )

        if w_per_unit < CALIB_MIN_W_PER_UNIT:
            return CalibrationResult(
                w_per_unit=DEFAULT_W_PER_UNIT,
                settling_time_s=settling_time_s,
                confidence="failed",
                notes=f"{array.name}: response too small to measure reliably.",
            )

        progress_callback("calibration_result", 0.95)
        notes = (
            f"{array.name}: {w_per_unit:.0f} W/step, "
            f"{settling_time_s}s response time"
            f"{' (PV sensor)' if use_pv_sensor else ' (grid sensor — add PV sensor for accuracy)'}."
        )
        if not use_pv_sensor:
            notes += f" {CALIB_NO_PV_SENSOR_NOTE}"
        return CalibrationResult(
            w_per_unit=w_per_unit,
            settling_time_s=settling_time_s,
            confidence="measured",
            notes=notes,
        )

    async def _wait_for_stable(
        self,
        hass: HomeAssistant,
        array: ArrayConfig,
        read_grid: Callable[[], float | None],
    ) -> bool:
        """Wait up to _STABLE_WINDOW_S for stable PV output.

        Returns True if conditions are suitable for calibration.
        """
        if array.pv_power_entity is None:
            # No PV sensor — check grid stability instead of blindly waiting
            grid_samples: deque[float] = deque(maxlen=CALIB_STABLE_WINDOW_S)
            for _ in range(CALIB_STABLE_WINDOW_S):
                val = read_grid()
                if val is not None:
                    grid_samples.append(val)
                await asyncio.sleep(1.0)
            if len(grid_samples) < CALIB_STABLE_WINDOW_S // 2:
                return False
            avg = sum(grid_samples) / len(grid_samples)
            variance_pct = (
                (max(grid_samples) - min(grid_samples)) / max(abs(avg), 1.0) * 100
            )
            # Grid is inherently noisier than PV; allow CALIB_GRID_VARIANCE_FACTOR× the PV variance threshold
            return variance_pct < CALIB_STABLE_VARIANCE_PCT * CALIB_GRID_VARIANCE_FACTOR

        samples: deque[float] = deque(maxlen=CALIB_STABLE_WINDOW_S)
        deadline = time.monotonic() + CALIB_STABLE_WINDOW_S * 2

        while time.monotonic() < deadline:
            state = hass.states.get(array.pv_power_entity)
            if state and state.state not in ("unknown", "unavailable"):
                try:
                    pv_w = float(state.state)
                    samples.append(pv_w)
                except ValueError:
                    pass

            if len(samples) >= CALIB_STABLE_WINDOW_S:
                avg = sum(samples) / len(samples)
                if avg < CALIB_MIN_PV_W:
                    return False  # Not enough sun
                variance_pct = (max(samples) - min(samples)) / avg * 100
                if variance_pct < CALIB_STABLE_VARIANCE_PCT:
                    return True

            await asyncio.sleep(1.0)

        return len(samples) >= CALIB_STABLE_WINDOW_S // 2

    async def _measure_grid_avg(
        self,
        read_grid: Callable[[], float | None],
        samples: int = 10,
    ) -> float | None:
        """Measure average grid_w over `samples` seconds."""
        readings: list[float] = []
        for _ in range(samples):
            val = read_grid()
            if val is not None:
                readings.append(val)
            await asyncio.sleep(1.0)
        if not readings:
            return None
        return sum(readings) / len(readings)

    async def _measure_pv_avg(
        self,
        hass: HomeAssistant,
        entity_id: str,
        samples: int = 10,
    ) -> float | None:
        """Measure average PV power over `samples` distinct sensor updates.

        Handles slow inverter APIs (e.g. SolarEdge 10 s poll) by waiting up to
        CALIB_PV_SENSOR_MAX_WAIT_S for each new reading instead of counting
        duplicate stale values.  Returns None if the sensor never updates.
        """
        readings: list[float] = []
        last_changed: object = None

        while len(readings) < samples:
            state = hass.states.get(entity_id)
            if state is None or state.state in ("unknown", "unavailable"):
                await asyncio.sleep(1.0)
                continue

            if state.last_changed != last_changed:
                try:
                    readings.append(float(state.state))
                    last_changed = state.last_changed
                except ValueError:
                    pass

            if len(readings) < samples:
                # Wait up to CALIB_PV_SENSOR_MAX_WAIT_S for the next update
                waited = 0.0
                while waited < CALIB_PV_SENSOR_MAX_WAIT_S:
                    await asyncio.sleep(1.0)
                    waited += 1.0
                    new_state = hass.states.get(entity_id)
                    if new_state and new_state.last_changed != last_changed:
                        break
                else:
                    # Sensor did not update within the wait window → frozen
                    _LOGGER.debug(
                        "PV sensor %s did not update within %d s",
                        entity_id,
                        CALIB_PV_SENSOR_MAX_WAIT_S,
                    )
                    if not readings:
                        return None  # never got a single reading
                    break  # use what we have

        if not readings:
            return None
        return sum(readings) / len(readings)

    def _read_pv_safe(self, hass: HomeAssistant, entity_id: str) -> float | None:
        """Read a PV sensor state synchronously, returning None on any error."""
        state = hass.states.get(entity_id)
        if state is None or state.state in ("unknown", "unavailable"):
            return None
        try:
            return float(state.state)
        except ValueError:
            return None

    def _read_setpoint(self, hass: HomeAssistant, array: ArrayConfig) -> float | None:
        state = hass.states.get(array.setpoint_entity)
        if state is None or state.state in ("unknown", "unavailable"):
            return None
        try:
            return float(state.state)
        except ValueError:
            return None
