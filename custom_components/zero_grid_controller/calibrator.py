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
    CALIB_INTER_ARRAY_SLEEP_S,
    CALIB_MAX_GRID_W,
    CALIB_MAX_TIME_S,
    CALIB_MIN_W_PER_UNIT,
    CALIB_SETTLING_CONFIRM_COUNT,
    CALIB_SETTLING_MAX_S,
    CALIB_SETTLING_MIN_S,
    CALIB_SETTLING_THRESHOLD_W,
    CALIBRATION_CONFIDENCE_MEASURED,
    CONTROL_INTERVAL_S,
)
from .sensor_reader import SensorReader

_LOGGER = logging.getLogger(__name__)


class _GridLimitExceeded(Exception):
    """Grid power exceeded the calibration safety limit."""


@dataclass
class CalibrationResult:
    """Result for one PV array calibration."""

    array_name: str
    success: bool
    w_per_unit: float
    settling_time_s: int
    kp: float
    ki: float
    derived_max_power_w: float | None = None
    settling_down_s: int | None = None
    settling_up_s: int | None = None
    message: str = ""


WriteSetpointFn = Callable[[ArrayConfig, float], Awaitable[None]]
OnArrayDoneFn = Callable[[int, int], None]
ReadGridFn = Callable[[], Awaitable[float | None]]


class ArrayCalibrator:
    """Calibrates numeric PV arrays via a step-response test."""

    def __init__(
        self,
        hass: HomeAssistant,
        arrays: list[ArrayConfig],
        current_setpoints: dict[str, float],
        aggressiveness: str,
        write_setpoint: WriteSetpointFn,
        sensor_reader: SensorReader | None = None,
        on_array_done: OnArrayDoneFn | None = None,
        read_grid: ReadGridFn | None = None,
    ) -> None:
        self._hass = hass
        self._arrays = arrays
        self._current_setpoints = current_setpoints
        self._aggressiveness = aggressiveness
        self._write_setpoint = write_setpoint
        self._sensor_reader = sensor_reader
        self._on_array_done = on_array_done
        self._read_grid = read_grid
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

            try:
                result = await self._calibrate_array(array)
            except Exception:
                _LOGGER.exception("Unexpected error calibrating %s", array.name)
                result = self._failed(array, "Unexpected error during calibration")
            results.append(result)
            if self._on_array_done is not None:
                self._on_array_done(len(results), len(numeric))
            _LOGGER.info(
                "Calibration %s for %s: w_per_unit=%.2f, settling=%d s",
                "OK" if result.success else "FAILED",
                array.name,
                result.w_per_unit,
                result.settling_time_s,
            )

        kp, ki = self._compute_global_gains(results)
        for result in results:
            if result.success:
                result.kp = kp
                result.ki = ki

        return results

    async def _calibrate_array(self, array: ArrayConfig) -> CalibrationResult:
        """Run 30% → 20% → 30% midpoint calibration for one array."""
        if not array.power_sensor_entity:
            return self._failed(array, "No array power sensor configured")

        start_sp = self._current_setpoints.get(array.name, array.setpoint_max)
        span = array.setpoint_max - array.setpoint_min
        if span <= 0:
            return self._failed(array, "Invalid setpoint range")

        setpoint_30 = array.setpoint_min + span * 0.30
        setpoint_20 = array.setpoint_min + span * 0.20
        if math.isclose(setpoint_30, setpoint_20):
            return self._failed(array, "Setpoint range too small for midpoint step")

        try:
            await self._write_setpoint(array, setpoint_30)
            self._current_setpoints[array.name] = setpoint_30
            power_30_down, settle_30_down = await self._wait_for_stable_power(array)
            if power_30_down is None or settle_30_down is None:
                return self._failed(array, "Power sensor did not settle at 30%")

            await self._write_setpoint(array, setpoint_20)
            self._current_setpoints[array.name] = setpoint_20
            power_20, settle_20 = await self._wait_for_stable_power(array)
            if power_20 is None or settle_20 is None:
                return self._failed(array, "Power sensor did not settle at 20%")

            await self._write_setpoint(array, setpoint_30)
            self._current_setpoints[array.name] = setpoint_30
            power_30_up, settle_30_up = await self._wait_for_stable_power(array)
            if power_30_up is None or settle_30_up is None:
                return self._failed(
                    array, "Power sensor did not settle after returning to 30%"
                )
        except _GridLimitExceeded:
            return self._failed(
                array,
                f"Aborted: |grid| exceeded {CALIB_MAX_GRID_W:.0f} W during calibration",
            )
        finally:
            await self._write_setpoint(array, start_sp)
            self._current_setpoints[array.name] = start_sp

        power_30 = (power_30_down + power_30_up) / 2
        delta_power = power_30 - power_20
        delta_units = setpoint_30 - setpoint_20
        w_per_unit = delta_power / delta_units

        if w_per_unit < CALIB_MIN_W_PER_UNIT:
            return self._failed(array, f"Response too small: {w_per_unit:.2f} W/unit")

        derived_max_power_w = w_per_unit * span
        if not self._is_plausible_max_power(derived_max_power_w, power_30, power_20):
            return self._failed(
                array,
                f"Derived max power implausible: {derived_max_power_w:.0f} W",
            )

        # Add 50 % safety margin so the controller doesn't re-command before
        # the inverter has fully settled, even on cloudy or cold days when the
        # response is slower than during the calibration run.
        raw_settling = max(settle_30_down, settle_20, settle_30_up)
        settling_time_s = int(
            max(
                CALIB_SETTLING_MIN_S,
                min(CALIB_SETTLING_MAX_S, raw_settling * 1.5),
            )
        )
        return CalibrationResult(
            array_name=array.name,
            success=True,
            w_per_unit=w_per_unit,
            settling_time_s=settling_time_s,
            kp=0.0,
            ki=0.0,
            derived_max_power_w=round(derived_max_power_w, 3),
            settling_down_s=int(settle_20 * 1.5),
            settling_up_s=int(settle_30_up * 1.5),
        )

    async def _wait_for_stable_power(
        self, array: ArrayConfig
    ) -> tuple[float | None, float | None]:
        elapsed = 0.0
        window: list[float] = []
        while elapsed < CALIB_MAX_TIME_S:
            await asyncio.sleep(CONTROL_INTERVAL_S)
            elapsed += CONTROL_INTERVAL_S

            if self._read_grid is not None:
                grid = await self._read_grid()
                if grid is not None and abs(grid) > CALIB_MAX_GRID_W:
                    raise _GridLimitExceeded

            power = self._read_power_sensor(array.power_sensor_entity)
            if power is None:
                return None, None

            window.append(power)
            if len(window) > CALIB_SETTLING_CONFIRM_COUNT:
                window.pop(0)

            if (
                len(window) == CALIB_SETTLING_CONFIRM_COUNT
                and (max(window) - min(window)) < CALIB_SETTLING_THRESHOLD_W
            ):
                return sum(window) / len(window), elapsed

        return None, None

    def _read_power_sensor(self, entity_id: str | None) -> float | None:
        if not entity_id:
            return None
        if self._sensor_reader is not None:
            return self._sensor_reader.read_sensor_safe(entity_id)
        state = self._hass.states.get(entity_id)
        if state is None or state.state in ("unavailable", "unknown"):
            return None
        try:
            return float(state.state)
        except ValueError:
            return None

    def _compute_global_gains(
        self, results: list[CalibrationResult]
    ) -> tuple[float, float]:
        """Compute global gains from successful measured numeric arrays."""
        overrides = {
            result.array_name: result.w_per_unit for result in results if result.success
        }
        total_w_per_unit = sum(
            overrides.get(array.name, array.w_per_unit)
            for array in self._arrays
            if not array.is_switch
            and (
                array.name in overrides
                or array.calibration_confidence == CALIBRATION_CONFIDENCE_MEASURED
            )
        )
        if total_w_per_unit <= 0:
            return 0.0, 0.0

        factor = AGGRESSIVENESS_FACTORS.get(self._aggressiveness, 1.0)
        kp = factor
        ki = kp * AGGRESSIVENESS_KI_RATIO
        return round(kp, 4), round(ki, 5)

    @staticmethod
    def _is_plausible_max_power(
        derived_max_power_w: float, power_30: float, power_20: float
    ) -> bool:
        max_from_30 = power_30 / 0.30 if power_30 > 0 else 0.0
        max_from_20 = power_20 / 0.20 if power_20 > 0 else 0.0
        # ±15 % tolerance: tight enough to catch real calibration problems
        # (measurement noise alone is typically < 2 %), yet loose enough to
        # accept minor inverter nonlinearities and firmware hysteresis.
        lower_bound = 0.85 * min(max_from_20, max_from_30)
        upper_bound = 1.15 * max(max_from_20, max_from_30)
        return lower_bound <= derived_max_power_w <= upper_bound

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
            derived_max_power_w=array.derived_max_power_w,
            settling_down_s=array.settling_down_s,
            settling_up_s=array.settling_up_s,
            message=reason,
        )
