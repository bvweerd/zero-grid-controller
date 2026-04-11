"""Automatic step-response calibration for numeric PV arrays."""

from __future__ import annotations

import asyncio
import logging
import math
from collections.abc import Awaitable, Callable
from dataclasses import dataclass

from homeassistant.core import HomeAssistant

from .array import ArrayConfig
from .const import (
    AGGRESSIVENESS_FACTORS,
    AGGRESSIVENESS_KI_RATIO,
    CALIB_BASELINE_SAMPLES,
    CALIB_INTER_ARRAY_SLEEP_S,
    CALIB_MAX_GRID_W,
    CALIB_MAX_TIME_S,
    CALIB_MIN_W_PER_UNIT,
    CALIB_SETTLING_CONFIRM_COUNT,
    CALIB_SETTLING_MAX_S,
    CALIB_SETTLING_MIN_S,
    CALIB_SETTLING_THRESHOLD_W,
    CALIB_STEP_MAX,
    CALIB_STEP_MIN,
    CALIB_STEP_RATIO,
    CONTROL_INTERVAL_S,
)

_LOGGER = logging.getLogger(__name__)


@dataclass
class CalibrationResult:
    """Result for one PV array calibration."""

    array_name: str
    success: bool
    w_per_unit: float
    settling_time_s: int
    kp: float
    ki: float
    message: str = ""


ReadGridFn = Callable[[], Awaitable[float | None]]
WriteSetpointFn = Callable[[ArrayConfig, float], Awaitable[None]]


class ArrayCalibrator:
    """Calibrates numeric PV arrays via a step-response test."""

    def __init__(
        self,
        hass: HomeAssistant,
        arrays: list[ArrayConfig],
        current_setpoints: dict[str, float],
        aggressiveness: str,
        read_grid: ReadGridFn,
        write_setpoint: WriteSetpointFn,
    ) -> None:
        self._hass = hass
        self._arrays = arrays
        self._current_setpoints = current_setpoints
        self._aggressiveness = aggressiveness
        self._read_grid = read_grid
        self._write_setpoint = write_setpoint
        self._abort = False

    def abort(self) -> None:
        """Signal the calibration to stop after the current array."""
        self._abort = True

    async def run(self) -> list[CalibrationResult]:
        """Calibrate all numeric arrays sequentially."""
        results: list[CalibrationResult] = []
        numeric = [a for a in self._arrays if not a.is_switch]

        for i, array in enumerate(numeric):
            if self._abort:
                break
            if i > 0:
                _LOGGER.debug("Waiting %d s between arrays", CALIB_INTER_ARRAY_SLEEP_S)
                await asyncio.sleep(CALIB_INTER_ARRAY_SLEEP_S)

            result = await self._calibrate_array(array)
            results.append(result)
            _LOGGER.info(
                "Calibration %s for %s: w_per_unit=%.2f, settling=%d s",
                "OK" if result.success else "FAILED",
                array.name,
                result.w_per_unit,
                result.settling_time_s,
            )

        return results

    async def _calibrate_array(self, array: ArrayConfig) -> CalibrationResult:
        """Run step-response test for one array."""
        start_sp = self._current_setpoints.get(array.name, array.setpoint_max)

        step = int(
            math.floor(
                max(
                    CALIB_STEP_MIN,
                    min(
                        CALIB_STEP_MAX,
                        (array.setpoint_max - array.setpoint_min) * CALIB_STEP_RATIO,
                    ),
                )
            )
        )

        # 1. Measure baseline grid
        baseline_samples: list[float] = []
        for _ in range(CALIB_BASELINE_SAMPLES):
            g = await self._read_grid()
            if g is None:
                return self._failed(array, "Grid sensor unavailable during baseline")
            if abs(g) > CALIB_MAX_GRID_W:
                return self._failed(array, f"Grid too far from zero ({g:.0f} W)")
            baseline_samples.append(g)
            await asyncio.sleep(CONTROL_INTERVAL_S)

        baseline = sum(baseline_samples) / len(baseline_samples)
        _LOGGER.debug("%s: baseline grid = %.1f W", array.name, baseline)

        # 2. Apply step (curtail: lower setpoint → less PV → more import)
        step_sp = max(array.setpoint_min, start_sp - step)
        if step_sp == start_sp:
            return self._failed(array, "Setpoint already at minimum, cannot step")

        await self._write_setpoint(array, step_sp)
        self._current_setpoints[array.name] = step_sp
        _LOGGER.debug("%s: step %.1f → %.1f", array.name, start_sp, step_sp)

        # 3. Wait for settling: grid must have moved AND stabilised
        #    "Stable" = last CONFIRM_COUNT readings all within THRESHOLD W of each other
        elapsed = 0.0
        window: list[float] = []
        settling_start: float | None = None

        while elapsed < CALIB_MAX_TIME_S:
            await asyncio.sleep(CONTROL_INTERVAL_S)
            elapsed += CONTROL_INTERVAL_S

            g = await self._read_grid()
            if g is None:
                break

            window.append(g)
            if len(window) > CALIB_SETTLING_CONFIRM_COUNT:
                window.pop(0)

            # Record when grid first moves from baseline
            if (
                abs(g - baseline) > CALIB_SETTLING_THRESHOLD_W
                and settling_start is None
            ):
                settling_start = elapsed

            # Settled = window full AND range within threshold AND moved from baseline
            if (
                len(window) == CALIB_SETTLING_CONFIRM_COUNT
                and (max(window) - min(window)) < CALIB_SETTLING_THRESHOLD_W
                and abs(sum(window) / len(window) - baseline)
                > CALIB_SETTLING_THRESHOLD_W
            ):
                break

        settled_values = window

        # 4. Restore original setpoint
        await self._write_setpoint(array, start_sp)
        self._current_setpoints[array.name] = start_sp

        # Check settled: window full AND range within threshold AND moved from baseline
        settled = (
            (
                len(settled_values) == CALIB_SETTLING_CONFIRM_COUNT
                and (max(settled_values) - min(settled_values))
                < CALIB_SETTLING_THRESHOLD_W
                and abs(sum(settled_values) / len(settled_values) - baseline)
                > CALIB_SETTLING_THRESHOLD_W
            )
            if settled_values
            else False
        )

        if not settled:
            return self._failed(array, "Inverter did not settle within timeout")

        # 5. Compute results
        delta_grid = sum(settled_values) / len(settled_values) - baseline
        w_per_unit = abs(delta_grid) / step

        if w_per_unit < CALIB_MIN_W_PER_UNIT:
            return self._failed(array, f"Response too small: {w_per_unit:.2f} W/unit")

        settling_time_s = int(
            max(
                CALIB_SETTLING_MIN_S,
                min(CALIB_SETTLING_MAX_S, settling_start or elapsed),
            )
        )

        kp, ki = self._compute_gains(w_per_unit)
        return CalibrationResult(
            array_name=array.name,
            success=True,
            w_per_unit=w_per_unit,
            settling_time_s=settling_time_s,
            kp=kp,
            ki=ki,
        )

    def _compute_gains(self, w_per_unit: float) -> tuple[float, float]:
        """Compute Kp and Ki from calibrated gain and aggressiveness."""
        factor = AGGRESSIVENESS_FACTORS.get(self._aggressiveness, 1.0)
        kp = factor / w_per_unit
        ki = kp * AGGRESSIVENESS_KI_RATIO
        return round(kp, 4), round(ki, 5)

    @staticmethod
    def _failed(array: ArrayConfig, reason: str) -> CalibrationResult:
        _LOGGER.warning("Calibration failed for %s: %s", array.name, reason)
        return CalibrationResult(
            array_name=array.name,
            success=False,
            w_per_unit=array.w_per_unit,
            settling_time_s=array.settling_time_s,
            kp=0.0,
            ki=0.0,
            message=reason,
        )
