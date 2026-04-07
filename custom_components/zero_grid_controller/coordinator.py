"""Main coordinator for Zero Grid Controller."""

from __future__ import annotations

import contextlib
import logging
import math
import time
from dataclasses import dataclass, field
from datetime import timedelta
from typing import Any

from homeassistant.config_entries import ConfigEntry
from homeassistant.const import ATTR_ENTITY_ID
from homeassistant.core import HomeAssistant
from homeassistant.helpers.update_coordinator import DataUpdateCoordinator, UpdateFailed

from .array import ArrayConfig, array_config_from_subentry
from .battery import BatteryConfig, battery_config_from_subentry
from .const import (
    ARRAY_SUBENTRY_TYPE,
    BATTERY_SUBENTRY_TYPE,
    CONF_DEADBAND_W,
    CONF_ESTIMATOR_STATE,
    CONF_EWM_ALPHA,
    CONF_EXPERT_MODE,
    CONF_GRID_EXPORT_SENSORS,
    CONF_GRID_IMPORT_SENSORS,
    CONF_INVERT_SIGN,
    CONF_KD,
    CONF_KI,
    CONF_KP,
    CONF_MODE_GUARD_ENABLED,
    CONF_MODE_GUARD_ENTITY,
    CONF_MODE_GUARD_MAPPING,
    CONF_OUTPUT_MAX_W,
    CONTROL_DT_MAX,
    CONTROL_DT_MIN,
    CONTROL_INTERVAL_S,
    DEFAULT_DEADBAND_W,
    DEFAULT_EWM_ALPHA,
    DEFAULT_KD,
    DEFAULT_KI,
    DEFAULT_KP,
    DEFAULT_OUTPUT_MAX_W,
    DEFAULT_RESPONSE_FACTOR,
    DEFAULT_SETTLING_TIME_S,
    DOMAIN,
    MODE_ACTIVE,
    MODE_DISABLED,
    MODE_PASSIVE,
    OUTPUT_TYPE_SWITCH,
    STATUS_ACTIVE,
    STATUS_CLOUD_SHADOW,
    STATUS_DEADBAND,
    STATUS_DISABLED,
    STATUS_PASSIVE,
    STATUS_SATURATION,
)
from .estimator import RLSEstimator
from .pid import PIDController
from .repairs import dismiss_grid_sensor_unavailable, raise_grid_sensor_unavailable

_LOGGER = logging.getLogger(__name__)

PARALLEL_UPDATES = 0


@dataclass
class ZGCResult:
    """Data returned by the coordinator each update cycle."""

    grid_raw_w: float
    grid_filtered_w: float
    pid_output_w: float
    pid_p_w: float
    pid_i_w: float
    pid_d_w: float
    mode: str
    battery_clipping: bool
    learning_status: str
    setpoints: dict[str, float] = field(default_factory=dict)
    battery_setpoints: dict[str, float] = field(default_factory=dict)
    array_clipping: dict[str, bool] = field(default_factory=dict)
    array_gain_k: dict[str, float | None] = field(default_factory=dict)
    array_calibration: dict[str, str] = field(default_factory=dict)


def _clamp(value: float, lo: float, hi: float) -> float:
    return max(lo, min(hi, value))


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

        self._init_from_entry(entry)

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
        self._expert_mode: bool = bool(data.get(CONF_EXPERT_MODE, False))

        # Grid measurement config
        self._import_sensors: list[str] = list(data.get(CONF_GRID_IMPORT_SENSORS) or [])
        self._export_sensors: list[str] = list(data.get(CONF_GRID_EXPORT_SENSORS) or [])
        self._invert_sign: bool = bool(data.get(CONF_INVERT_SIGN, False))

        # Batteries from subentries
        self._batteries: list[BatteryConfig] = [
            battery_config_from_subentry(s.subentry_id, dict(s.data))
            for s in entry.subentries.values()
            if s.subentry_type == BATTERY_SUBENTRY_TYPE
        ]

        # Mode guard config
        self._mode_guard_enabled: bool = bool(data.get(CONF_MODE_GUARD_ENABLED, False))
        self._mode_guard_entity: str | None = data.get(CONF_MODE_GUARD_ENTITY)
        self._mode_guard_mapping: dict[str, str] = data.get(CONF_MODE_GUARD_MAPPING, {})

        # Arrays from subentries
        self._arrays: list[ArrayConfig] = [
            array_config_from_subentry(s.subentry_id, dict(s.data))
            for s in entry.subentries.values()
            if s.subentry_type == ARRAY_SUBENTRY_TYPE
        ]
        # Sort by max power (highest first) for intelligent priority
        # Arrays with more capacity are served first when distributing setpoints
        self._arrays.sort(key=lambda a: a.max_power_w, reverse=True)

        # Runtime state
        self._filtered_w: float = 0.0
        self._current_setpoints: dict[str, float] = {}
        self._settling_until: dict[str, float] = {}
        for array in self._arrays:
            self._current_setpoints.setdefault(array.name, array.setpoint_max)

        # Estimators: restore saved state or create fresh
        saved: dict[str, Any] = data.get(CONF_ESTIMATOR_STATE, {})
        self._estimators: dict[str, RLSEstimator] = {}
        for array in self._arrays:
            restored = None
            if array.name in saved:
                with contextlib.suppress(KeyError, TypeError):
                    restored = RLSEstimator.from_dict(saved[array.name])
            self._estimators[array.name] = restored or RLSEstimator(
                settling_time_s=array.settling_time_s
            )

        self._pending_estimates: dict[str, tuple[float, float, float]] = {}
        self._override_setpoints: dict[
            str, tuple[float, float]
        ] = {}  # name -> (value, expires_at)
        self._grid_unavailable_reported: bool = False
        self._controller_enabled: bool = True

    def reload_config(self) -> None:
        """Re-read config from the config entry (called after options update)."""
        old_setpoints = dict(self._current_setpoints)
        self._init_from_entry(self._entry)
        # Preserve any setpoints already applied
        for name, sp in old_setpoints.items():
            if name in self._current_setpoints:
                self._current_setpoints[name] = sp

    def set_ewm_alpha(self, value: float) -> None:
        """Update the EWM filter smoothing coefficient live."""
        self._ewm_alpha = value

    def set_deadband(self, value: float) -> None:
        """Update the deadband threshold live."""
        self._deadband_w = value

    def apply_array_config_update(
        self, array_name: str, updates: dict[str, Any]
    ) -> None:
        """Live-update a single array's parameters (e.g. from number entity changes)."""
        for array in self._arrays:
            if array.name == array_name:
                for key, value in updates.items():
                    if hasattr(array, key):
                        setattr(array, key, value)
                break

    def override_setpoint(
        self, array_name: str, value: float, duration_s: float = 300.0
    ) -> None:
        """Force a setpoint for an array, bypassing PID for up to duration_s seconds."""
        self._override_setpoints[array_name] = (value, time.monotonic() + duration_s)

    def reset_pid(self) -> None:
        """Reset the PID integrator and history."""
        self._pid.reset()

    def set_controller_enabled(self, enabled: bool) -> None:
        """Enable or disable the controller."""
        self._controller_enabled = enabled

    async def apply_disabled_state(self) -> None:
        """Set PV arrays to max and batteries to 0 when controller is disabled."""
        # Set all PV arrays to maximum output
        for array in self._arrays:
            if not array.enabled:
                continue
            try:
                await self.hass.services.async_call(
                    "number",
                    "set_value",
                    {
                        ATTR_ENTITY_ID: array.setpoint_entity,
                        "value": array.setpoint_max,
                    },
                )
                _LOGGER.debug(
                    "Set array %s to max: %.1f", array.name, array.setpoint_max
                )
            except Exception as err:
                _LOGGER.error("Failed to set array %s to max: %s", array.name, err)

        # Set all batteries to 0 W
        for battery in self._batteries:
            if not battery.control_enabled or not battery.setpoint_entity:
                continue
            try:
                await self.hass.services.async_call(
                    "number",
                    "set_value",
                    {ATTR_ENTITY_ID: battery.setpoint_entity, "value": 0.0},
                )
                _LOGGER.debug("Set battery %s to 0 W", battery.name)
            except Exception as err:
                _LOGGER.error("Failed to set battery %s to 0: %s", battery.name, err)

    # ------------------------------------------------------------------
    # DataUpdateCoordinator implementation
    # ------------------------------------------------------------------

    async def _async_update_data(self) -> ZGCResult:
        """Main control loop — called every update_interval seconds."""
        now = time.monotonic()
        dt = max(now - self._last_update, CONTROL_DT_MIN)
        dt = min(dt, CONTROL_DT_MAX)
        self._last_update = now

        try:
            return await self._run_control_loop(dt, now)
        except Exception as err:
            raise UpdateFailed(
                translation_domain=DOMAIN,
                translation_key="coordinator_update_failed",
                translation_placeholders={"error": str(err)},
            ) from err

    async def _run_control_loop(self, dt: float, now: float) -> ZGCResult:
        # --- Check if controller is enabled ---
        if not self._controller_enabled:
            _LOGGER.debug("Controller is disabled, skipping control loop")
            self._pid.reset()
            return self._make_result(0.0, STATUS_DISABLED)

        # --- Repair issue tracking ---
        if self._is_grid_sensor_unavailable():
            if not self._grid_unavailable_reported:
                raise_grid_sensor_unavailable(self.hass)
                self._grid_unavailable_reported = True
        else:
            if self._grid_unavailable_reported:
                dismiss_grid_sensor_unavailable(self.hass)
                self._grid_unavailable_reported = False

        # --- 1. Read & normalise grid measurement ---
        raw_w = self._read_grid()

        # --- 2. EWM low-pass filter ---
        self._filtered_w = (
            self._ewm_alpha * raw_w + (1.0 - self._ewm_alpha) * self._filtered_w
        )

        # --- 3. Mode guard ---
        mode = self._resolve_mode()
        if mode == MODE_DISABLED:
            self._pid.reset()
            return self._make_result(raw_w, STATUS_DISABLED)

        # --- 4. Deadband ---
        if abs(self._filtered_w) < self._deadband_w:
            self._pid.freeze_integrator()
            return self._make_result(raw_w, STATUS_DEADBAND)

        # --- 5. Clipping & cloud-shadow detection ---
        battery_clipping = self._detect_battery_clipping()
        # Capture early so _make_result does not repeat the detection

        arrays_with_pv = [a for a in self._arrays if a.pv_power_entity]
        pv_clipping_any = False
        if arrays_with_pv:
            for array in arrays_with_pv:
                pv_w = self._read_sensor_safe(array.pv_power_entity or "", 0.0)
                sp_w = (
                    self._current_setpoints.get(array.name, array.setpoint_max)
                    * array.w_per_unit
                )
                if array.is_clipping_active(sp_w, pv_w):
                    pv_clipping_any = True
                    break
            if not pv_clipping_any and not battery_clipping:
                self._pid.freeze_integrator()
                return self._make_result(raw_w, STATUS_CLOUD_SHADOW)

        saturation = battery_clipping and not pv_clipping_any and bool(arrays_with_pv)

        # --- 5b. Freeze integrator when no arrays can act (all settling/overridden) ---
        if self._arrays and not any(
            a.enabled
            and now >= self._settling_until.get(a.name, 0.0)
            and a.name not in self._override_setpoints
            for a in self._arrays
        ):
            self._pid.freeze_integrator()

        # --- 6. PID compute ---
        delta_w = self._pid.compute(self._filtered_w, dt)

        # --- 7. Passive mode: only tighten, never open ---
        if mode == MODE_PASSIVE and delta_w < 0:
            delta_w = 0.0

        # --- 8. Distribute and write setpoints ---
        written = await self._distribute_and_write(delta_w, now)

        # --- 9. Update RLS estimators (one-cycle delay) ---
        self._update_estimators(written, now)

        # --- 10. Optional: write battery setpoint ---
        battery_setpoints = await self._write_battery_targets(self._filtered_w)

        # --- 11. Persist estimator states (fire-and-forget option update) ---
        self._persist_estimators()

        if saturation:
            status = STATUS_SATURATION
        elif mode == MODE_PASSIVE:
            status = STATUS_PASSIVE
        else:
            status = STATUS_ACTIVE
        return self._make_result(raw_w, status, battery_clipping, battery_setpoints)

    # ------------------------------------------------------------------
    # Grid reading
    # ------------------------------------------------------------------

    def _is_grid_sensor_unavailable(self) -> bool:
        """Return True if any configured import sensor is unavailable."""

        def _unavail(eid: str) -> bool:
            s = self.hass.states.get(eid)
            return s is None or s.state in ("unknown", "unavailable")

        return any(_unavail(e) for e in self._import_sensors)

    def _read_grid(self) -> float:
        """Read and normalise grid power in Watts.

        Positive = importing from grid.
        Negative = exporting to grid.
        grid_w = sum(import_sensors) - sum(export_sensors)
        """
        import_w = sum(self._read_sensor_safe(e, 0.0) for e in self._import_sensors)
        export_w = sum(self._read_sensor_safe(e, 0.0) for e in self._export_sensors)
        grid_w = import_w - export_w
        return -grid_w if self._invert_sign else grid_w

    def read_grid_w(self) -> float | None:
        """Public method for external grid reading (e.g. calibrator).

        Returns None if any import sensor is unavailable.
        """
        if self._is_grid_sensor_unavailable():
            return None
        return self._read_grid()

    def _read_sensor_safe(self, entity_id: str, default: float) -> float:
        """Read a numeric sensor state, returning default on error."""
        if not entity_id:
            return default
        state = self.hass.states.get(entity_id)
        if state is None or state.state in ("unknown", "unavailable"):
            return default
        try:
            return float(state.state)
        except ValueError:
            return default

    # ------------------------------------------------------------------
    # Mode guard
    # ------------------------------------------------------------------

    def _resolve_mode(self) -> str:
        """Determine the operating mode from the mode guard entity (if configured)."""
        if not self._mode_guard_enabled or not self._mode_guard_entity:
            return MODE_ACTIVE

        state = self.hass.states.get(self._mode_guard_entity)
        if state is None or state.state in ("unknown", "unavailable"):
            return MODE_ACTIVE

        mapped = self._mode_guard_mapping.get(state.state, MODE_ACTIVE)
        if mapped in (MODE_ACTIVE, MODE_PASSIVE, MODE_DISABLED):
            return mapped
        return MODE_ACTIVE

    # ------------------------------------------------------------------
    # Battery clipping detection
    # ------------------------------------------------------------------

    def _detect_battery_clipping(self) -> bool:
        """True if any battery is at maximum charge power (absorbing all it can)."""
        for battery in self._batteries:
            battery_w = self._read_sensor_safe(battery.sensor_entity, 0.0)
            if battery.is_clipping(battery_w):
                return True
        return False

    # ------------------------------------------------------------------
    # Setpoint distribution
    # ------------------------------------------------------------------

    async def _distribute_and_write(
        self, delta_w: float, now: float
    ) -> dict[str, float]:
        """Distribute delta_w across active arrays proportional to headroom.

        Returns a dict of {array_name: actual_delta_w_written}.
        """
        # Clean up expired overrides
        expired = [
            name for name, (_, exp) in self._override_setpoints.items() if now >= exp
        ]
        for name in expired:
            del self._override_setpoints[name]

        active = [
            a
            for a in self._arrays
            if a.enabled
            and now >= self._settling_until.get(a.name, 0.0)
            and a.name not in self._override_setpoints
        ]

        if not active or delta_w == 0:
            return {}

        headrooms = {
            a.name: (
                a.headroom_up_w(self._current_setpoints.get(a.name, a.setpoint_max))
                if delta_w > 0
                else a.headroom_down_w(
                    self._current_setpoints.get(a.name, a.setpoint_max)
                )
            )
            * a.response_factor
            for a in active
        }
        total = sum(headrooms.values())
        if total <= 0:
            return {}

        written: dict[str, float] = {}
        for array in active:
            share_w = delta_w * headrooms[array.name] / total
            delta_unit = share_w / array.w_per_unit
            # Round towards zero to avoid tiny oscillations
            delta_unit = (
                math.floor(delta_unit) if delta_w > 0 else math.ceil(delta_unit)
            )
            if delta_unit == 0:
                continue

            current = self._current_setpoints.get(array.name, array.setpoint_max)
            new_sp = _clamp(
                current - delta_unit,
                array.setpoint_min,
                array.setpoint_max,
            )
            actual_delta = new_sp - current
            if actual_delta == 0:
                continue

            await self._write_setpoint(array, new_sp)
            self._current_setpoints[array.name] = new_sp
            self._settling_until[array.name] = now + array.settling_time_s
            written[array.name] = actual_delta * array.w_per_unit

        return written

    async def _write_setpoint(self, array: ArrayConfig, value: float) -> None:
        """Write a setpoint to the inverter entity."""
        if array.output_type == OUTPUT_TYPE_SWITCH:
            service = "turn_on" if value > 0 else "turn_off"
            await self.hass.services.async_call(
                "switch", service, {ATTR_ENTITY_ID: array.setpoint_entity}
            )
        else:
            await self.hass.services.async_call(
                "number",
                "set_value",
                {ATTR_ENTITY_ID: array.setpoint_entity, "value": value},
            )

    # ------------------------------------------------------------------
    # RLS estimator
    # ------------------------------------------------------------------

    def _update_estimators(self, written: dict[str, float], now: float) -> None:
        """Two-step measurement: record delta, then observe response after settling.

        Only update the estimator if there was no new step for this array
        (clean measurement, not mixed signals).
        """
        # Record new steps with timestamp
        for name, delta_w in written.items():
            self._pending_estimates[name] = (delta_w, self._filtered_w, now)

        # Observe responses — but only after the array has had time to settle
        for name, pending in list(self._pending_estimates.items()):
            prev_delta_sp_w, prev_grid_w, step_time = pending
            if name in written:
                continue  # new step this cycle — wait
            array = self.get_array(name)
            settling = array.settling_time_s if array else DEFAULT_SETTLING_TIME_S
            if now - step_time < settling:
                continue  # not settled yet
            delta_grid = self._filtered_w - prev_grid_w
            if abs(prev_delta_sp_w) > 1:
                estimator = self._estimators.get(name)
                if estimator:
                    estimator.update(prev_delta_sp_w, delta_grid)
                    if estimator.is_reliable and not self._expert_mode:
                        old_kp = self._pid.kp
                        new_kp = estimator.suggest_kp(old_kp, DEFAULT_RESPONSE_FACTOR)
                        # Scale Ki proportionally to keep integral time constant Ti = Kp/Ki
                        new_ki = (
                            self._pid.ki * (new_kp / old_kp)
                            if old_kp > 0
                            else self._pid.ki
                        )
                        self._pid.set_gains(new_kp, new_ki, self._pid.kd)
            del self._pending_estimates[name]

    def _persist_estimators(self) -> None:
        """Write estimator states back to entry.options for HA restart persistence."""
        state_dict = {name: est.to_dict() for name, est in self._estimators.items()}
        # Use hass.loop-safe update — do not await here
        self.hass.config_entries.async_update_entry(
            self._entry,
            options={**self._entry.options, CONF_ESTIMATOR_STATE: state_dict},
        )

    # ------------------------------------------------------------------
    # Battery setpoint
    # ------------------------------------------------------------------

    async def _write_battery_targets(self, grid_w: float) -> dict[str, float]:
        """Write battery power targets to reduce grid imbalance.

        Distributes the target across all batteries with control_enabled,
        proportionally weighted by max power × response_factor.
        Returns a dict of {battery_name: target_w} for all controlled batteries.
        """
        all_batteries = [b for b in self._batteries if b.control_enabled]
        targets: dict[str, float] = {}

        if not all_batteries:
            return targets

        # Target is negative grid (if importing, discharge battery)
        total_target = -grid_w

        # Weighted capacity for proportional distribution
        if total_target > 0:  # Discharge
            total_capacity = sum(
                b.max_discharge_w * b.response_factor for b in all_batteries
            )
        else:  # Charge
            total_capacity = sum(
                b.max_charge_w * b.response_factor for b in all_batteries
            )

        if total_capacity <= 0:
            return targets

        for battery in all_batteries:
            if total_target > 0:  # Discharge
                weight = battery.max_discharge_w * battery.response_factor
                battery_target = _clamp(
                    total_target * weight / total_capacity, 0, battery.max_discharge_w
                )
            else:  # Charge
                weight = battery.max_charge_w * battery.response_factor
                battery_target = _clamp(
                    total_target * weight / total_capacity, -battery.max_charge_w, 0
                )

            targets[battery.name] = battery_target

            if battery.setpoint_entity:
                await self.hass.services.async_call(
                    "number",
                    "set_value",
                    {ATTR_ENTITY_ID: battery.setpoint_entity, "value": battery_target},
                )

        return targets

    # ------------------------------------------------------------------
    # Result assembly
    # ------------------------------------------------------------------

    def _make_result(
        self,
        raw_w: float,
        mode: str,
        battery_clipping: bool = False,
        battery_setpoints: dict[str, float] | None = None,
    ) -> ZGCResult:
        components = self._pid.components
        any_reliable = any(est.is_reliable for est in self._estimators.values())
        learning_status = "Calibrated \u2713" if any_reliable else "Learning..."

        setpoints = dict(self._current_setpoints)
        array_clipping: dict[str, bool] = {}
        array_gain_k: dict[str, float | None] = {}
        array_calib: dict[str, str] = {}

        for array in self._arrays:
            pv_w = 0.0
            if array.pv_power_entity:
                pv_w = self._read_sensor_safe(array.pv_power_entity or "", 0.0)
            sp = self._current_setpoints.get(array.name, array.setpoint_max)
            sp_w = sp * array.w_per_unit
            array_clipping[array.name] = array.is_clipping_active(sp_w, pv_w)
            est = self._estimators.get(array.name)
            array_gain_k[array.name] = est.estimated_gain if est else None
            array_calib[array.name] = array.calibration_confidence

        return ZGCResult(
            grid_raw_w=raw_w,
            grid_filtered_w=self._filtered_w,
            pid_output_w=components["p"] + components["i"] + components["d"],
            pid_p_w=components["p"],
            pid_i_w=components["i"],
            pid_d_w=components["d"],
            mode=mode,
            battery_clipping=battery_clipping,
            learning_status=learning_status,
            setpoints=setpoints,
            battery_setpoints=battery_setpoints or {},
            array_clipping=array_clipping,
            array_gain_k=array_gain_k,
            array_calibration=array_calib,
        )

    # ------------------------------------------------------------------
    # Public accessors (for entities)
    # ------------------------------------------------------------------

    @property
    def arrays(self) -> list[ArrayConfig]:
        return list(self._arrays)

    @property
    def batteries(self) -> list[BatteryConfig]:
        return list(self._batteries)

    @property
    def controller_enabled(self) -> bool:
        return self._controller_enabled

    @property
    def pid(self) -> PIDController:
        return self._pid

    @property
    def expert_mode(self) -> bool:
        return self._expert_mode

    def get_array(self, name: str) -> ArrayConfig | None:
        for a in self._arrays:
            if a.name == name:
                return a
        return None

    def get_estimator(self, array_name: str) -> RLSEstimator | None:
        return self._estimators.get(array_name)
