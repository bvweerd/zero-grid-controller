"""Automatic step-response calibration for PV arrays."""

from __future__ import annotations

import asyncio
import logging
import time
from collections import deque
from collections.abc import Callable
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
        grid_entity: str,
        invert_sign: bool,
        progress_callback: Callable[[str, float], None],
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
                grid_entity,
                invert_sign,
                lambda msg, p, bp=base_progress: progress_callback(msg, bp + p / n),  # type: ignore[misc]
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
        grid_entity: str,
        invert_sign: bool,
        progress_callback: Callable[[str, float], None],
    ) -> CalibrationResult:
        """Calibrate a single array. Returns a CalibrationResult."""
        default = CalibrationResult(
            w_per_unit=DEFAULT_W_PER_UNIT,
            settling_time_s=DEFAULT_SETTLING_TIME_S,
            confidence=CALIBRATION_CONFIDENCE_ESTIMATED,
            notes="Using default values (calibration not run or insufficient solar output).",
        )

        # --- Step 1: wait for stable conditions ---
        progress_callback("calibration_waiting_sun", 0.0)
        stable = await self._wait_for_stable(hass, array, grid_entity, invert_sign)
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

        # --- Step 3: measure baseline grid_w ---
        progress_callback("calibration_measuring_baseline", 0.1)
        baseline = await self._measure_grid_avg(
            hass, grid_entity, invert_sign, samples=CALIB_BASELINE_SAMPLES
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
        await self._write_setpoint(hass, array, test_setpoint)
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
            grid_w = self._read_grid(hass, grid_entity, invert_sign)
            if grid_w is None:
                continue

            if abs(grid_w) > CALIB_MAX_GRID_W:
                _LOGGER.warning(
                    "Grid measurement %.0f W exceeds safety limit; aborting calibration for %s",
                    grid_w,
                    array.name,
                )
                await self._write_setpoint(hass, array, original_setpoint)
                return default

            recent.append(grid_w)
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
        await self._write_setpoint(hass, array, original_setpoint)

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
        return CalibrationResult(
            w_per_unit=w_per_unit,
            settling_time_s=settling_time_s,
            confidence="measured",
            notes=(
                f"{array.name}: {w_per_unit:.0f} W/step, "
                f"{settling_time_s}s response time."
            ),
        )

    async def _wait_for_stable(
        self,
        hass: HomeAssistant,
        array: ArrayConfig,
        grid_entity: str,
        invert_sign: bool,
    ) -> bool:
        """Wait up to _STABLE_WINDOW_S for stable PV output.

        Returns True if conditions are suitable for calibration.
        """
        if array.pv_power_entity is None:
            # No PV sensor — check grid stability instead of blindly waiting
            grid_samples: deque[float] = deque(maxlen=CALIB_STABLE_WINDOW_S)
            for _ in range(CALIB_STABLE_WINDOW_S):
                val = self._read_grid(hass, grid_entity, invert_sign)
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
        hass: HomeAssistant,
        grid_entity: str,
        invert_sign: bool,
        samples: int = 10,
    ) -> float | None:
        """Measure average grid_w over `samples` seconds."""
        readings: list[float] = []
        for _ in range(samples):
            val = self._read_grid(hass, grid_entity, invert_sign)
            if val is not None:
                readings.append(val)
            await asyncio.sleep(1.0)
        if not readings:
            return None
        return sum(readings) / len(readings)

    def _read_grid(
        self,
        hass: HomeAssistant,
        grid_entity: str,
        invert_sign: bool,
    ) -> float | None:
        state = hass.states.get(grid_entity)
        if state is None or state.state in ("unknown", "unavailable"):
            return None
        try:
            val = float(state.state)
            return -val if invert_sign else val
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

    async def _write_setpoint(
        self, hass: HomeAssistant, array: ArrayConfig, value: float
    ) -> None:
        """Write a new setpoint to the inverter entity."""
        from homeassistant.const import ATTR_ENTITY_ID

        from .const import OUTPUT_TYPE_SWITCH

        if array.output_type == OUTPUT_TYPE_SWITCH:
            service = "turn_on" if value > 0 else "turn_off"
            await hass.services.async_call(
                "switch",
                service,
                {ATTR_ENTITY_ID: array.setpoint_entity},
            )
        else:
            await hass.services.async_call(
                "number",
                "set_value",
                {ATTR_ENTITY_ID: array.setpoint_entity, "value": value},
            )
