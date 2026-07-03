"""Pure control logic engine — no HA lifecycle dependencies."""

from __future__ import annotations

import logging
import time
from dataclasses import dataclass, field

from homeassistant.core import HomeAssistant

from .actuator_manager import ActuatorManager
from .array import ArrayConfig
from .battery import BatteryConfig
from .const import (
    BATTERY_PENDING_S,
    BATTERY_WRITE_THRESHOLD_W,
    CONTROL_DT_MAX,
    CONTROL_DT_MIN,
    DEFAULT_FAILSAFE_MODE,
    PV_RECOVERY_STEP_W,
    PV_RECOVERY_TRACKING_TOLERANCE_W,
    STATUS_ACTIVE,
    STATUS_DEADBAND,
    STATUS_DISABLED,
    ControllerMode,
    ControllerStatus,
)
from .load import LoadConfig
from .pid import PIDController
from .utils import clamp, power_unit_factor

_LOGGER = logging.getLogger(__name__)


@dataclass
class ControlCycleResult:
    """Data returned by one control cycle."""

    grid_raw_w: float
    grid_filtered_w: float
    pid_output_w: float
    status: ControllerStatus
    setpoints: dict[str, float] = field(default_factory=dict)
    battery_setpoints: dict[str, float] = field(default_factory=dict)
    load_setpoints: dict[str, float] = field(default_factory=dict)


@dataclass
class BatteryState:
    """Measured state and effective capacities of reactive batteries this cycle."""

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


class ControlEngine:
    """Stateful control engine: owns PID, filter and all setpoint state.

    Holds no HA lifecycle state (no ConfigEntry, no DataUpdateCoordinator).
    All HA I/O is via the injected *hass* (for state reads) and *actuators*
    (for writes).  This keeps the coordinator thin — pure HA integration
    glue — and makes the control logic independently unit-testable.
    """

    def __init__(
        self,
        hass: HomeAssistant,
        pid: PIDController,
        actuators: ActuatorManager,
        ewm_alpha: float,
        deadband_w: float,
        mode: ControllerMode = ControllerMode.ZERO_GRID,
        failsafe_mode: str = DEFAULT_FAILSAFE_MODE,
    ) -> None:
        self._hass = hass
        self._pid = pid
        self._actuators = actuators
        self._ewm_alpha = ewm_alpha
        self._deadband_w = deadband_w
        self._mode = mode
        self._failsafe_mode = failsafe_mode

        self._filtered_w: float | None = None
        self._filter_sample_count: int = 0
        self._last_update: float = time.monotonic()

        self._current_setpoints: dict[str, float] = {}
        self._current_battery_setpoints: dict[str, float] = {}
        self._current_load_setpoints: dict[str, float] = {}
        self._settling_until: dict[str, float] = {}
        self._load_settling_until: dict[str, float] = {}
        self._battery_pending_until: dict[str, float] = {}
        self._schedule_unavailable: set[str] = set()

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    @property
    def pid(self) -> PIDController:
        """The PID controller instance."""
        return self._pid

    @property
    def filtered_w(self) -> float | None:
        """Current EWM-filtered grid power."""
        return self._filtered_w

    @property
    def current_setpoints(self) -> dict[str, float]:
        """Current array setpoints (by name)."""
        return self._current_setpoints

    @property
    def current_battery_setpoints(self) -> dict[str, float]:
        """Current battery setpoints (by name)."""
        return self._current_battery_setpoints

    @property
    def current_load_setpoints(self) -> dict[str, float]:
        """Current load setpoints (by name)."""
        return self._current_load_setpoints

    def update_params(
        self,
        pid: PIDController,
        ewm_alpha: float,
        deadband_w: float,
        mode: ControllerMode = ControllerMode.ZERO_GRID,
        failsafe_mode: str = DEFAULT_FAILSAFE_MODE,
    ) -> None:
        """Update PID and filter parameters, preserving all state dicts."""
        self._pid = pid
        self._ewm_alpha = ewm_alpha
        self._deadband_w = deadband_w
        self._mode = mode
        self._failsafe_mode = failsafe_mode

    def restore_filter_state(
        self,
        filtered_w: float | None,
        integral: float,
    ) -> None:
        """Restore EWM and integrator state after a config reload."""
        self._filtered_w = filtered_w
        self._pid.set_integral(integral)

    def prune_stale_keys(
        self,
        active_array_names: set[str],
        active_battery_names: set[str],
        active_load_names: set[str],
    ) -> None:
        """Remove setpoint/settling keys for subentries that no longer exist."""
        self._current_setpoints = {
            k: v for k, v in self._current_setpoints.items() if k in active_array_names
        }
        self._settling_until = {
            k: v for k, v in self._settling_until.items() if k in active_array_names
        }
        self._current_load_setpoints = {
            k: v
            for k, v in self._current_load_setpoints.items()
            if k in active_load_names
        }
        self._load_settling_until = {
            k: v for k, v in self._load_settling_until.items() if k in active_load_names
        }
        self._current_battery_setpoints = {
            k: v
            for k, v in self._current_battery_setpoints.items()
            if k in active_battery_names
        }
        self._battery_pending_until = {
            k: v
            for k, v in self._battery_pending_until.items()
            if k in active_battery_names
        }

    async def run_cycle(
        self,
        grid_raw: float | None,
        arrays: list[ArrayConfig],
        batteries: list[BatteryConfig],
        loads: list[LoadConfig],
        enabled: bool,
        calibrating: bool,
    ) -> ControlCycleResult:
        """Execute one full control cycle.

        *grid_raw* is None when the grid sensors are unavailable.
        """
        now = time.monotonic()
        dt = clamp(now - self._last_update, CONTROL_DT_MIN, CONTROL_DT_MAX)
        self._last_update = now

        # 1. Grid unavailability → safe state
        if grid_raw is None:
            self._pid.reset()
            # Restart the EWM warm-up on recovery so stale filter state does
            # not bias the first cycles after an outage.
            self._filtered_w = None
            self._filter_sample_count = 0
            await self._actuators.enter_safe_state(
                arrays, batteries, self._current_setpoints, self._failsafe_mode
            )
            await self._enter_load_safe_state(loads)
            return ControlCycleResult(
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

        # Freeze integrator during EWM warm-up to avoid acting on biased
        # filter output.  Warmup length ≈ 3/α samples (95% settling time).
        self._filter_sample_count += 1
        warmup_samples = max(1, round(3.0 / max(self._ewm_alpha, 0.05)))
        if self._filter_sample_count <= warmup_samples:
            self._pid.freeze_integrator()

        # 3. Calibration guard — suspend normal control while calibrator owns actuators
        if calibrating:
            self._pid.freeze_integrator()
            return ControlCycleResult(
                grid_raw_w=grid_raw,
                grid_filtered_w=filtered,
                pid_output_w=0.0,
                status=STATUS_DISABLED,
                setpoints=dict(self._current_setpoints),
                battery_setpoints=dict(self._current_battery_setpoints),
                load_setpoints=dict(self._current_load_setpoints),
            )

        # 4. Enable check
        if not enabled:
            self._pid.reset()
            await self._actuators.enter_safe_state(
                arrays, batteries, self._current_setpoints, self._failsafe_mode
            )
            await self._enter_load_safe_state(loads)
            return ControlCycleResult(
                grid_raw_w=grid_raw,
                grid_filtered_w=filtered,
                pid_output_w=0.0,
                status=STATUS_DISABLED,
                setpoints=dict(self._current_setpoints),
                load_setpoints=dict(self._current_load_setpoints),
            )

        # 4b. Maximize modes — static actuator positions, no PID
        if self._mode == ControllerMode.MAXIMIZE_EXPORT:
            self._pid.reset()
            await self._set_maximize_export(arrays, loads, batteries)
            return ControlCycleResult(
                grid_raw_w=grid_raw,
                grid_filtered_w=filtered,
                pid_output_w=0.0,
                status=ControllerStatus.MAXIMIZING_EXPORT,
                setpoints=dict(self._current_setpoints),
                load_setpoints=dict(self._current_load_setpoints),
                battery_setpoints=dict(self._current_battery_setpoints),
            )
        if self._mode == ControllerMode.MAXIMIZE_IMPORT:
            self._pid.reset()
            await self._set_maximize_import(arrays, loads, batteries)
            return ControlCycleResult(
                grid_raw_w=grid_raw,
                grid_filtered_w=filtered,
                pid_output_w=0.0,
                status=ControllerStatus.MAXIMIZING_IMPORT,
                setpoints=dict(self._current_setpoints),
                load_setpoints=dict(self._current_load_setpoints),
                battery_setpoints=dict(self._current_battery_setpoints),
            )

        # 5. Reactive battery state (pure reads) and PV recovery bias.
        # Recovery adds a virtual import so curtailed arrays gradually reopen
        # while reactive batteries still have spare charge capacity.
        # A scheduled battery whose optimizer sensor is unavailable falls back
        # to the reactive layer (SoC-aware, incremental) instead of silently
        # degrading to correction-only control.
        scheduled_batteries: list[BatteryConfig] = []
        reactive_batteries: list[BatteryConfig] = []
        schedule_values: dict[str, float] = {}
        for battery in batteries:
            if not battery.schedule_sensor_entity:
                reactive_batteries.append(battery)
                continue
            schedule_w = self.read_power_w(battery.schedule_sensor_entity)
            if schedule_w is None:
                if battery.name not in self._schedule_unavailable:
                    self._schedule_unavailable.add(battery.name)
                    _LOGGER.warning(
                        "Schedule sensor %s for battery %s is unavailable — "
                        "falling back to reactive control",
                        battery.schedule_sensor_entity,
                        battery.name,
                    )
                reactive_batteries.append(battery)
                continue
            if battery.name in self._schedule_unavailable:
                self._schedule_unavailable.discard(battery.name)
                _LOGGER.info("Schedule sensor for battery %s recovered", battery.name)
            schedule_values[battery.name] = schedule_w
            scheduled_batteries.append(battery)
        battery_state = self._read_battery_states(reactive_batteries)
        recovery_w = self._pv_recovery_w(arrays, battery_state, filtered, now)

        # 5a. Deadband check (recovery deliberately bypasses the deadband —
        # the whole point is to act while the grid is already balanced)
        if abs(filtered) < self._deadband_w and recovery_w == 0.0:
            self._pid.freeze_integrator()
            return ControlCycleResult(
                grid_raw_w=grid_raw,
                grid_filtered_w=filtered,
                pid_output_w=0.0,
                status=STATUS_DEADBAND,
                setpoints=dict(self._current_setpoints),
                battery_setpoints=dict(self._current_battery_setpoints),
                load_setpoints=dict(self._current_load_setpoints),
            )

        # 5b. Mode idle check — only activate PID on the "forbidden" side
        skip_pid = False
        cycle_status: ControllerStatus = STATUS_ACTIVE
        if self._mode == ControllerMode.ZERO_IMPORT and filtered < -self._deadband_w:
            # Exporting: allowed direction — idle, battery charge layer still runs below
            self._pid.freeze_integrator()
            skip_pid = True
            cycle_status = ControllerStatus.IDLE_EXPORT_OK
        elif self._mode == ControllerMode.ZERO_EXPORT and filtered > self._deadband_w:
            # Importing: allowed direction — idle, battery discharge layer still runs below
            self._pid.freeze_integrator()
            skip_pid = True
            cycle_status = ControllerStatus.IDLE_IMPORT_OK

        # 6. Scheduled batteries follow an external optimizer
        # (e.g. battery_controller) with a real-time grid correction on top.
        scheduled_pending_w = 0.0
        if scheduled_batteries:
            total_scheduled_cap = sum(b.max_charge_w for b in scheduled_batteries)
            for battery in scheduled_batteries:
                schedule_w = schedule_values[battery.name]
                # Proportional reactive correction: adjusts schedule up/down based on
                # the current grid error.  filtered > 0 → importing → push toward
                # discharge (positive correction); filtered < 0 → exporting → push
                # toward charge (negative correction).
                correction = (
                    filtered * (battery.max_charge_w / total_scheduled_cap)
                    if total_scheduled_cap > 0
                    else 0.0
                )
                # SoC limits also bound the corrected target: no charging at
                # or above max_soc, no discharging at or below min_soc.
                soc = (
                    self.read_sensor_safe(battery.soc_sensor_entity)
                    if battery.soc_sensor_entity
                    else None
                )
                lower = (
                    0.0
                    if soc is not None and soc >= battery.max_soc
                    else -battery.max_charge_w
                )
                upper = (
                    0.0
                    if soc is not None and soc <= battery.min_soc
                    else battery.max_discharge_w
                )
                target = clamp(schedule_w + correction, lower, upper)

                actual = self.read_power_w(battery.sensor_entity)
                if actual is None:
                    actual = self._current_battery_setpoints.get(battery.name, 0.0)

                previous = self._current_battery_setpoints.get(battery.name)
                if (
                    previous is None
                    or abs(target - previous) >= BATTERY_WRITE_THRESHOLD_W
                ):
                    try:
                        await self._actuators.write_numeric_entity(
                            battery.setpoint_entity, target
                        )
                    except Exception:
                        _LOGGER.exception(
                            "Failed to write schedule setpoint for %s", battery.name
                        )
                        continue
                    self._current_battery_setpoints[battery.name] = target
                    self._battery_pending_until[battery.name] = now + BATTERY_PENDING_S

                # Feed the commanded-but-not-yet-measured battery response
                # forward so the PID does not correct the same grid error a
                # second time (which oscillates: combined loop gain ≈ 2).
                if now < self._battery_pending_until.get(battery.name, 0.0):
                    scheduled_pending_w += (
                        self._current_battery_setpoints.get(battery.name, 0.0) - actual
                    )

        # 6b. Reactive battery layer: incremental command, returns the grid
        # residual with the still-pending battery response fed forward so
        # downstream layers do not double-correct.  Runs in one-sided idle
        # modes too (charge on export in zero_import, discharge on import in
        # zero_export).
        residual = await self._command_batteries(
            filtered, now, reactive_batteries, battery_state, arrays, loads
        )
        residual -= scheduled_pending_w
        residual += recovery_w

        # 7. PID + actuator distribution (skipped in one-sided idle modes)
        pid_output = 0.0
        if not skip_pid:
            # 7a. PID on residual
            # Negate: positive output when importing (open PV / reduce load)
            pid_output = self._pid.compute(-residual, dt)

            # Back-calculation anti-windup (sign contradiction):
            # When the integral has wound up so far that it opposes the actual
            # grid direction, immediately reset it and use the P-only output for
            # this cycle.  Threshold of 3×deadband avoids triggering on normal
            # near-zero oscillation.
            if abs(pid_output) > 3 * self._deadband_w:
                wrong_direction = (pid_output < 0 and filtered > self._deadband_w) or (
                    pid_output > 0 and filtered < -self._deadband_w
                )
                if wrong_direction:
                    p_only = self._pid.kp * residual
                    _LOGGER.info(
                        "Integrator wind-up corrected: output %.0f W → %.0f W"
                        " (grid %.1f W)",
                        pid_output,
                        p_only,
                        filtered,
                    )
                    self._pid.set_integral(0.0)
                    pid_output = p_only

            # 7b. Numeric correction
            _load_sp_before = {
                ld.name: self._current_load_setpoints.get(ld.name, ld.setpoint_min)
                for ld in loads
                if not ld.is_switch
            }
            _load_w_per_unit = {
                ld.name: ld.w_per_unit for ld in loads if not ld.is_switch
            }

            if pid_output < 0:
                # Exporting surplus → increase loads, then curtail arrays
                after_loads = await self._distribute_to_numeric_loads(
                    pid_output, now, loads
                )
                final_remaining = await self._distribute_to_numeric_arrays(
                    after_loads, now, arrays
                )
                array_absorbed_w = after_loads - final_remaining
            else:
                # Importing deficit → open arrays, then reduce loads
                after_arrays = await self._distribute_to_numeric_arrays(
                    pid_output, now, arrays
                )
                array_absorbed_w = pid_output - after_arrays
                final_remaining = await self._distribute_to_numeric_loads(
                    after_arrays, now, loads
                )

            # Saturation anti-windup: freeze integrator when actuators cannot
            # absorb the requested correction (settling timers or at physical
            # limits).  Prevents further wind-up while the system is saturated.
            if abs(pid_output) > 1.0 and abs(final_remaining) >= 0.95 * abs(pid_output):
                self._pid.freeze_integrator()

            # Actual watts absorbed by numeric loads in this cycle
            load_absorbed_w = sum(
                (self._current_load_setpoints.get(name, before) - before)
                * _load_w_per_unit[name]
                for name, before in _load_sp_before.items()
            )

            # 7c. Switch loads: feedforward of numeric load AND array changes
            # so switch decisions do not double-take corrections made this cycle
            residual = await self._apply_load_switch_hysteresis(
                residual + load_absorbed_w - array_absorbed_w, now, loads
            )

            # 7d. Switch arrays: hysteresis on updated residual
            residual = await self._apply_switch_hysteresis(residual, now, arrays)

        return ControlCycleResult(
            grid_raw_w=grid_raw,
            grid_filtered_w=filtered,
            pid_output_w=pid_output,
            status=cycle_status,
            setpoints=dict(self._current_setpoints),
            battery_setpoints=dict(self._current_battery_setpoints),
            load_setpoints=dict(self._current_load_setpoints),
        )

    # ------------------------------------------------------------------
    # Internal helpers
    # ------------------------------------------------------------------

    def read_sensor_safe(self, entity_id: str) -> float | None:
        """Read a sensor state as float, returning None on unavailable/non-numeric."""
        state = self._hass.states.get(entity_id)
        if state is None or state.state in ("unavailable", "unknown"):
            return None
        try:
            return float(state.state)
        except ValueError:
            return None

    def read_power_w(self, entity_id: str) -> float | None:
        """Read a power sensor in Watts, converting kW/MW/mW units."""
        state = self._hass.states.get(entity_id)
        if state is None or state.state in ("unavailable", "unknown"):
            return None
        try:
            value = float(state.state)
        except ValueError:
            return None
        attributes = getattr(state, "attributes", None) or {}
        return value * power_unit_factor(attributes.get("unit_of_measurement"))

    def entity_state(self, entity_id: str) -> str | None:
        """Return entity state string, or None if missing/unavailable/unknown."""
        state = self._hass.states.get(entity_id)
        if state is None or state.state in ("unavailable", "unknown"):
            return None
        return str(state.state)

    # ------------------------------------------------------------------
    # Reactive battery layer
    # ------------------------------------------------------------------

    def _read_battery_states(self, batteries: list[BatteryConfig]) -> BatteryState:
        """Read measured battery power and SoC-limited capacities.

        Measured power falls back to the commanded setpoint when the sensor
        is unavailable.  SoC at/above max_soc zeroes the charge capacity;
        SoC at/below min_soc zeroes the discharge capacity.
        """
        state = BatteryState()
        for battery in batteries:
            actual = self.read_power_w(battery.sensor_entity)
            if actual is None:
                actual = self._current_battery_setpoints.get(battery.name, 0.0)
            state.actuals[battery.name] = actual

            soc = (
                self.read_sensor_safe(battery.soc_sensor_entity)
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

    def _discharge_allowed(
        self, arrays: list[ArrayConfig], loads: list[LoadConfig]
    ) -> bool:
        """Battery discharge is the last resort: PV at max and loads off/min."""
        numeric_arrays = [a for a in arrays if not a.is_switch]
        arrays_maxed = all(
            self._current_setpoints.get(a.name, a.setpoint_max) >= a.setpoint_max
            for a in numeric_arrays
        )
        loads_off = all(
            self._current_load_setpoints.get(ld.name, 0.0)
            <= (0.0 if ld.is_switch else ld.setpoint_min)
            for ld in loads
        )
        return arrays_maxed and loads_off

    def _pv_recovery_w(
        self,
        arrays: list[ArrayConfig],
        battery_state: BatteryState,
        filtered: float,
        now: float,
    ) -> float:
        """Virtual import to reopen curtailed PV while batteries can absorb it.

        Without this, arrays stay curtailed once the battery has backed off
        export: the grid sits at zero, so the PID never reopens them and the
        spare charge capacity is wasted.
        """
        if not battery_state.actuals:
            return 0.0
        # Recovery deliberately creates a transient export for the battery to
        # absorb — only valid in modes where export is acceptable.
        if self._mode not in (ControllerMode.ZERO_GRID, ControllerMode.ZERO_IMPORT):
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
            for a in arrays
        )
        if not any_curtailed:
            return 0.0
        charging_w = max(0.0, -battery_state.total_actual)
        spare_charge_w = battery_state.total_charge_cap - charging_w
        if spare_charge_w <= 0.0:
            return 0.0
        return min(spare_charge_w, PV_RECOVERY_STEP_W)

    async def _command_batteries(
        self,
        filtered: float,
        now: float,
        batteries: list[BatteryConfig],
        battery_state: BatteryState,
        arrays: list[ArrayConfig],
        loads: list[LoadConfig],
    ) -> float:
        """Incrementally command reactive batteries to absorb the grid error.

        The desired total battery power is the *measured* total plus the grid
        error (negative = charge), clamped to SoC-limited capacities.
        Discharge is only allowed as a last resort (PV maxed, loads off).

        Returns the residual grid power for downstream layers: the measured
        grid minus the commanded-but-not-yet-measured battery response.  The
        feedforward expires after BATTERY_PENDING_S so a non-responsive
        battery cannot suppress curtailment indefinitely.
        """
        if not batteries:
            return filtered

        total_charge_cap = battery_state.total_charge_cap
        total_discharge_cap = (
            battery_state.total_discharge_cap
            if self._discharge_allowed(arrays, loads)
            else 0.0
        )
        desired_total = clamp(
            battery_state.total_actual + filtered,
            -total_charge_cap,
            total_discharge_cap,
        )

        # Distribute proportionally to per-battery capacity in the active
        # direction (SoC-limited batteries automatically get a zero share).
        for battery in batteries:
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
                previous = self.read_sensor_safe(battery.setpoint_entity) or 0.0
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
            for battery in batteries
            if now < self._battery_pending_until.get(battery.name, 0.0)
        )
        return filtered - pending_w

    async def _distribute_to_numeric_arrays(
        self, delta_w: float, now: float, arrays: list[ArrayConfig]
    ) -> float:
        """Distribute PID output across numeric arrays by available headroom.

        Returns the remaining unabsorbed delta_w (for passing on to loads).
        """
        active = [
            a
            for a in arrays
            if not a.is_switch and now >= self._settling_until.get(a.name, 0.0)
        ]
        if not active or delta_w == 0:
            return delta_w

        # Initialise setpoints from actual entity state on first encounter so
        # headroom is computed from the real position, not assumed setpoint_max.
        for a in active:
            if a.name not in self._current_setpoints:
                state_str = self.entity_state(a.setpoint_entity)
                if state_str is not None:
                    try:
                        self._current_setpoints[a.name] = clamp(
                            float(state_str), a.setpoint_min, a.setpoint_max
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
            return delta_w

        total_absorbed_w = 0.0
        for array in active:
            if array.w_per_unit == 0:
                continue
            share_w = delta_w * headrooms[array.name] / total_headroom
            delta_units = round(share_w / array.w_per_unit)
            if delta_units == 0:
                continue

            # Cloud-shadow guard: only block when *opening* the limit.
            if delta_units > 0 and not self._can_open(array):
                continue

            current = self._current_setpoints[array.name]
            new_sp = clamp(
                current + delta_units, array.setpoint_min, array.setpoint_max
            )
            if new_sp == current:
                continue

            try:
                await self._actuators.write_setpoint(array, new_sp)
            except Exception:
                _LOGGER.exception("Failed to write setpoint for array %s", array.name)
                continue
            total_absorbed_w += (new_sp - current) * array.w_per_unit
            self._current_setpoints[array.name] = new_sp
            settle_s = (
                (array.settling_up_s or array.settling_time_s)
                if delta_units > 0
                else (array.settling_down_s or array.settling_time_s)
            )
            self._settling_until[array.name] = now + settle_s

        return delta_w - total_absorbed_w

    def _can_open(self, array: ArrayConfig) -> bool:
        """Return True if the array may increase its setpoint (cloud-shadow guard).

        Prevents raising the limit when the inverter's actual output is well
        below the current commanded value — a sign of cloud shadow.  Fails
        open when no sensor is configured or the sensor is unavailable.
        """
        if array.power_sensor_entity is None:
            return True
        actual = self.read_power_w(array.power_sensor_entity)
        if actual is None:
            return True
        current_sp = self._current_setpoints.get(array.name, array.setpoint_max)
        return actual >= (current_sp - 1) * array.w_per_unit

    async def _distribute_to_numeric_loads(
        self, delta_w: float, now: float, loads: list[LoadConfig]
    ) -> float:
        """Distribute correction across numeric loads by priority (greedy).

        delta_w < 0: exporting surplus → increase loads, highest priority first.
        delta_w > 0: importing deficit → decrease loads, lowest priority first.

        Returns remaining unabsorbed delta_w.
        """
        active = [
            ld
            for ld in loads
            if not ld.is_switch and now >= self._load_settling_until.get(ld.name, 0.0)
        ]
        if not active or delta_w == 0:
            return delta_w

        # Initialise setpoints from actual entity state on first encounter
        for load in active:
            if load.name not in self._current_load_setpoints:
                state_str = self.entity_state(load.setpoint_entity)
                if state_str is not None:
                    try:
                        self._current_load_setpoints[load.name] = clamp(
                            float(state_str), load.setpoint_min, load.setpoint_max
                        )
                    except ValueError:
                        self._current_load_setpoints[load.name] = load.setpoint_min
                else:
                    self._current_load_setpoints[load.name] = load.setpoint_min

        remaining = delta_w

        if delta_w < 0:
            # Exporting surplus → increase loads, highest priority (lowest number) first
            ordered = sorted(active, key=lambda ld: ld.priority)
            for load in ordered:
                if remaining >= 0:
                    break
                current = self._current_load_setpoints[load.name]
                available_w = load.headroom_increase_w(current)
                if available_w <= 0:
                    continue
                # Loads with a minimum active power only turn on when the
                # surplus covers that minimum — otherwise snapping up to the
                # minimum would create grid import.
                if (
                    load.absolute_min_w is not None
                    and current * load.w_per_unit < load.absolute_min_w
                    and -remaining < load.absolute_min_w
                ):
                    continue
                take_w = min(available_w, -remaining)
                delta_units = round(take_w / load.w_per_unit)
                if delta_units == 0:
                    continue
                new_sp = clamp(
                    current + delta_units, load.setpoint_min, load.setpoint_max
                )
                new_sp = load.snap_setpoint(new_sp, increasing=True)
                if new_sp == current:
                    continue
                actual_w = (new_sp - current) * load.w_per_unit
                try:
                    await self._actuators.write_numeric_entity(
                        load.setpoint_entity, new_sp
                    )
                except Exception:
                    _LOGGER.exception("Failed to write setpoint for load %s", load.name)
                    continue
                self._current_load_setpoints[load.name] = new_sp
                self._load_settling_until[load.name] = now + load.settling_time_s
                remaining += actual_w
        else:
            # Importing deficit → decrease loads, lowest priority (highest number) first
            ordered = sorted(active, key=lambda ld: -ld.priority)
            for load in ordered:
                if remaining <= 0:
                    break
                current = self._current_load_setpoints[load.name]
                available_w = load.headroom_decrease_w(current)
                if available_w <= 0:
                    continue
                take_w = min(available_w, remaining)
                delta_units = round(take_w / load.w_per_unit)
                if delta_units == 0:
                    continue
                new_sp = clamp(
                    current - delta_units, load.setpoint_min, load.setpoint_max
                )
                new_sp = load.snap_setpoint(new_sp, increasing=False)
                if new_sp == current:
                    continue
                actual_w = (current - new_sp) * load.w_per_unit
                try:
                    await self._actuators.write_numeric_entity(
                        load.setpoint_entity, new_sp
                    )
                except Exception:
                    _LOGGER.exception("Failed to write setpoint for load %s", load.name)
                    continue
                self._current_load_setpoints[load.name] = new_sp
                self._load_settling_until[load.name] = now + load.settling_time_s
                remaining -= actual_w

        return remaining

    async def _apply_load_switch_hysteresis(
        self, residual: float, now: float, loads: list[LoadConfig]
    ) -> float:
        """Turn switch loads on/off based on residual power.

        Returns updated residual with feedforward applied for each switch action.
        """
        for load in sorted(loads, key=lambda ld: ld.priority):
            if not load.is_switch:
                continue
            if now < self._load_settling_until.get(load.name, 0.0):
                continue

            # Initialise from actual entity state on first encounter
            if load.name not in self._current_load_setpoints:
                state_str = self.entity_state(load.setpoint_entity)
                if state_str is not None:
                    self._current_load_setpoints[load.name] = (
                        1.0 if state_str == "on" else 0.0
                    )
                else:
                    self._current_load_setpoints[load.name] = 0.0

            is_on = self._current_load_setpoints[load.name] > 0.0

            if not is_on and residual <= -load.power_w:
                try:
                    await self._actuators.write_switch_entity(
                        load.setpoint_entity, True
                    )
                except Exception:
                    _LOGGER.exception("Failed to turn on load %s", load.name)
                    continue
                self._current_load_setpoints[load.name] = 1.0
                self._load_settling_until[load.name] = now + load.switch_debounce_s
                residual += load.power_w
            elif is_on and residual >= 0:
                try:
                    await self._actuators.write_switch_entity(
                        load.setpoint_entity, False
                    )
                except Exception:
                    _LOGGER.exception("Failed to turn off load %s", load.name)
                    continue
                self._current_load_setpoints[load.name] = 0.0
                self._load_settling_until[load.name] = now + load.switch_debounce_s
                residual -= load.power_w

        return residual

    async def _apply_switch_hysteresis(
        self, residual: float, now: float, arrays: list[ArrayConfig]
    ) -> float:
        """Turn switch arrays on/off based on residual power.

        Returns the residual with feedforward applied for each switch action.
        """
        for array in arrays:
            if not array.is_switch:
                continue
            if now < self._settling_until.get(array.name, 0.0):
                continue

            if array.name not in self._current_setpoints:
                state_str = self.entity_state(array.setpoint_entity)
                if state_str is not None:
                    initial = (
                        array.setpoint_max if state_str == "on" else array.setpoint_min
                    )
                else:
                    initial = array.setpoint_min
                self._current_setpoints[array.name] = initial
            current = self._current_setpoints[array.name]
            is_on = current > array.setpoint_min

            if not is_on and residual >= array.switch_on_threshold_w:
                new_sp = array.setpoint_max
                residual_delta = -array.max_power_w
            elif is_on and residual <= -array.switch_off_threshold_w:
                new_sp = array.setpoint_min
                residual_delta = array.max_power_w
            else:
                continue

            await self._actuators.write_setpoint(array, new_sp)
            self._current_setpoints[array.name] = new_sp
            self._settling_until[array.name] = now + array.switch_debounce_s
            residual += residual_delta

        return residual

    async def _set_maximize_export(
        self,
        arrays: list[ArrayConfig],
        loads: list[LoadConfig],
        batteries: list[BatteryConfig],
    ) -> None:
        """Maximize export: arrays → max, loads → off, batteries → discharge max."""
        for array in arrays:
            new_sp = array.setpoint_max
            try:
                await self._actuators.write_setpoint(array, new_sp)
            except Exception:
                _LOGGER.exception(
                    "Failed to set array %s to max for maximize_export", array.name
                )
                continue
            self._current_setpoints[array.name] = new_sp
        for load in loads:
            try:
                if load.is_switch:
                    await self._actuators.write_switch_entity(
                        load.setpoint_entity, False
                    )
                    self._current_load_setpoints[load.name] = 0.0
                else:
                    await self._actuators.write_numeric_entity(
                        load.setpoint_entity, load.setpoint_min
                    )
                    self._current_load_setpoints[load.name] = load.setpoint_min
            except Exception:
                _LOGGER.exception(
                    "Failed to set load %s to min for maximize_export", load.name
                )
        for battery in batteries:
            target = battery.max_discharge_w
            try:
                await self._actuators.write_numeric_entity(
                    battery.setpoint_entity, target
                )
            except Exception:
                _LOGGER.exception(
                    "Failed to set battery %s to discharge for maximize_export",
                    battery.name,
                )
                continue
            self._current_battery_setpoints[battery.name] = target

    async def _set_maximize_import(
        self,
        arrays: list[ArrayConfig],
        loads: list[LoadConfig],
        batteries: list[BatteryConfig],
    ) -> None:
        """Maximize import: arrays → off, loads → max, batteries → charge max."""
        for array in arrays:
            new_sp = array.setpoint_min
            try:
                await self._actuators.write_setpoint(array, new_sp)
            except Exception:
                _LOGGER.exception(
                    "Failed to set array %s to min for maximize_import", array.name
                )
                continue
            self._current_setpoints[array.name] = new_sp
        for load in loads:
            try:
                if load.is_switch:
                    await self._actuators.write_switch_entity(
                        load.setpoint_entity, True
                    )
                    self._current_load_setpoints[load.name] = 1.0
                else:
                    await self._actuators.write_numeric_entity(
                        load.setpoint_entity, load.setpoint_max
                    )
                    self._current_load_setpoints[load.name] = load.setpoint_max
            except Exception:
                _LOGGER.exception(
                    "Failed to set load %s to max for maximize_import", load.name
                )
        for battery in batteries:
            target = -battery.max_charge_w
            try:
                await self._actuators.write_numeric_entity(
                    battery.setpoint_entity, target
                )
            except Exception:
                _LOGGER.exception(
                    "Failed to set battery %s to charge for maximize_import",
                    battery.name,
                )
                continue
            self._current_battery_setpoints[battery.name] = target

    async def _enter_load_safe_state(self, loads: list[LoadConfig]) -> None:
        """Move all loads to a neutral fail-safe state (minimum setpoint / off)."""
        for load in loads:
            try:
                if load.is_switch:
                    await self._actuators.write_switch_entity(
                        load.setpoint_entity, False
                    )
                    self._current_load_setpoints[load.name] = 0.0
                else:
                    await self._actuators.write_numeric_entity(
                        load.setpoint_entity, load.setpoint_min
                    )
                    self._current_load_setpoints[load.name] = load.setpoint_min
            except Exception:
                _LOGGER.exception("Failed to set load %s to safe state", load.name)
