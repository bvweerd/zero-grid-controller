"""Main coordinator for Zero Grid Controller."""

from __future__ import annotations

import logging
import time
from dataclasses import dataclass, field
from datetime import timedelta

from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant
from homeassistant.helpers.update_coordinator import DataUpdateCoordinator, UpdateFailed

from .actuator_manager import ActuatorManager
from .array import ArrayConfig, array_config_from_subentry
from .battery import BatteryConfig, battery_config_from_subentry
from .calibrator import ArrayCalibrator, CalibrationResult
from .const import (
    AGGRESSIVENESS_FACTORS,
    AGGRESSIVENESS_KI_RATIO,
    ARRAY_SUBENTRY_TYPE,
    BATTERY_PENDING_S,
    BATTERY_SUBENTRY_TYPE,
    BATTERY_WRITE_THRESHOLD_W,
    CALIBRATION_CONFIDENCE_MEASURED,
    CONF_AGGRESSIVENESS,
    CONF_CALIBRATION_CONFIDENCE,
    CONF_CONTROLLER_ENABLED,
    CONF_DEADBAND_W,
    CONF_DERIVED_MAX_POWER_W,
    CONF_EWM_ALPHA,
    CONF_GRID_EXPORT_SENSORS,
    CONF_GRID_IMPORT_SENSORS,
    CONF_KD,
    CONF_KI,
    CONF_KP,
    CONF_OUTPUT_MAX_W,
    CONF_SETTLING_DOWN_S,
    CONF_SETTLING_TIME_S,
    CONF_SETTLING_UP_S,
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
    DOMAIN,
    PV_RECOVERY_STEP_W,
    PV_RECOVERY_TRACKING_TOLERANCE_W,
    STATUS_ACTIVE,
    STATUS_DEADBAND,
    STATUS_DISABLED,
)
from .pid import PIDController
from .repairs import dismiss_grid_sensor_unavailable, raise_grid_sensor_unavailable
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


@dataclass
class BatteryState:
    """Measured state and effective capacities of all batteries this cycle."""

    actuals: dict[str, float] = field(default_factory=dict)
    charge_caps: dict[str, float] = field(default_factory=dict)
    discharge_caps: dict[str, float] = field(default_factory=dict)

    @property
    def total_actual(self) -> float:
        return sum(self.actuals.values())

    @property
    def total_charge_cap(self) -> float:
        return sum(self.charge_caps.values())

    @property
    def total_discharge_cap(self) -> float:
        return sum(self.discharge_caps.values())


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
        self._battery_pending_until: dict[str, float] = {}
        self._settling_until: dict[str, float] = {}
        self._calibrator: ArrayCalibrator | None = None
        self._grid_sensor_unavailable: bool = False

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
        self._enabled: bool = bool(data.get(CONF_CONTROLLER_ENABLED, True))
        self._aggressiveness: str = data.get(
            CONF_AGGRESSIVENESS, DEFAULT_AGGRESSIVENESS
        )

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
        try:
            return await self._run_control_cycle()
        except Exception as err:
            raise UpdateFailed(
                translation_domain=DOMAIN,
                translation_key="coordinator_update_failed",
                translation_placeholders={"error": str(err)},
            ) from err

    async def _run_control_cycle(self) -> ZGCResult:
        """Inner control cycle logic."""
        now = time.monotonic()
        dt = clamp(now - self._last_update, CONTROL_DT_MIN, CONTROL_DT_MAX)
        self._last_update = now

        # 1. Read grid
        grid_raw = await self._read_grid()
        if grid_raw is None:
            if not self._grid_sensor_unavailable:
                self._grid_sensor_unavailable = True
                raise_grid_sensor_unavailable(self.hass)
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
        if self._grid_sensor_unavailable:
            self._grid_sensor_unavailable = False
            dismiss_grid_sensor_unavailable(self.hass)

        # 2. EWM filter
        if self._filtered_w is None:
            self._filtered_w = grid_raw
        else:
            self._filtered_w = (
                self._ewm_alpha * grid_raw + (1 - self._ewm_alpha) * self._filtered_w
            )
        filtered = self._filtered_w

        # Suspend normal control while calibration owns the actuators.
        if self._calibrator is not None:
            self._pid.freeze_integrator()
            return ZGCResult(
                grid_raw_w=grid_raw,
                grid_filtered_w=filtered,
                pid_output_w=0.0,
                status=STATUS_DISABLED,
                setpoints=dict(self._current_setpoints),
                battery_setpoints=dict(self._current_battery_setpoints),
            )

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

        # 4. Battery state (pure reads) and PV recovery bias.  Recovery adds a
        # virtual import so curtailed arrays gradually reopen while batteries
        # still have spare charge capacity, instead of staying curtailed.
        battery_state = self._read_battery_states()
        recovery_w = self._pv_recovery_w(battery_state, filtered, now)

        # 5. Deadband check (recovery deliberately bypasses the deadband —
        # the whole point is to act while the grid is already balanced)
        if abs(filtered) < self._deadband_w and recovery_w == 0.0:
            self._pid.freeze_integrator()
            return ZGCResult(
                grid_raw_w=grid_raw,
                grid_filtered_w=filtered,
                pid_output_w=0.0,
                status=STATUS_DEADBAND,
                setpoints=dict(self._current_setpoints),
                battery_setpoints=dict(self._current_battery_setpoints),
            )

        # 6. Battery layer: incremental command, returns the grid residual
        # with the still-pending battery response fed forward so downstream
        # layers do not double-correct.
        residual = await self._command_batteries(filtered, now, battery_state)
        residual += recovery_w

        # 7a. Numeric arrays: PID on residual
        # Negate: PID error = setpoint - (-residual) = residual
        # → positive output when importing (open PV), negative when exporting (curtail PV)
        pid_output = self._pid.compute(-residual, dt)

        await self._distribute_to_numeric_arrays(pid_output, now)

        # 7b. Switch arrays: hysteresis on residual
        await self._apply_switch_hysteresis(residual, now)

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

    @property
    def enabled(self) -> bool:
        """Return True if the controller is enabled."""
        return self._enabled

    def set_enabled(self, value: bool) -> None:
        """Enable or disable the controller."""
        self._enabled = value

    def reset_pid(self) -> None:
        """Reset the PID controller state."""
        self._pid.reset()

    def _is_enabled(self) -> bool:
        """Return True if the controller should be active."""
        return self._enabled

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

    # ------------------------------------------------------------------
    # Battery layer
    # ------------------------------------------------------------------

    def _read_battery_states(self) -> BatteryState:
        """Read measured battery power and SoC-limited capacities.

        Measured power falls back to the commanded setpoint when the sensor
        is unavailable.  SoC at/above max_soc zeroes the charge capacity;
        SoC at/below min_soc zeroes the discharge capacity.
        """
        state = BatteryState()
        for battery in self.batteries:
            actual = self._read_sensor_safe(battery.sensor_entity)
            if actual is None:
                actual = self._current_battery_setpoints.get(battery.name, 0.0)
            state.actuals[battery.name] = actual

            soc = (
                self._read_sensor_safe(battery.soc_sensor_entity)
                if battery.soc_sensor_entity
                else None
            )
            state.charge_caps[battery.name] = (
                0.0
                if soc is not None and soc >= battery.max_soc
                else battery.max_charge_w
            )
            state.discharge_caps[battery.name] = (
                0.0
                if soc is not None and soc <= battery.min_soc
                else battery.max_discharge_w
            )
        return state

    def _discharge_allowed(self) -> bool:
        """Battery discharge is the last resort: only when numeric PV is at max."""
        numeric_arrays = [a for a in self.arrays if not a.is_switch]
        return all(
            self._current_setpoints.get(a.name, a.setpoint_max) >= a.setpoint_max
            for a in numeric_arrays
        )

    def _pv_recovery_w(
        self, battery_state: BatteryState, filtered: float, now: float
    ) -> float:
        """Virtual import to reopen curtailed PV while batteries can absorb it.

        Without this, arrays stay curtailed once the battery has backed off
        export: the grid sits at zero, so the PID never reopens them and the
        spare charge capacity is wasted.
        """
        if not battery_state.actuals:
            return 0.0
        # Only when the grid is balanced: real import opens arrays by itself,
        # and during export the battery layer is still absorbing.
        if abs(filtered) >= self._deadband_w:
            return 0.0
        # Hold off while a battery command is still pending settlement.
        if any(now < until for until in self._battery_pending_until.values()):
            return 0.0
        # Only recover into batteries that actually track their commands —
        # otherwise a dead battery causes an endless open/curtail limit cycle.
        target_total = sum(
            self._current_battery_setpoints.get(name, 0.0)
            for name in battery_state.actuals
        )
        if (
            abs(target_total - battery_state.total_actual)
            > PV_RECOVERY_TRACKING_TOLERANCE_W
        ):
            return 0.0
        any_curtailed = any(
            not a.is_switch
            and a.name in self._current_setpoints
            and self._current_setpoints[a.name] < a.setpoint_max
            and now >= self._settling_until.get(a.name, 0.0)
            for a in self.arrays
        )
        if not any_curtailed:
            return 0.0
        charging_w = max(0.0, -battery_state.total_actual)
        spare_charge_w = battery_state.total_charge_cap - charging_w
        if spare_charge_w <= 0.0:
            return 0.0
        return min(spare_charge_w, PV_RECOVERY_STEP_W)

    async def _command_batteries(
        self, filtered: float, now: float, battery_state: BatteryState
    ) -> float:
        """Incrementally command batteries to absorb the grid error.

        The desired total battery power is the *measured* total plus the grid
        error (negative = charge), clamped to SoC-limited capacities.
        Discharge is only allowed as a last resort (numeric PV at max).

        Returns the residual grid power for downstream layers: the measured
        grid minus the commanded-but-not-yet-measured battery response.  The
        feedforward expires after BATTERY_PENDING_S so a non-responsive
        battery cannot suppress curtailment indefinitely.
        """
        if not self.batteries:
            return filtered

        total_charge_cap = battery_state.total_charge_cap
        total_discharge_cap = (
            battery_state.total_discharge_cap if self._discharge_allowed() else 0.0
        )
        desired_total = clamp(
            battery_state.total_actual + filtered,
            -total_charge_cap,
            total_discharge_cap,
        )

        # Distribute proportionally to per-battery capacity in the active
        # direction (SoC-limited batteries automatically get a zero share).
        for battery in self.batteries:
            if desired_total < 0 and total_charge_cap > 0:
                target = (
                    desired_total
                    * battery_state.charge_caps[battery.name]
                    / total_charge_cap
                )
            elif desired_total > 0 and total_discharge_cap > 0:
                target = (
                    desired_total
                    * battery_state.discharge_caps[battery.name]
                    / total_discharge_cap
                )
            else:
                target = 0.0

            # Initialise from the actual entity state on first encounter so an
            # already-zero battery is not written to needlessly.
            previous = self._current_battery_setpoints.get(battery.name)
            if previous is None:
                previous = self._read_sensor_safe(battery.setpoint_entity) or 0.0
                self._current_battery_setpoints[battery.name] = previous
            if abs(target - previous) < BATTERY_WRITE_THRESHOLD_W:
                continue
            try:
                await self._actuators.write_numeric_entity(
                    battery.setpoint_entity, target
                )
            except Exception:
                _LOGGER.exception(
                    "Failed to write battery setpoint for %s", battery.name
                )
                continue
            self._current_battery_setpoints[battery.name] = target
            self._battery_pending_until[battery.name] = now + BATTERY_PENDING_S

        pending_w = sum(
            self._current_battery_setpoints.get(battery.name, 0.0)
            - battery_state.actuals[battery.name]
            for battery in self.batteries
            if now < self._battery_pending_until.get(battery.name, 0.0)
        )
        return filtered - pending_w

    async def _distribute_to_numeric_arrays(self, delta_w: float, now: float) -> None:
        """Distribute PID output across numeric arrays by available headroom."""
        active = [
            a
            for a in self.arrays
            if not a.is_switch and now >= self._settling_until.get(a.name, 0.0)
        ]
        if not active or delta_w == 0:
            return

        # Initialise setpoints from actual entity state on first encounter so
        # headroom is computed from the real position, not assumed setpoint_max.
        for a in active:
            if a.name not in self._current_setpoints:
                state = self.hass.states.get(a.setpoint_entity)
                if state is not None and state.state not in ("unavailable", "unknown"):
                    try:
                        self._current_setpoints[a.name] = clamp(
                            float(state.state), a.setpoint_min, a.setpoint_max
                        )
                    except ValueError:
                        self._current_setpoints[a.name] = a.setpoint_max
                else:
                    self._current_setpoints[a.name] = a.setpoint_max

        # delta_w > 0: import → open PV (raise setpoint) → headroom_down
        # delta_w < 0: export → curtail PV (lower setpoint) → headroom_up
        headrooms = {
            a.name: (
                a.headroom_down_w(self._current_setpoints[a.name])
                if delta_w > 0
                else a.headroom_up_w(self._current_setpoints[a.name])
            )
            for a in active
        }
        total_headroom = sum(headrooms.values())
        if total_headroom <= 0:
            return

        for array in active:
            if array.w_per_unit == 0:
                continue
            share_w = delta_w * headrooms[array.name] / total_headroom
            delta_units = round(share_w / array.w_per_unit)
            if delta_units == 0:
                continue

            # Cloud-shadow guard: only block when *opening* the limit.
            # Curtailment is always safe regardless of actual production.
            if delta_units > 0 and not self._can_open(array):
                continue

            current = self._current_setpoints[array.name]
            # Positive delta_units: raise setpoint (open PV); negative: lower (curtail)
            new_sp = clamp(
                current + delta_units, array.setpoint_min, array.setpoint_max
            )
            if new_sp == current:
                continue

            await self._actuators.write_setpoint(array, new_sp)
            self._current_setpoints[array.name] = new_sp
            settle_s = (
                (array.settling_up_s or array.settling_time_s)
                if delta_units > 0
                else (array.settling_down_s or array.settling_time_s)
            )
            self._settling_until[array.name] = now + settle_s

    def _can_open(self, array: ArrayConfig) -> bool:
        """Return True if the array may increase its setpoint.

        Reads the power sensor to prevent opening the limit during cloud shadow:
        only allow raising the setpoint when the inverter is actually producing
        close to its current commanded output.  Fails open when no sensor is
        configured or the sensor is unavailable.
        """
        if array.power_sensor_entity is None:
            return True
        actual = self._read_sensor_safe(array.power_sensor_entity)
        if actual is None:
            return True
        current_sp = self._current_setpoints.get(array.name, array.setpoint_max)
        return actual >= (current_sp - 1) * array.w_per_unit

    async def _apply_switch_hysteresis(self, residual: float, now: float) -> None:
        """Turn switch arrays on/off based on residual power."""
        for array in self.arrays:
            if not array.is_switch:
                continue
            if now < self._settling_until.get(array.name, 0.0):
                continue

            if array.name not in self._current_setpoints:
                # Initialise from the actual entity state so the controller
                # does not wrongly assume the switch is off on first run.
                state = self.hass.states.get(array.setpoint_entity)
                if state is not None and state.state not in ("unavailable", "unknown"):
                    initial = (
                        array.setpoint_max
                        if state.state == "on"
                        else array.setpoint_min
                    )
                else:
                    initial = array.setpoint_min
                self._current_setpoints[array.name] = initial
            current = self._current_setpoints[array.name]
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
        updated_any = False
        for result in results:
            if not result.success:
                continue
            array = next((a for a in self.arrays if a.name == result.array_name), None)
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
                    CONF_SETTLING_DOWN_S: result.settling_down_s,
                    CONF_SETTLING_UP_S: result.settling_up_s,
                    CONF_CALIBRATION_CONFIDENCE: CALIBRATION_CONFIDENCE_MEASURED,
                    CONF_DERIVED_MAX_POWER_W: result.derived_max_power_w,
                }
                self.hass.config_entries.async_update_subentry(
                    self._entry, subentry, data=new_data
                )
                # Update in-memory too
                array.w_per_unit = result.w_per_unit
                array.settling_time_s = result.settling_time_s
                array.calibration_confidence = CALIBRATION_CONFIDENCE_MEASURED
                array.derived_max_power_w = result.derived_max_power_w
                array.settling_down_s = result.settling_down_s
                array.settling_up_s = result.settling_up_s
                updated_any = True
                _LOGGER.info(
                    "Calibration persisted for %s: %.2f W/unit, kp=%.4f",
                    array.name,
                    result.w_per_unit,
                    result.kp,
                )
                break

        if not updated_any:
            return

        pid_gains = self._compute_global_pid_gains()
        if pid_gains is None:
            return

        kp, ki = pid_gains
        options = {
            **self._entry.options,
            CONF_KP: kp,
            CONF_KI: ki,
        }
        self.hass.config_entries.async_update_entry(self._entry, options=options)
        self._pid.set_gains(kp, ki, self._pid.kd)

    def _compute_global_pid_gains(self) -> tuple[float, float] | None:
        """Compute global gains from measured numeric arrays."""
        total_w_per_unit = sum(
            array.w_per_unit
            for array in self.arrays
            if not array.is_switch
            and array.calibration_confidence == CALIBRATION_CONFIDENCE_MEASURED
        )
        if total_w_per_unit <= 0:
            return None

        factor = AGGRESSIVENESS_FACTORS.get(self._aggressiveness, 1.0)
        kp = round(factor, 4)
        ki = round(kp * AGGRESSIVENESS_KI_RATIO, 5)
        return kp, ki

    def _diagnostics_pid_basis(self) -> dict[str, object]:
        """Return the measured numeric arrays used for current PID tuning."""
        included_arrays = [
            array.name
            for array in self.arrays
            if not array.is_switch
            and array.calibration_confidence == CALIBRATION_CONFIDENCE_MEASURED
        ]
        total_w_per_unit = sum(
            array.w_per_unit for array in self.arrays if array.name in included_arrays
        )
        return {
            "total_w_per_unit": round(total_w_per_unit, 3),
            "included_arrays": included_arrays,
        }
