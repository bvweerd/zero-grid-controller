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
    CONTROL_DT_MAX,
    CONTROL_DT_MIN,
    STATUS_ACTIVE,
    STATUS_DEADBAND,
    STATUS_DISABLED,
    ControllerStatus,
)
from .load import LoadConfig
from .pid import PIDController
from .utils import clamp

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
    ) -> None:
        self._hass = hass
        self._pid = pid
        self._actuators = actuators
        self._ewm_alpha = ewm_alpha
        self._deadband_w = deadband_w

        self._filtered_w: float | None = None
        self._filter_sample_count: int = 0
        self._last_update: float = time.monotonic()

        self._current_setpoints: dict[str, float] = {}
        self._current_battery_setpoints: dict[str, float] = {}
        self._current_load_setpoints: dict[str, float] = {}
        self._settling_until: dict[str, float] = {}
        self._load_settling_until: dict[str, float] = {}

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
    ) -> None:
        """Update PID and filter parameters, preserving all state dicts."""
        self._pid = pid
        self._ewm_alpha = ewm_alpha
        self._deadband_w = deadband_w

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
            await self._actuators.enter_safe_state(
                arrays, batteries, self._current_setpoints
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
                arrays, batteries, self._current_setpoints
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

        # 5. Deadband check
        if abs(filtered) < self._deadband_w:
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

        # 6. Battery charge layer (export: grid < 0 → charge batteries)
        if filtered < 0 and batteries:
            total_charge_cap = sum(b.max_charge_w for b in batteries)
            if total_charge_cap > 0:
                charge_w = min(total_charge_cap, abs(filtered))
                for battery in batteries:
                    target = -(charge_w * battery.max_charge_w / total_charge_cap)
                    try:
                        await self._actuators.write_numeric_entity(
                            battery.setpoint_entity, target
                        )
                    except Exception:
                        _LOGGER.exception(
                            "Failed to write charge setpoint for %s", battery.name
                        )
                    else:
                        self._current_battery_setpoints[battery.name] = target

        elif filtered > 0 and batteries:
            # Reset charge targets when importing
            for battery in batteries:
                if self._current_battery_setpoints.get(battery.name, 0.0) < 0:
                    try:
                        await self._actuators.write_numeric_entity(
                            battery.setpoint_entity, 0.0
                        )
                    except Exception:
                        _LOGGER.exception(
                            "Failed to reset charge setpoint for %s", battery.name
                        )
                    else:
                        self._current_battery_setpoints[battery.name] = 0.0

        # 7. Residual after battery pre-compensation — use actual measured power
        residual = filtered - self._read_battery_actual_w(batteries)

        # 7a. PID on residual
        # Negate: positive output when importing (open PV / reduce load)
        pid_output = self._pid.compute(-residual, dt)

        # 7b. Numeric correction
        _load_sp_before = {
            ld.name: self._current_load_setpoints.get(ld.name, ld.setpoint_min)
            for ld in loads
            if not ld.is_switch
        }
        _load_w_per_unit = {ld.name: ld.w_per_unit for ld in loads if not ld.is_switch}

        if pid_output < 0:
            # Exporting surplus → increase loads, then curtail arrays
            remaining = await self._distribute_to_numeric_loads(pid_output, now, loads)
            await self._distribute_to_numeric_arrays(remaining, now, arrays)
        else:
            # Importing deficit → open arrays, then reduce loads
            remaining = await self._distribute_to_numeric_arrays(
                pid_output, now, arrays
            )
            await self._distribute_to_numeric_loads(remaining, now, loads)

        # Actual watts absorbed by numeric loads in this cycle
        load_absorbed_w = sum(
            (self._current_load_setpoints.get(name, before) - before)
            * _load_w_per_unit[name]
            for name, before in _load_sp_before.items()
        )

        # 7c. Switch loads: feedforward updates residual for each decision
        residual = await self._apply_load_switch_hysteresis(
            residual + load_absorbed_w, now, loads
        )

        # 7d. Switch arrays: hysteresis on updated residual
        await self._apply_switch_hysteresis(residual, now, arrays)

        # 8. Battery discharge layer (all numeric PV at max AND still importing)
        if filtered > 0 and batteries:
            numeric = [a for a in arrays if not a.is_switch]
            all_maxed = all(
                self._current_setpoints.get(a.name, a.setpoint_max) >= a.setpoint_max
                for a in numeric
            )
            if all_maxed or not numeric:
                total_discharge_cap = sum(b.max_discharge_w for b in batteries)
                if total_discharge_cap > 0:
                    discharge_w = min(total_discharge_cap, abs(residual))
                    for battery in batteries:
                        target = (
                            discharge_w * battery.max_discharge_w / total_discharge_cap
                        )
                        try:
                            await self._actuators.write_numeric_entity(
                                battery.setpoint_entity, target
                            )
                        except Exception:
                            _LOGGER.exception(
                                "Failed to write discharge setpoint for %s",
                                battery.name,
                            )
                        else:
                            self._current_battery_setpoints[battery.name] = target

        return ControlCycleResult(
            grid_raw_w=grid_raw,
            grid_filtered_w=filtered,
            pid_output_w=pid_output,
            status=STATUS_ACTIVE,
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

    def entity_state(self, entity_id: str) -> str | None:
        """Return entity state string, or None if missing/unavailable/unknown."""
        state = self._hass.states.get(entity_id)
        if state is None or state.state in ("unavailable", "unknown"):
            return None
        return str(state.state)

    def _read_battery_actual_w(self, batteries: list[BatteryConfig]) -> float:
        """Return total actual battery power, falling back to commanded setpoint."""
        total = 0.0
        for battery in batteries:
            actual = self.read_sensor_safe(battery.sensor_entity)
            total += (
                actual
                if actual is not None
                else self._current_battery_setpoints.get(battery.name, 0.0)
            )
        return total

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
        actual = self.read_sensor_safe(array.power_sensor_entity)
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
    ) -> None:
        """Turn switch arrays on/off based on residual power."""
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
            elif is_on and residual <= -array.switch_off_threshold_w:
                new_sp = array.setpoint_min
            else:
                continue

            await self._actuators.write_setpoint(array, new_sp)
            self._current_setpoints[array.name] = new_sp
            self._settling_until[array.name] = now + array.switch_debounce_s

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
