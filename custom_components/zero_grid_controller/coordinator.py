"""Main coordinator for Zero Grid Controller."""

from __future__ import annotations

import logging
import math
import time
from dataclasses import dataclass, field
from datetime import timedelta

from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant
from homeassistant.helpers.update_coordinator import DataUpdateCoordinator

from .actuator_manager import ActuatorManager
from .array import ArrayConfig, array_config_from_subentry
from .battery import BatteryConfig, battery_config_from_subentry
from .calibrator import ArrayCalibrator, CalibrationResult
from .const import (
    ARRAY_SUBENTRY_TYPE,
    BATTERY_SUBENTRY_TYPE,
    CONF_AGGRESSIVENESS,
    CONF_CALIBRATION_CONFIDENCE,
    CONF_DEADBAND_W,
    CONF_ENABLE_ENTITY,
    CONF_EWM_ALPHA,
    CONF_GRID_EXPORT_SENSORS,
    CONF_GRID_IMPORT_SENSORS,
    CONF_KD,
    CONF_KI,
    CONF_KP,
    CONF_OUTPUT_MAX_W,
    CONF_SETTLING_TIME_S,
    CONF_W_PER_UNIT,
    CONTROL_DT_MAX,
    CONTROL_DT_MIN,
    CONTROL_INTERVAL_S,
    DEFAULT_AGGRESSIVENESS,
    DEFAULT_DEADBAND_W,
    DEFAULT_EWM_ALPHA,
    DEFAULT_KD,
    DEFAULT_KI,
    DEFAULT_KP,
    DEFAULT_OUTPUT_MAX_W,
    STATUS_ACTIVE,
    STATUS_DEADBAND,
    STATUS_DISABLED,
)
from .pid import PIDController
from .utils import clamp

_LOGGER = logging.getLogger(__name__)

PARALLEL_UPDATES = 0


@dataclass
class ZGCResult:
    """Data returned by the coordinator each update cycle."""

    grid_raw_w: float
    grid_filtered_w: float
    pid_output_w: float
    status: str
    setpoints: dict[str, float] = field(default_factory=dict)
    battery_setpoints: dict[str, float] = field(default_factory=dict)


class ZeroGridCoordinator(DataUpdateCoordinator[ZGCResult]):
    """Central coordinator: reads grid, runs PID, distributes setpoints."""

    def __init__(
        self,
        hass: HomeAssistant,
        entry: ConfigEntry,
        update_interval_s: int = CONTROL_INTERVAL_S,
    ) -> None:
        super().__init__(
            hass,
            _LOGGER,
            name=f"zero_grid_controller_{entry.entry_id}",
            update_interval=timedelta(seconds=update_interval_s),
        )
        self._entry = entry
        self._last_update = time.monotonic()
        self._actuators = ActuatorManager(hass)
        self._filtered_w: float | None = None
        self._current_setpoints: dict[str, float] = {}
        self._current_battery_setpoints: dict[str, float] = {}
        self._settling_until: dict[str, float] = {}
        self._calibrator: ArrayCalibrator | None = None

        self._init_from_entry(entry)

    # ------------------------------------------------------------------
    # Initialisation
    # ------------------------------------------------------------------

    def _init_from_entry(self, entry: ConfigEntry) -> None:
        """Initialise / re-initialise all state from the config entry."""
        data = {**entry.data, **entry.options}

        kp = float(data.get(CONF_KP, DEFAULT_KP))
        ki = float(data.get(CONF_KI, DEFAULT_KI))
        kd = float(data.get(CONF_KD, DEFAULT_KD))
        output_max = float(data.get(CONF_OUTPUT_MAX_W, DEFAULT_OUTPUT_MAX_W))

        self._pid = PIDController(
            kp,
            ki,
            kd,
            setpoint=0.0,
            output_min=-output_max,
            output_max=output_max,
        )
        self._ewm_alpha = float(data.get(CONF_EWM_ALPHA, DEFAULT_EWM_ALPHA))
        self._deadband_w = float(data.get(CONF_DEADBAND_W, DEFAULT_DEADBAND_W))
        self._enable_entity: str | None = data.get(CONF_ENABLE_ENTITY)
        self._aggressiveness: str = data.get(CONF_AGGRESSIVENESS, DEFAULT_AGGRESSIVENESS)

        self._import_sensors: list[str] = data.get(CONF_GRID_IMPORT_SENSORS, [])
        self._export_sensors: list[str] = data.get(CONF_GRID_EXPORT_SENSORS, [])

        self.arrays: list[ArrayConfig] = []
        self.batteries: list[BatteryConfig] = []

        for subentry in entry.subentries.values():
            if subentry.subentry_type == ARRAY_SUBENTRY_TYPE:
                self.arrays.append(
                    array_config_from_subentry(subentry.subentry_id, subentry.data)
                )
            elif subentry.subentry_type == BATTERY_SUBENTRY_TYPE:
                self.batteries.append(
                    battery_config_from_subentry(subentry.subentry_id, subentry.data)
                )

    # ------------------------------------------------------------------
    # Public helpers
    # ------------------------------------------------------------------

    def reload_config(self) -> None:
        """Reload config entry data, preserving filter and PID integral state."""
        old_filtered = self._filtered_w
        old_integral = self._pid.integral

        self._init_from_entry(self._entry)

        # Restore continuity
        self._filtered_w = old_filtered
        self._pid.set_integral(old_integral)

    async def start_calibration(self) -> list[CalibrationResult]:
        """Run calibration on all numeric arrays and persist results."""
        if self._calibrator is not None:
            _LOGGER.warning("Calibration already running")
            return []

        self._calibrator = ArrayCalibrator(
            hass=self.hass,
            arrays=self.arrays,
            current_setpoints=self._current_setpoints,
            aggressiveness=self._aggressiveness,
            read_grid=self._read_grid,
            write_setpoint=self._actuators.write_setpoint,
        )
        try:
            results = await self._calibrator.run()
        finally:
            self._calibrator = None

        await self._persist_calibration_results(results)
        return results

    def abort_calibration(self) -> None:
        """Abort a running calibration."""
        if self._calibrator:
            self._calibrator.abort()

    # ------------------------------------------------------------------
    # Update loop
    # ------------------------------------------------------------------

    async def _async_update_data(self) -> ZGCResult:
        """Main control cycle, called every CONTROL_INTERVAL_S seconds."""
        now = time.monotonic()
        dt = clamp(now - self._last_update, CONTROL_DT_MIN, CONTROL_DT_MAX)
        self._last_update = now

        # 1. Read grid
        grid_raw = await self._read_grid()
        if grid_raw is None:
            _LOGGER.warning("Grid sensor(s) unavailable, entering safe state")
            self._pid.reset()
            await self._actuators.enter_safe_state(
                self.arrays, self.batteries, self._current_setpoints
            )
            return ZGCResult(
                grid_raw_w=0.0,
                grid_filtered_w=0.0,
                pid_output_w=0.0,
                status=STATUS_DISABLED,
            )

        # 2. EWM filter
        if self._filtered_w is None:
            self._filtered_w = grid_raw
        else:
            self._filtered_w = (
                self._ewm_alpha * grid_raw + (1 - self._ewm_alpha) * self._filtered_w
            )
        filtered = self._filtered_w

        # 3. Enable entity check
        if not self._is_enabled():
            self._pid.reset()
            await self._actuators.enter_safe_state(
                self.arrays, self.batteries, self._current_setpoints
            )
            return ZGCResult(
                grid_raw_w=grid_raw,
                grid_filtered_w=filtered,
                pid_output_w=0.0,
                status=STATUS_DISABLED,
                setpoints=dict(self._current_setpoints),
            )

        # 4. Deadband check
        if abs(filtered) < self._deadband_w:
            self._pid.freeze_integrator()
            return ZGCResult(
                grid_raw_w=grid_raw,
                grid_filtered_w=filtered,
                pid_output_w=0.0,
                status=STATUS_DEADBAND,
                setpoints=dict(self._current_setpoints),
                battery_setpoints=dict(self._current_battery_setpoints),
            )

        # 5. Battery charge layer (export: grid < 0 → charge batteries)
        if filtered < 0 and self.batteries:
            charge_w = min(
                sum(b.max_charge_w for b in self.batteries),
                abs(filtered),
            )
            per_battery = charge_w / len(self.batteries)
            for battery in self.batteries:
                target = -min(battery.max_charge_w, per_battery)
                await self._actuators.write_numeric_entity(
                    battery.setpoint_entity, target
                )
                self._current_battery_setpoints[battery.name] = target

        elif filtered > 0 and self.batteries:
            # Reset charge targets when importing
            for battery in self.batteries:
                if self._current_battery_setpoints.get(battery.name, 0.0) < 0:
                    await self._actuators.write_numeric_entity(
                        battery.setpoint_entity, 0.0
                    )
                    self._current_battery_setpoints[battery.name] = 0.0

        # 6. Residual after battery pre-compensation
        battery_cmd_w = sum(self._current_battery_setpoints.values())
        residual = filtered - battery_cmd_w

        # 7a. Numeric arrays: PID on residual
        any_settling = any(
            now < self._settling_until.get(a.name, 0.0)
            for a in self.arrays
            if not a.is_switch
        )
        if any_settling:
            self._pid.freeze_integrator()

        # Negate: PID error = setpoint - (-residual) = residual
        # → positive output when importing (curtail PV), negative when exporting (open)
        pid_output = self._pid.compute(-residual, dt)

        await self._distribute_to_numeric_arrays(pid_output, now)

        # 7b. Switch arrays: hysteresis on residual
        await self._apply_switch_hysteresis(residual, now)

        # 8. Battery discharge layer (all numeric PV at max AND still importing)
        if filtered > 0 and self.batteries:
            numeric = [a for a in self.arrays if not a.is_switch]
            all_maxed = all(
                self._current_setpoints.get(a.name, a.setpoint_max) >= a.setpoint_max
                for a in numeric
            )
            if all_maxed or not numeric:
                discharge_w = min(
                    sum(b.max_discharge_w for b in self.batteries),
                    abs(residual),
                )
                per_battery = discharge_w / len(self.batteries)
                for battery in self.batteries:
                    target = min(battery.max_discharge_w, per_battery)
                    await self._actuators.write_numeric_entity(
                        battery.setpoint_entity, target
                    )
                    self._current_battery_setpoints[battery.name] = target

        return ZGCResult(
            grid_raw_w=grid_raw,
            grid_filtered_w=filtered,
            pid_output_w=pid_output,
            status=STATUS_ACTIVE,
            setpoints=dict(self._current_setpoints),
            battery_setpoints=dict(self._current_battery_setpoints),
        )

    # ------------------------------------------------------------------
    # Internal helpers
    # ------------------------------------------------------------------

    def _is_enabled(self) -> bool:
        """Return True if the controller should be active."""
        if not self._enable_entity:
            return True
        state = self.hass.states.get(self._enable_entity)
        if state is None or state.state in ("unavailable", "unknown"):
            return False
        return state.state in ("on", "true", "1", "yes")

    async def _read_grid(self) -> float | None:
        """Read and sum grid import/export sensors. Returns None on failure."""
        total = 0.0
        for entity_id in self._import_sensors:
            val = self._read_sensor_safe(entity_id)
            if val is None:
                return None
            total += val
        for entity_id in self._export_sensors:
            val = self._read_sensor_safe(entity_id)
            if val is None:
                return None
            total -= val
        return total

    def _read_sensor_safe(self, entity_id: str) -> float | None:
        """Read a sensor state as float, returning None on unavailable/non-numeric."""
        state = self.hass.states.get(entity_id)
        if state is None or state.state in ("unavailable", "unknown"):
            return None
        try:
            return float(state.state)
        except ValueError:
            return None

    async def _distribute_to_numeric_arrays(self, delta_w: float, now: float) -> None:
        """Distribute PID output across numeric arrays by available headroom."""
        active = [
            a
            for a in self.arrays
            if not a.is_switch and now >= self._settling_until.get(a.name, 0.0)
        ]
        if not active or delta_w == 0:
            return

        # delta_w > 0: import → open PV (raise setpoint) → headroom_down
        # delta_w < 0: export → curtail PV (lower setpoint) → headroom_up
        headrooms = {
            a.name: (
                a.headroom_down_w(self._current_setpoints.get(a.name, a.setpoint_max))
                if delta_w > 0
                else a.headroom_up_w(
                    self._current_setpoints.get(a.name, a.setpoint_max)
                )
            )
            for a in active
        }
        total_headroom = sum(headrooms.values())
        if total_headroom <= 0:
            return

        for array in active:
            share_w = delta_w * headrooms[array.name] / total_headroom
            delta_units = share_w / array.w_per_unit
            delta_units = (
                math.floor(delta_units) if delta_w > 0 else math.ceil(delta_units)
            )
            if delta_units == 0:
                continue

            current = self._current_setpoints.get(array.name, array.setpoint_max)
            # Positive delta_units: raise setpoint (open PV); negative: lower (curtail)
            new_sp = clamp(current + delta_units, array.setpoint_min, array.setpoint_max)
            if new_sp == current:
                continue

            await self._actuators.write_setpoint(array, new_sp)
            self._current_setpoints[array.name] = new_sp
            self._settling_until[array.name] = now + array.settling_time_s

    async def _apply_switch_hysteresis(self, residual: float, now: float) -> None:
        """Turn switch arrays on/off based on residual power."""
        for array in self.arrays:
            if not array.is_switch:
                continue
            if now < self._settling_until.get(array.name, 0.0):
                continue

            current = self._current_setpoints.get(array.name, array.setpoint_min)
            is_on = current > array.setpoint_min

            if not is_on and residual >= array.switch_on_threshold_w:
                # Importing and above threshold → turn on to generate more
                new_sp = array.setpoint_max
            elif is_on and residual <= -array.switch_off_threshold_w:
                # Exporting and below threshold → turn off to stop generating
                new_sp = array.setpoint_min
            else:
                continue

            await self._actuators.write_setpoint(array, new_sp)
            self._current_setpoints[array.name] = new_sp
            self._settling_until[array.name] = now + array.switch_debounce_s

    async def _persist_calibration_results(
        self, results: list[CalibrationResult]
    ) -> None:
        """Persist w_per_unit and settling_time_s to each array's subentry."""
        for result in results:
            if not result.success:
                continue
            array = next(
                (a for a in self.arrays if a.name == result.array_name), None
            )
            if array is None:
                continue
            # Find the matching subentry and update its data
            for subentry in self._entry.subentries.values():
                if subentry.subentry_type != ARRAY_SUBENTRY_TYPE:
                    continue
                subentry_data = dict(subentry.data)
                from .const import CONF_ARRAY_NAME
                if subentry_data.get(CONF_ARRAY_NAME) != array.name:
                    continue
                new_data = {
                    **subentry_data,
                    CONF_W_PER_UNIT: result.w_per_unit,
                    CONF_SETTLING_TIME_S: result.settling_time_s,
                    CONF_CALIBRATION_CONFIDENCE: "measured",
                }
                self.hass.config_entries.async_update_subentry(
                    self._entry, subentry, data=new_data
                )
                # Update PID gains in main entry options
                options = {
                    **self._entry.options,
                    CONF_KP: result.kp,
                    CONF_KI: result.ki,
                }
                self.hass.config_entries.async_update_entry(
                    self._entry, options=options
                )
                # Update in-memory too
                array.w_per_unit = result.w_per_unit
                array.settling_time_s = result.settling_time_s
                self._pid.set_gains(result.kp, result.ki, self._pid.kd)
                _LOGGER.info(
                    "Calibration persisted for %s: %.2f W/unit, kp=%.4f",
                    array.name,
                    result.w_per_unit,
                    result.kp,
                )
                break
