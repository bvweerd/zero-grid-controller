"""Main coordinator for Zero Grid Controller."""

from __future__ import annotations

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
from .const import (
    ARRAY_SUBENTRY_TYPE,
    CONF_BATTERY_CONTROL_ENABLED,
    CONF_BATTERY_MAX_CHARGE_W,
    CONF_BATTERY_SENSOR,
    CONF_BATTERY_SETPOINT_ENTITY,
    CONF_DEADBAND_W,
    CONF_EWM_ALPHA,
    CONF_ESTIMATOR_STATE,
    CONF_EXPERT_MODE,
    CONF_GRID_MEASUREMENT_TYPE,
    CONF_GRID_SENSOR,
    CONF_GRID_SENSOR_EXPORT,
    CONF_GRID_SENSOR_IMPORT,
    CONF_INVERT_SIGN,
    CONF_KD,
    CONF_KI,
    CONF_KP,
    CONF_MODE_GUARD_ENABLED,
    CONF_MODE_GUARD_ENTITY,
    CONF_MODE_GUARD_MAPPING,
    CONF_OUTPUT_MAX_W,
    CONF_RESPONSE_FACTOR,
    DEFAULT_DEADBAND_W,
    DEFAULT_EWM_ALPHA,
    DEFAULT_KD,
    DEFAULT_KI,
    DEFAULT_KP,
    DEFAULT_OUTPUT_MAX_W,
    DEFAULT_RESPONSE_FACTOR,
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
        update_interval_s: int = 5,
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
            kp, ki, kd,
            setpoint=0.0,
            output_min=-output_max,
            output_max=output_max,
        )
        self._ewm_alpha = float(data.get(CONF_EWM_ALPHA, DEFAULT_EWM_ALPHA))
        self._deadband_w = float(data.get(CONF_DEADBAND_W, DEFAULT_DEADBAND_W))
        self._response_factor = float(data.get(CONF_RESPONSE_FACTOR, DEFAULT_RESPONSE_FACTOR))
        self._expert_mode: bool = bool(data.get(CONF_EXPERT_MODE, False))

        # Grid measurement config
        self._grid_entity: str = data.get(CONF_GRID_SENSOR, "")
        self._grid_import_entity: str | None = data.get(CONF_GRID_SENSOR_IMPORT)
        self._grid_export_entity: str | None = data.get(CONF_GRID_SENSOR_EXPORT)
        self._measurement_type: str = data.get(CONF_GRID_MEASUREMENT_TYPE, "net")
        self._invert_sign: bool = bool(data.get(CONF_INVERT_SIGN, False))

        # Battery config
        self._battery_entity: str | None = data.get(CONF_BATTERY_SENSOR)
        self._battery_max_charge_w: float = float(data.get(CONF_BATTERY_MAX_CHARGE_W, 5000.0))
        self._battery_control_enabled: bool = bool(data.get(CONF_BATTERY_CONTROL_ENABLED, False))
        self._battery_setpoint_entity: str | None = data.get(CONF_BATTERY_SETPOINT_ENTITY)

        # Mode guard config
        self._mode_guard_enabled: bool = bool(data.get(CONF_MODE_GUARD_ENABLED, False))
        self._mode_guard_entity: str | None = data.get(CONF_MODE_GUARD_ENTITY)
        self._mode_guard_mapping: dict[str, str] = data.get(CONF_MODE_GUARD_MAPPING, {})

        # Arrays from subentries
        self._arrays: list[ArrayConfig] = [
            array_config_from_subentry(s.subentry_id, s.data)
            for s in entry.subentries.values()
            if s.subentry_type == ARRAY_SUBENTRY_TYPE
        ]
        self._arrays.sort(key=lambda a: a.priority)

        # Runtime state
        self._filtered_w: float = 0.0
        self._current_setpoints: dict[str, float] = {}
        self._settling_until: dict[str, float] = {}
        for array in self._arrays:
            self._current_setpoints.setdefault(array.name, array.setpoint_max)

        # Estimators: restore or create fresh
        saved: dict[str, Any] = data.get(CONF_ESTIMATOR_STATE, {})
        self._estimators: dict[str, RLSEstimator] = {}
        for array in self._arrays:
            if array.name in saved:
                try:
                    self._estimators[array.name] = RLSEstimator.from_dict(
                        saved[array.name]
                    )
                except (KeyError, TypeError):
                    self._estimators[array.name] = RLSEstimator(
                        settling_time_s=array.settling_time_s
                    )
            else:
                self._estimators[array.name] = RLSEstimator(
                    settling_time_s=array.settling_time_s
                )

        self._pending_estimates: dict[str, tuple[float, float]] = {}
        self._override_setpoints: dict[str, tuple[float, float]] = {}  # name -> (value, expires_at)

    def reload_config(self) -> None:
        """Re-read config from the config entry (called after options update)."""
        old_setpoints = dict(self._current_setpoints)
        self._init_from_entry(self._entry)
        # Preserve any setpoints already applied
        for name, sp in old_setpoints.items():
            if name in self._current_setpoints:
                self._current_setpoints[name] = sp

    def apply_array_config_update(self, array_name: str, updates: dict) -> None:
        """Live-update a single array's parameters (e.g. from number entity changes)."""
        for array in self._arrays:
            if array.name == array_name:
                for key, value in updates.items():
                    if hasattr(array, key):
                        setattr(array, key, value)
                break

    def override_setpoint(self, array_name: str, value: float, duration_s: float = 300.0) -> None:
        """Force a setpoint for an array, bypassing PID for up to duration_s seconds."""
        self._override_setpoints[array_name] = (value, time.monotonic() + duration_s)

    def reset_pid(self) -> None:
        """Reset the PID integrator and history."""
        self._pid.reset()

    # ------------------------------------------------------------------
    # DataUpdateCoordinator implementation
    # ------------------------------------------------------------------

    async def _async_update_data(self) -> ZGCResult:
        """Main control loop — called every update_interval seconds."""
        now = time.monotonic()
        dt = max(now - self._last_update, 0.1)
        self._last_update = now

        try:
            return await self._run_control_loop(dt, now)
        except Exception as err:
            raise UpdateFailed(f"ZGC update failed: {err}") from err

    async def _run_control_loop(self, dt: float, now: float) -> ZGCResult:
        # --- 1. Read & normalise grid measurement ---
        raw_w = self._read_grid()

        # --- 2. Optional: subtract battery net power (residual mode) ---
        if self._measurement_type == "residual" and self._battery_entity:
            battery_w = self._read_sensor_safe(self._battery_entity, 0.0)
            raw_w -= battery_w

        # --- 3. EWM low-pass filter ---
        self._filtered_w = (
            self._ewm_alpha * raw_w
            + (1.0 - self._ewm_alpha) * self._filtered_w
        )

        # --- 4. Mode guard ---
        mode = self._resolve_mode()
        if mode == MODE_DISABLED:
            self._pid.reset()
            return self._make_result(raw_w, STATUS_DISABLED)

        # --- 5. Deadband ---
        if abs(self._filtered_w) < self._deadband_w:
            self._pid.freeze_integrator()
            return self._make_result(raw_w, STATUS_DEADBAND)

        # --- 6. Clipping detection ---
        battery_clipping = self._detect_battery_clipping()

        arrays_with_pv = [a for a in self._arrays if a.pv_power_entity]
        pv_clipping_any = False
        if arrays_with_pv:
            for array in arrays_with_pv:
                pv_w = self._read_sensor_safe(array.pv_power_entity, 0.0)
                sp_w = self._current_setpoints.get(array.name, array.setpoint_max) * array.w_per_unit
                if array.is_clipping_active(sp_w, pv_w):
                    pv_clipping_any = True
                    break
            if not pv_clipping_any and not battery_clipping:
                self._pid.freeze_integrator()
                return self._make_result(raw_w, STATUS_CLOUD_SHADOW)

        saturation = battery_clipping and not pv_clipping_any and bool(arrays_with_pv)

        # --- 7. PID compute ---
        delta_w = self._pid.compute(self._filtered_w, dt)

        # --- 8. Passive mode: only tighten, never open ---
        if mode == MODE_PASSIVE and delta_w < 0:
            delta_w = 0.0

        # --- 9. Distribute and write setpoints ---
        written = await self._distribute_and_write(delta_w, now)

        # --- 10. Update RLS estimators (one-cycle delay) ---
        self._update_estimators(written)

        # --- 11. Optional: write battery setpoint ---
        if self._battery_control_enabled and self._battery_setpoint_entity:
            await self._write_battery_target(raw_w)

        # --- 12. Persist estimator states (fire-and-forget option update) ---
        self._persist_estimators()

        status = STATUS_SATURATION if saturation else (
            STATUS_PASSIVE if mode == MODE_PASSIVE else STATUS_ACTIVE
        )
        return self._make_result(raw_w, status)

    # ------------------------------------------------------------------
    # Grid reading
    # ------------------------------------------------------------------

    def _read_grid(self) -> float:
        """Read and normalise grid power in Watts.

        Positive = importing from grid.
        Negative = exporting to grid.
        """
        if self._measurement_type == "split":
            import_w = self._read_sensor_safe(self._grid_import_entity or "", 0.0)
            export_w = self._read_sensor_safe(self._grid_export_entity or "", 0.0)
            return import_w - export_w

        val = self._read_sensor_safe(self._grid_entity, 0.0)
        return -val if self._invert_sign else val

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
        """True if the battery is at maximum charge power (absorbing all it can)."""
        if not self._battery_entity:
            return False
        battery_w = self._read_sensor_safe(self._battery_entity, 0.0)
        return battery_w >= self._battery_max_charge_w * 0.95

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
            name for name, (_, exp) in self._override_setpoints.items()
            if now >= exp
        ]
        for name in expired:
            del self._override_setpoints[name]

        active = [
            a for a in self._arrays
            if a.enabled and now >= self._settling_until.get(a.name, 0.0)
            and a.name not in self._override_setpoints
        ]

        if not active or delta_w == 0:
            return {}

        headrooms = {
            a.name: (
                a.headroom_up_w(self._current_setpoints.get(a.name, a.setpoint_max))
                if delta_w > 0
                else a.headroom_down_w(self._current_setpoints.get(a.name, a.setpoint_max))
            )
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
                current + delta_unit,
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

    def _update_estimators(self, written: dict[str, float]) -> None:
        """Two-step measurement: record delta, then observe response next cycle.

        Only update the estimator if there was no new step for this array
        (clean measurement, not mixed signals).
        """
        # Record new steps
        for name, delta_w in written.items():
            self._pending_estimates[name] = (delta_w, self._filtered_w)

        # Observe responses for arrays where we did NOT step this cycle
        for name, (prev_delta_sp_w, prev_grid_w) in list(
            self._pending_estimates.items()
        ):
            if name in written:
                continue  # New step this cycle — wait for next
            delta_grid = self._filtered_w - prev_grid_w
            if abs(prev_delta_sp_w) > 1:
                estimator = self._estimators.get(name)
                if estimator:
                    estimator.update(prev_delta_sp_w, delta_grid)
                    if estimator.is_reliable and not self._expert_mode:
                        new_kp = estimator.suggest_kp(
                            self._pid.kp, self._response_factor
                        )
                        self._pid.set_gains(new_kp, self._pid.ki, self._pid.kd)
            del self._pending_estimates[name]

    def _persist_estimators(self) -> None:
        """Write estimator states back to entry.options for HA restart persistence."""
        state_dict = {
            name: est.to_dict() for name, est in self._estimators.items()
        }
        # Use hass.loop-safe update — do not await here
        self.hass.config_entries.async_update_entry(
            self._entry,
            options={**self._entry.options, CONF_ESTIMATOR_STATE: state_dict},
        )

    # ------------------------------------------------------------------
    # Battery setpoint
    # ------------------------------------------------------------------

    async def _write_battery_target(self, grid_w: float) -> None:
        """Write a battery power target to reduce grid imbalance."""
        if not self._battery_setpoint_entity:
            return
        target = _clamp(-grid_w, -self._battery_max_charge_w, self._battery_max_charge_w)
        await self.hass.services.async_call(
            "number",
            "set_value",
            {ATTR_ENTITY_ID: self._battery_setpoint_entity, "value": target},
        )

    # ------------------------------------------------------------------
    # Result assembly
    # ------------------------------------------------------------------

    def _make_result(self, raw_w: float, mode: str) -> ZGCResult:
        components = self._pid.components
        all_reliable = any(
            est.is_reliable for est in self._estimators.values()
        )
        learning_status = (
            "Calibrated \u2713" if all_reliable else "Learning..."
        )

        setpoints = dict(self._current_setpoints)
        array_clipping: dict[str, bool] = {}
        array_gain_k: dict[str, float | None] = {}
        array_calib: dict[str, str] = {}

        for array in self._arrays:
            pv_w = 0.0
            if array.pv_power_entity:
                pv_w = self._read_sensor_safe(array.pv_power_entity, 0.0)
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
            battery_clipping=self._detect_battery_clipping(),
            learning_status=learning_status,
            setpoints=setpoints,
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
