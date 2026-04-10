"""Main coordinator for Zero Grid Controller."""

from __future__ import annotations

import asyncio
import contextlib
import logging
import time
from collections import deque
from collections.abc import Callable
from dataclasses import dataclass, field
from datetime import UTC, datetime, timedelta
from typing import Any

from homeassistant.config_entries import ConfigEntry, ConfigSubentry
from homeassistant.core import HomeAssistant
from homeassistant.helpers.issue_registry import IssueSeverity
from homeassistant.helpers.update_coordinator import DataUpdateCoordinator, UpdateFailed

from .actuator_manager import ActuatorManager
from .array import ArrayConfig, array_config_from_subentry
from .battery import BatteryConfig, battery_config_from_subentry
from .calibrator import ArrayCalibrator
from .const import (
    ARRAY_CLIPPING_THRESHOLD,
    ARRAY_SUBENTRY_TYPE,
    BATTERY_RESPONSE_PERSIST_INTERVAL_S,
    BATTERY_SUBENTRY_TYPE,
    CALIB_BASELINE_SAMPLES,
    CALIB_GRID_VARIANCE_FACTOR,
    CALIB_INTER_ARRAY_SLEEP_S,
    CALIB_MIN_PV_W,
    CALIB_PV_SENSOR_MAX_WAIT_S,
    CALIB_SETTLING_CONFIRM_COUNT,
    CALIB_SETTLING_THRESHOLD_W,
    CALIB_STABLE_VARIANCE_PCT,
    CALIB_STABLE_WINDOW_S,
    CONF_ARRAY_CLIPPING_THRESHOLD,
    CONF_CALIB_BASELINE_SAMPLES,
    CONF_CALIB_GRID_VARIANCE_FACTOR,
    CONF_CALIB_INTER_ARRAY_SLEEP_S,
    CONF_CALIB_MAX_GRID_W,
    CONF_CALIB_MIN_PV_W,
    CONF_CALIB_PV_SENSOR_MAX_WAIT_S,
    CONF_CALIB_SETTLING_CONFIRM_COUNT,
    CONF_CALIB_SETTLING_THRESHOLD_W,
    CONF_CALIB_STABLE_VARIANCE_PCT,
    CONF_CALIB_STABLE_WINDOW_S,
    CONF_CONTROLLER_ENABLED,
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
    CONF_SENSOR_STALE_S,
    CONTROL_DT_MAX,
    CONTROL_DT_MIN,
    CONTROL_INTERVAL_S,
    DEFAULT_CALIB_MAX_GRID_W,
    DEFAULT_DEADBAND_W,
    DEFAULT_EWM_ALPHA,
    DEFAULT_KD,
    DEFAULT_KI,
    DEFAULT_KP,
    DEFAULT_OUTPUT_MAX_W,
    DEFAULT_RESPONSE_FACTOR,
    DEFAULT_SENSOR_STALE_S,
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
from .control_observer import ControlObserver
from .estimator import RLSEstimator
from .pid import PIDController
from .repairs import (
    ISSUE_ARRAY_CONFIGURATION_PROBLEM,
    ISSUE_BATTERY_UNRESPONSIVE,
    ISSUE_CALIBRATION_NOT_CONVERGING,
    ISSUE_GRID_SENSOR_STALE,
    ISSUE_GRID_SENSOR_UNAVAILABLE,
    ISSUE_MODE_GUARD_INVALID_STATE,
    ISSUE_SAFE_STATE_ACTIVE,
    create_issue,
    dismiss_issue,
    issue_id,
)
from .utils import clamp

_LOGGER = logging.getLogger(__name__)

PARALLEL_UPDATES = 0
CONTROL_CYCLE_LOG_MAXLEN = 180
EVENT_LOG_MAXLEN = 120


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
    status: str
    battery_clipping: bool
    learning_status: str
    residual_w: float = 0.0  # grid_w portion not covered by battery pre-compensation
    setpoints: dict[str, float] = field(default_factory=dict)
    battery_setpoints: dict[str, float] = field(default_factory=dict)
    array_clipping: dict[str, bool] = field(default_factory=dict)
    array_gain_k: dict[str, float | None] = field(default_factory=dict)
    array_calibration: dict[str, str] = field(default_factory=dict)
    battery_unresponsive: dict[str, bool] = field(default_factory=dict)


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
        self._observer = ControlObserver(self._read_sensor_safe)
        self._actuators = ActuatorManager(hass)

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
        self._sensor_stale_s: float = float(
            data.get(CONF_SENSOR_STALE_S, DEFAULT_SENSOR_STALE_S)
        )
        self._array_clipping_threshold: float = float(
            data.get(CONF_ARRAY_CLIPPING_THRESHOLD, ARRAY_CLIPPING_THRESHOLD)
        )
        self._calib_max_grid_w: float = float(
            data.get(CONF_CALIB_MAX_GRID_W, DEFAULT_CALIB_MAX_GRID_W)
        )
        self._calib_stable_variance_pct: float = float(
            data.get(CONF_CALIB_STABLE_VARIANCE_PCT, CALIB_STABLE_VARIANCE_PCT)
        )
        self._calib_stable_window_s: int = int(
            data.get(CONF_CALIB_STABLE_WINDOW_S, CALIB_STABLE_WINDOW_S)
        )
        self._calib_baseline_samples: int = int(
            data.get(CONF_CALIB_BASELINE_SAMPLES, CALIB_BASELINE_SAMPLES)
        )
        self._calib_settling_confirm_count: int = int(
            data.get(
                CONF_CALIB_SETTLING_CONFIRM_COUNT,
                CALIB_SETTLING_CONFIRM_COUNT,
            )
        )
        self._calib_settling_threshold_w: float = float(
            data.get(CONF_CALIB_SETTLING_THRESHOLD_W, CALIB_SETTLING_THRESHOLD_W)
        )
        self._calib_min_pv_w: float = float(
            data.get(CONF_CALIB_MIN_PV_W, CALIB_MIN_PV_W)
        )
        self._calib_grid_variance_factor: float = float(
            data.get(CONF_CALIB_GRID_VARIANCE_FACTOR, CALIB_GRID_VARIANCE_FACTOR)
        )
        self._calib_inter_array_sleep_s: float = float(
            data.get(CONF_CALIB_INTER_ARRAY_SLEEP_S, CALIB_INTER_ARRAY_SLEEP_S)
        )
        self._calib_pv_sensor_max_wait_s: float = float(
            data.get(CONF_CALIB_PV_SENSOR_MAX_WAIT_S, CALIB_PV_SENSOR_MAX_WAIT_S)
        )

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
        for array in self._arrays:
            array.array_clipping_threshold = self._array_clipping_threshold
        # Sort by max power (highest first) for intelligent priority
        self._arrays.sort(key=lambda a: a.max_power_w, reverse=True)

        # Runtime state — PV setpoints
        self._filtered_w: float = 0.0
        self._filtered_w_initialized: bool = False
        self._current_setpoints: dict[str, float] = {}
        self._settling_until: dict[str, float] = {}
        for array in self._arrays:
            self._current_setpoints.setdefault(array.name, array.setpoint_max)

        # Runtime state — battery setpoints (None = not yet commanded this session)
        self._current_battery_setpoints: dict[str, float | None] = {
            b.name: None for b in self._batteries
        }
        self._battery_settling_until: dict[str, float] = {}
        # Battery verification: {name: (verify_at_monotonic, commanded_w)}
        self._battery_verify_at: dict[str, tuple[float, float]] = {}
        self._last_battery_response_persist: float = 0.0
        self._last_persisted_battery_factors: dict[str, float] = {}

        # Restore measured_response_factor from persisted options
        saved_factors: dict[str, float] = data.get("battery_response_factors", {})
        for battery in self._batteries:
            if battery.name in saved_factors:
                battery.measured_response_factor = float(saved_factors[battery.name])

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
        self._controller_enabled: bool = bool(data.get(CONF_CONTROLLER_ENABLED, True))
        self._suppress_reload_once: bool = False
        self._last_estimator_persist: float = 0.0
        self._safe_state_applied: bool = False
        self._last_health_snapshot: dict[str, Any] = {}
        self._last_grid_input_health: list[dict[str, Any]] = []
        self._control_cycle_count: int = 0
        self._control_cycle_log: deque[dict[str, Any]]
        if not hasattr(self, "_control_cycle_log"):
            self._control_cycle_log = deque(maxlen=CONTROL_CYCLE_LOG_MAXLEN)
        self._sensor_health_log: deque[dict[str, Any]]
        if not hasattr(self, "_sensor_health_log"):
            self._sensor_health_log = deque(maxlen=EVENT_LOG_MAXLEN)
        self._battery_response_log: deque[dict[str, Any]]
        if not hasattr(self, "_battery_response_log"):
            self._battery_response_log = deque(maxlen=EVENT_LOG_MAXLEN)
        self._repair_event_log: deque[dict[str, Any]]
        if not hasattr(self, "_repair_event_log"):
            self._repair_event_log = deque(maxlen=EVENT_LOG_MAXLEN)
        self._calibration_log: deque[dict[str, Any]]
        if not hasattr(self, "_calibration_log"):
            self._calibration_log = deque(maxlen=EVENT_LOG_MAXLEN)
        if not hasattr(self, "_active_repair_issue_ids"):
            self._active_repair_issue_ids: set[str] = set()
        # Calibration task tracking — initialised once here, not reset by reload_config
        if not hasattr(self, "_calibration_task"):
            self._calibration_task: asyncio.Task[None] | None = None

    def reload_config(self) -> None:
        """Re-read config from the config entry (called after options update)."""
        # Preserve control state that should survive a config change
        old_filtered_w = self._filtered_w
        old_filtered_initialized = self._filtered_w_initialized
        old_integral = self._pid.integral
        old_setpoints = dict(self._current_setpoints)
        old_settling_until = dict(self._settling_until)
        old_battery_setpoints = dict(self._current_battery_setpoints)
        old_battery_settling = dict(self._battery_settling_until)

        self._init_from_entry(self._entry)

        # Restore EWM filter state — avoid transient ramp from 0 on reload
        self._filtered_w = old_filtered_w
        self._filtered_w_initialized = old_filtered_initialized
        # Restore PID integral — gains come from new config, but integral survives
        self._pid.set_integral(old_integral)
        # Restore setpoints and settling timers
        for name, sp in old_setpoints.items():
            if name in self._current_setpoints:
                self._current_setpoints[name] = sp
        for name, t in old_settling_until.items():
            if any(a.name == name for a in self._arrays):
                self._settling_until[name] = t
        for name, bsp in old_battery_setpoints.items():
            if name in self._current_battery_setpoints:
                self._current_battery_setpoints[name] = bsp
        for name, t in old_battery_settling.items():
            if any(b.name == name for b in self._batteries):
                self._battery_settling_until[name] = t

    def _now_iso(self) -> str:
        """Return the current UTC time as ISO string."""
        return datetime.now(UTC).isoformat()

    def _append_event(self, log: deque[dict[str, Any]], event: dict[str, Any]) -> None:
        """Append a JSON-safe event with a timestamp."""
        log.append({"timestamp": self._now_iso(), **event})

    def _log_sensor_health_event(
        self,
        entity_id: str,
        role: str,
        status: str,
        *,
        age_s: float | None = None,
    ) -> None:
        """Track sensor health transitions for diagnostics."""
        event: dict[str, Any] = {
            "entity_id": entity_id,
            "role": role,
            "status": status,
        }
        if age_s is not None:
            event["age_s"] = round(age_s, 1)
        self._append_event(self._sensor_health_log, event)

    def _log_battery_response_event(
        self,
        battery_name: str,
        *,
        commanded_w: float,
        actual_w: float,
        response_factor: float,
        state: str,
    ) -> None:
        """Track battery verification events for diagnostics."""
        self._append_event(
            self._battery_response_log,
            {
                "battery": battery_name,
                "commanded_w": round(commanded_w, 3),
                "actual_w": round(actual_w, 3),
                "response_factor": round(response_factor, 4),
                "state": state,
            },
        )

    def _log_repair_event(
        self,
        action: str,
        issue_key: str,
        full_issue_id: str,
        *,
        context: dict[str, Any] | None = None,
    ) -> None:
        """Track repair lifecycle events for diagnostics."""
        event: dict[str, Any] = {
            "action": action,
            "issue_key": issue_key,
            "issue_id": full_issue_id,
        }
        if context:
            event["context"] = context
        self._append_event(self._repair_event_log, event)

    def _log_calibration_event(
        self, array_name: str, status: str, **details: Any
    ) -> None:
        """Track calibration-related events for diagnostics."""
        self._append_event(
            self._calibration_log,
            {"array": array_name, "status": status, **details},
        )

    def _sync_repair_issue(
        self,
        *,
        active: bool,
        issue_key: str,
        suffix: str | None = None,
        placeholders: dict[str, str] | None = None,
        severity: IssueSeverity = IssueSeverity.ERROR,
        context: dict[str, Any] | None = None,
    ) -> None:
        """Create or dismiss a repair issue only on state transitions."""
        full_issue_id = issue_id(issue_key, suffix)
        if active:
            if full_issue_id in self._active_repair_issue_ids:
                return
            create_issue(
                self.hass,
                issue_key,
                suffix=suffix,
                placeholders=placeholders,
                severity=severity,
            )
            self._active_repair_issue_ids.add(full_issue_id)
            self._log_repair_event(
                "create", issue_key, full_issue_id, context=context or placeholders
            )
            return

        if full_issue_id not in self._active_repair_issue_ids:
            return
        dismiss_issue(self.hass, issue_key, suffix=suffix)
        self._active_repair_issue_ids.discard(full_issue_id)
        self._log_repair_event(
            "dismiss", issue_key, full_issue_id, context=context or placeholders
        )

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

    async def async_override_setpoint(
        self, array_name: str, value: float, duration_s: float = 300.0
    ) -> float:
        """Force a setpoint for an array, bypassing PID for up to duration_s seconds."""
        array = self.get_array(array_name)
        if array is None:
            raise ValueError(f"Unknown array: {array_name}")

        clamped = clamp(value, array.setpoint_min, array.setpoint_max)
        now = time.monotonic()
        await self._write_setpoint(array, clamped)
        self._current_setpoints[array.name] = clamped
        self._settling_until[array.name] = now + array.settling_time_s
        self._override_setpoints[array.name] = (clamped, now + duration_s)
        return clamped

    def reset_pid(self) -> None:
        """Reset the PID integrator and history."""
        self._pid.reset()

    def set_controller_enabled(self, enabled: bool) -> None:
        """Enable or disable the controller."""
        self._controller_enabled = enabled
        if enabled:
            self._safe_state_applied = False

    async def async_set_controller_enabled(self, enabled: bool) -> None:
        """Enable or disable the controller and persist that state."""
        self.set_controller_enabled(enabled)
        self._mark_internal_update()
        self.hass.config_entries.async_update_entry(
            self._entry,
            options={**self._entry.options, CONF_CONTROLLER_ENABLED: enabled},
        )

    def _mark_internal_update(self) -> None:
        """Suppress the next config entry reload triggered by an internal update."""
        self._suppress_reload_once = True

    def consume_internal_update(self) -> bool:
        """Return True once when the next update listener call should be suppressed."""
        if not self._suppress_reload_once:
            return False
        self._suppress_reload_once = False
        return True

    def get_array_subentry(self, array_name: str) -> ConfigSubentry | None:
        """Return the config subentry for an array name."""
        for subentry in self._entry.subentries.values():
            if subentry.subentry_type != ARRAY_SUBENTRY_TYPE:
                continue
            if subentry.data.get("array_name", subentry.subentry_id) == array_name:
                return subentry
        return None

    async def async_update_array_config(
        self, array_name: str, updates: dict[str, Any]
    ) -> None:
        """Persist and live-apply updates for a single array."""
        subentry = self.get_array_subentry(array_name)
        if subentry is None:
            raise ValueError(f"Unknown array: {array_name}")

        data = {**dict(subentry.data), **updates}
        title = str(data.get("array_name", subentry.title))
        self.apply_array_config_update(array_name, updates)
        self._mark_internal_update()
        self.hass.config_entries.async_update_subentry(
            self._entry,
            subentry,
            data=data,
            title=title,
        )

    async def async_enter_safe_state(self, *, force: bool = False) -> None:
        """Move all actuators to a neutral fail-safe state once."""
        if self._safe_state_applied and not force:
            return
        await self._actuators.enter_safe_state(
            arrays=self._arrays,
            batteries=self._batteries,
            current_setpoints=self._current_setpoints,
            current_battery_setpoints=self._current_battery_setpoints,
            write_setpoint=self._write_setpoint,
            write_numeric_entity=self._async_write_numeric_entity,
        )

        self._safe_state_applied = True

    async def _async_write_numeric_entity(self, entity_id: str, value: float) -> None:
        """Write a numeric target entity, supporting both number and input_number."""
        await self._actuators.write_numeric_entity(entity_id, value)

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

    def _evaluate_grid_inputs(self) -> list[dict[str, Any]]:
        """Return current health information for configured grid sensors."""
        health: list[dict[str, Any]] = []
        for role, entity_ids in (
            ("import", self._import_sensors),
            ("export", self._export_sensors),
        ):
            for entity_id in entity_ids:
                state = self.hass.states.get(entity_id)
                age_s: float | None = None
                if state is not None:
                    last_updated = getattr(state, "last_updated", None)
                    if last_updated is not None:
                        age_s = (datetime.now(UTC) - last_updated).total_seconds()
                available = state is not None and state.state not in (
                    "unknown",
                    "unavailable",
                )
                fresh = available and self._is_state_fresh(
                    entity_id, self._sensor_stale_s
                )
                is_numeric = False
                if available:
                    with contextlib.suppress(ValueError, TypeError):
                        float(state.state)
                        is_numeric = True
                if not available:
                    status = "unavailable"
                elif not fresh:
                    status = "stale"
                elif not is_numeric:
                    status = "non_numeric"
                else:
                    status = "ok"
                health.append(
                    {
                        "entity_id": entity_id,
                        "role": role,
                        "status": status,
                        "available": available,
                        "fresh": fresh,
                        "is_numeric": is_numeric,
                        "value": None if state is None else state.state,
                        "age_s": None if age_s is None else round(age_s, 1),
                    }
                )
        return health

    def _evaluate_mode_guard_health(self) -> dict[str, Any]:
        """Return mode-guard status information for diagnostics and repairs."""
        if not self._mode_guard_enabled:
            return {"enabled": False, "status": "disabled"}
        if not self._mode_guard_entity:
            return {
                "enabled": True,
                "status": "misconfigured",
                "mapped_mode": MODE_DISABLED,
            }

        state = self.hass.states.get(self._mode_guard_entity)
        if state is None:
            return {
                "enabled": True,
                "entity_id": self._mode_guard_entity,
                "status": "missing",
                "mapped_mode": MODE_DISABLED,
            }
        if state.state in ("unknown", "unavailable"):
            return {
                "enabled": True,
                "entity_id": self._mode_guard_entity,
                "status": "unavailable",
                "state": state.state,
                "mapped_mode": MODE_DISABLED,
            }
        if not self._is_state_fresh(self._mode_guard_entity, self._sensor_stale_s):
            return {
                "enabled": True,
                "entity_id": self._mode_guard_entity,
                "status": "stale",
                "state": state.state,
                "mapped_mode": MODE_DISABLED,
            }
        mapped = self._mode_guard_mapping.get(state.state)
        if mapped is None:
            return {
                "enabled": True,
                "entity_id": self._mode_guard_entity,
                "status": "unmapped",
                "state": state.state,
                "mapped_mode": MODE_ACTIVE,
            }
        return {
            "enabled": True,
            "entity_id": self._mode_guard_entity,
            "status": "ok",
            "state": state.state,
            "mapped_mode": mapped,
        }

    def _evaluate_array_configuration(self) -> list[dict[str, Any]]:
        """Return configuration problems that can be detected locally."""
        problems: list[dict[str, Any]] = []
        for array in self._arrays:
            if array.setpoint_min > array.setpoint_max:
                problems.append(
                    {
                        "array": array.name,
                        "problem": "setpoint_range_invalid",
                        "details": f"min {array.setpoint_min} > max {array.setpoint_max}",
                    }
                )
            if (
                array.output_type == OUTPUT_TYPE_SWITCH
                and array.switch_on_threshold_w <= 0
            ):
                problems.append(
                    {
                        "array": array.name,
                        "problem": "switch_threshold_invalid",
                        "details": "switch_on_threshold_w must be > 0",
                    }
                )
        for battery in self._batteries:
            if battery.control_enabled and not battery.setpoint_entity:
                problems.append(
                    {
                        "battery": battery.name,
                        "problem": "missing_setpoint_entity",
                        "details": "battery control is enabled without a setpoint entity",
                    }
                )
        return problems

    def _evaluate_batteries(self, now: float) -> list[dict[str, Any]]:
        """Return battery state and health details."""
        batteries: list[dict[str, Any]] = []
        for battery in self._batteries:
            actual_w = self._read_sensor_safe(battery.sensor_entity, 0.0)
            batteries.append(
                {
                    "name": battery.name,
                    "subentry_id": battery.subentry_id,
                    "control_enabled": battery.control_enabled,
                    "setpoint_entity": battery.setpoint_entity,
                    "sensor_entity": battery.sensor_entity,
                    "max_charge_w": battery.max_charge_w,
                    "max_discharge_w": battery.max_discharge_w,
                    "current_setpoint_w": self._current_battery_setpoints.get(
                        battery.name
                    ),
                    "current_power_w": actual_w,
                    "measured_response_factor": battery.measured_response_factor,
                    "unresponsive": battery.is_unresponsive(),
                    "unresponsive_count": getattr(battery, "_unresponsive_count", 0),
                    "settling_remaining_s": round(
                        max(
                            0.0,
                            self._battery_settling_until.get(battery.name, 0.0) - now,
                        ),
                        1,
                    ),
                    "verify_pending": battery.name in self._battery_verify_at,
                    "verification_min_w": battery.verification_min_w,
                    "unresponsive_threshold_w": battery.unresponsive_threshold_w,
                }
            )
        return batteries

    def _evaluate_calibration_health(self) -> dict[str, Any]:
        """Return estimator/calibration health summary."""
        non_switch_arrays = [
            a for a in self._arrays if a.output_type != OUTPUT_TYPE_SWITCH
        ]
        reliable = [
            array.name
            for array in non_switch_arrays
            if (est := self._estimators.get(array.name)) is not None and est.is_reliable
        ]
        not_reliable = [
            array.name for array in non_switch_arrays if array.name not in reliable
        ]
        issue_active = (
            bool(non_switch_arrays)
            and self._control_cycle_count >= 60
            and not self.is_calibrating
            and bool(not_reliable)
        )
        return {
            "is_calibrating": self.is_calibrating,
            "reliable_arrays": reliable,
            "unreliable_arrays": not_reliable,
            "issue_active": issue_active,
        }

    def _build_health_snapshot(self, result: ZGCResult, now: float) -> dict[str, Any]:
        """Assemble a health snapshot for diagnostics and repair evaluation."""
        grid_inputs = self._evaluate_grid_inputs()
        previous = {
            item["entity_id"]: item["status"] for item in self._last_grid_input_health
        }
        for item in grid_inputs:
            if previous.get(item["entity_id"]) != item["status"]:
                self._log_sensor_health_event(
                    item["entity_id"],
                    item["role"],
                    item["status"],
                    age_s=item["age_s"],
                )
        self._last_grid_input_health = grid_inputs

        batteries = self._evaluate_batteries(now)
        mode_guard = self._evaluate_mode_guard_health()
        calibration = self._evaluate_calibration_health()
        config_problems = self._evaluate_array_configuration()
        active_repairs = sorted(self._active_repair_issue_ids)
        return {
            "controller_enabled": self._controller_enabled,
            "safe_state_applied": self._safe_state_applied,
            "mode": result.mode,
            "status": result.status,
            "grid_inputs_ok": all(item["status"] == "ok" for item in grid_inputs),
            "grid_inputs": grid_inputs,
            "mode_guard": mode_guard,
            "batteries": batteries,
            "calibration": calibration,
            "config_problems": config_problems,
            "active_repairs": active_repairs,
        }

    def _update_repair_issues(self, health: dict[str, Any]) -> None:
        """Synchronize repair issues with the current health snapshot."""
        grid_inputs = health["grid_inputs"]
        unavailable = [
            item
            for item in grid_inputs
            if item["status"] in {"unavailable", "non_numeric"}
        ]
        stale = [item for item in grid_inputs if item["status"] == "stale"]
        self._sync_repair_issue(
            active=bool(unavailable),
            issue_key=ISSUE_GRID_SENSOR_UNAVAILABLE,
            placeholders={
                "sensors": ", ".join(item["entity_id"] for item in unavailable)
            },
        )
        self._sync_repair_issue(
            active=bool(stale),
            issue_key=ISSUE_GRID_SENSOR_STALE,
            placeholders={"sensors": ", ".join(item["entity_id"] for item in stale)},
            severity=IssueSeverity.WARNING,
        )

        mode_guard = health["mode_guard"]
        mode_guard_invalid = (
            mode_guard.get("enabled") and mode_guard.get("status") != "ok"
        )
        self._sync_repair_issue(
            active=bool(mode_guard_invalid),
            issue_key=ISSUE_MODE_GUARD_INVALID_STATE,
            placeholders={
                "entity": str(mode_guard.get("entity_id") or "not_configured"),
                "state": str(mode_guard.get("state") or mode_guard.get("status")),
            },
            severity=IssueSeverity.WARNING,
        )

        self._sync_repair_issue(
            active=health["safe_state_applied"],
            issue_key=ISSUE_SAFE_STATE_ACTIVE,
            placeholders={"status": str(health["status"])},
            severity=IssueSeverity.WARNING,
        )

        for battery in self._batteries:
            self._sync_repair_issue(
                active=battery.is_unresponsive(),
                issue_key=ISSUE_BATTERY_UNRESPONSIVE,
                suffix=battery.name,
                placeholders={"battery": battery.name},
                severity=IssueSeverity.WARNING,
            )

        problems = health["config_problems"]
        for battery in self._batteries:
            active = any(problem.get("battery") == battery.name for problem in problems)
            self._sync_repair_issue(
                active=active,
                issue_key=ISSUE_ARRAY_CONFIGURATION_PROBLEM,
                suffix=f"battery_{battery.name}",
                placeholders={"target": battery.name},
                severity=IssueSeverity.WARNING,
            )
        for array in self._arrays:
            active = any(problem.get("array") == array.name for problem in problems)
            self._sync_repair_issue(
                active=active,
                issue_key=ISSUE_ARRAY_CONFIGURATION_PROBLEM,
                suffix=f"array_{array.name}",
                placeholders={"target": array.name},
                severity=IssueSeverity.WARNING,
            )

        calibration = health["calibration"]
        self._sync_repair_issue(
            active=calibration["issue_active"],
            issue_key=ISSUE_CALIBRATION_NOT_CONVERGING,
            placeholders={
                "arrays": ", ".join(calibration["unreliable_arrays"]) or "none",
            },
            severity=IssueSeverity.WARNING,
        )

    def _log_control_cycle(self, result: ZGCResult) -> None:
        """Store a compact per-cycle control log for diagnostics."""
        self._control_cycle_count += 1
        self._append_event(
            self._control_cycle_log,
            {
                "cycle": self._control_cycle_count,
                "mode": result.mode,
                "status": result.status,
                "grid_raw_w": round(result.grid_raw_w, 3),
                "grid_filtered_w": round(result.grid_filtered_w, 3),
                "residual_w": round(result.residual_w, 3),
                "pid_output_w": round(result.pid_output_w, 3),
                "pid_p_w": round(result.pid_p_w, 3),
                "pid_i_w": round(result.pid_i_w, 3),
                "pid_d_w": round(result.pid_d_w, 3),
                "battery_clipping": result.battery_clipping,
                "safe_state_applied": self._safe_state_applied,
                "setpoints": dict(result.setpoints),
                "battery_setpoints": dict(result.battery_setpoints),
                "battery_unresponsive": dict(result.battery_unresponsive),
            },
        )

    def _finalize_result(self, result: ZGCResult, now: float) -> ZGCResult:
        """Finalize diagnostics bookkeeping before returning coordinator data."""
        self._log_control_cycle(result)
        health = self._build_health_snapshot(result, now)
        self._update_repair_issues(health)
        health["active_repairs"] = sorted(self._active_repair_issue_ids)
        self._last_health_snapshot = health
        return result

    async def _run_control_loop(self, dt: float, now: float) -> ZGCResult:
        # --- 0. Battery response verification (runs every cycle regardless of mode) ---
        battery_unresponsive = self._verify_battery_responses(now)
        self._persist_battery_response_factors(now)

        # --- Check if controller is enabled ---
        if not self._controller_enabled:
            _LOGGER.debug("Controller is disabled, skipping control loop")
            self._pid.reset()
            await self.async_enter_safe_state()
            return self._finalize_result(
                self._make_result(
                    0.0,
                    MODE_DISABLED,
                    STATUS_DISABLED,
                    battery_unresponsive=battery_unresponsive,
                ),
                now,
            )

        # --- Grid input validation ---
        if self._is_grid_sensor_unavailable():
            self._pid.reset()
            await self.async_enter_safe_state()
            return self._finalize_result(
                self._make_result(
                    0.0,
                    MODE_DISABLED,
                    STATUS_DISABLED,
                    battery_unresponsive=battery_unresponsive,
                ),
                now,
            )

        # --- 1. Read & normalise grid measurement ---
        raw_w = self._read_grid()

        # --- 2. EWM low-pass filter ---
        if not self._filtered_w_initialized:
            # Seed the filter with the first real reading to avoid a ramp from 0
            self._filtered_w = raw_w
            self._filtered_w_initialized = True
        else:
            self._filtered_w = (
                self._ewm_alpha * raw_w + (1.0 - self._ewm_alpha) * self._filtered_w
            )

        # --- 3. Mode guard ---
        mode = self._resolve_mode()
        if mode == MODE_DISABLED:
            self._pid.reset()
            await self.async_enter_safe_state()
            return self._finalize_result(
                self._make_result(
                    raw_w,
                    MODE_DISABLED,
                    STATUS_DISABLED,
                    battery_unresponsive=battery_unresponsive,
                ),
                now,
            )

        self._safe_state_applied = False

        # --- 4. Battery layer — pre-compensate grid error ---
        battery_targets = self._compute_battery_cmd(self._filtered_w, mode)
        battery_setpoints = await self._apply_battery_targets(battery_targets, now)

        # --- 5. Residual: portion of grid error not yet covered by battery ---
        # Use the last-written setpoints (not freshly computed targets) so that
        # the residual is accurate while a battery write is still settling.
        battery_cmd_w = sum(
            (self._current_battery_setpoints.get(b.name) or 0.0)
            for b in self._batteries
            if b.control_enabled and b.setpoint_entity
        )
        residual_w = self._filtered_w - battery_cmd_w

        # --- 6. Deadband on residual (no PV action needed) ---
        if abs(residual_w) < self._deadband_w:
            self._pid.freeze_integrator()
            return self._finalize_result(
                self._make_result(
                    raw_w,
                    mode,
                    STATUS_DEADBAND,
                    battery_setpoints=battery_setpoints,
                    residual_w=residual_w,
                    battery_unresponsive=battery_unresponsive,
                ),
                now,
            )

        # --- 7. Clipping & cloud-shadow detection ---
        observation = self._observer.assess(
            residual_w=residual_w,
            now=now,
            arrays=self._arrays,
            batteries=self._batteries,
            current_setpoints=self._current_setpoints,
            settling_until=self._settling_until,
        )
        if observation.cloud_shadow:
            self._pid.freeze_integrator()
            return self._finalize_result(
                self._make_result(
                    raw_w,
                    mode,
                    STATUS_CLOUD_SHADOW,
                    battery_setpoints=battery_setpoints,
                    residual_w=residual_w,
                    battery_unresponsive=battery_unresponsive,
                ),
                now,
            )

        # --- 8. Freeze integrator until the longest-settling PV correction lands ---
        if observation.any_array_settling:
            self._pid.freeze_integrator()

        # --- 9. PID on residual error ---
        delta_w = self._pid.compute(residual_w, dt)

        # --- 10. Passive mode: only curtail PV, never open limits ---
        if mode == MODE_PASSIVE and delta_w < 0:
            delta_w = 0.0

        # --- 11. Distribute and write PV setpoints (inhibited during calibration) ---
        if self.is_calibrating:
            # Calibration owns the setpoint writes — freeze integrator and skip PV writes
            self._pid.freeze_integrator()
            written: dict[str, float] = {}
        else:
            written = await self._distribute_and_write(delta_w, now, mode)

        # --- 12. Update RLS estimators (one-cycle delay) ---
        self._update_estimators(written, now)

        # --- 13. Persist estimator states ---
        self._persist_estimators()

        if observation.saturation:
            status = STATUS_SATURATION
        elif mode == MODE_PASSIVE:
            status = STATUS_PASSIVE
        else:
            status = STATUS_ACTIVE

        return self._finalize_result(
            self._make_result(
                raw_w,
                mode,
                status,
                observation.battery_clipping,
                battery_setpoints,
                residual_w,
                battery_unresponsive=battery_unresponsive,
            ),
            now,
        )

    # ------------------------------------------------------------------
    # Grid reading
    # ------------------------------------------------------------------

    def _is_numeric_state_available(self, entity_id: str) -> bool:
        """Return True if a sensor exists and currently has a numeric state."""
        state = self.hass.states.get(entity_id)
        if (
            state is None
            or state.state in ("unknown", "unavailable")
            or not self._is_state_fresh(entity_id, self._sensor_stale_s)
        ):
            return False
        try:
            float(state.state)
        except ValueError:
            return False
        return True

    def _is_grid_sensor_unavailable(self) -> bool:
        """Return True if any configured grid sensor is unavailable or invalid."""
        configured = [*self._import_sensors, *self._export_sensors]
        return any(not self._is_numeric_state_available(eid) for eid in configured)

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
        """Public method for external grid reading (e.g. calibrator)."""
        if self._is_grid_sensor_unavailable():
            return None
        return self._read_grid()

    def _read_sensor_safe(self, entity_id: str, default: float) -> float:
        """Read a numeric sensor state, returning default on error."""
        if not entity_id:
            return default
        state = self.hass.states.get(entity_id)
        if (
            state is None
            or state.state in ("unknown", "unavailable")
            or not self._is_state_fresh(entity_id, self._sensor_stale_s)
        ):
            return default
        try:
            return float(state.state)
        except ValueError:
            return default

    def _is_state_fresh(self, entity_id: str, max_age_s: float) -> bool:
        """Return True if the entity has updated recently enough to trust."""
        state = self.hass.states.get(entity_id)
        if state is None:
            return False
        last_updated = getattr(state, "last_updated", None)
        if last_updated is None:
            return False
        age_s = (datetime.now(UTC) - last_updated).total_seconds()
        return bool(age_s <= max_age_s)

    # ------------------------------------------------------------------
    # Mode guard
    # ------------------------------------------------------------------

    def _resolve_mode(self) -> str:
        """Determine the operating mode from the mode guard entity (if configured)."""
        if not self._mode_guard_enabled or not self._mode_guard_entity:
            return MODE_ACTIVE

        state = self.hass.states.get(self._mode_guard_entity)
        if (
            state is None
            or state.state in ("unknown", "unavailable")
            or not self._is_state_fresh(self._mode_guard_entity, self._sensor_stale_s)
        ):
            return MODE_DISABLED

        mapped = self._mode_guard_mapping.get(state.state, MODE_ACTIVE)
        if mapped in (MODE_ACTIVE, MODE_PASSIVE, MODE_DISABLED):
            return mapped
        return MODE_ACTIVE

    # ------------------------------------------------------------------
    # Battery layer — pre-compensation
    # ------------------------------------------------------------------

    def _pv_has_curtailment(self) -> bool:
        """Return True if any enabled PV array is curtailed (below its max setpoint).

        Used to implement PV-first priority: the battery should not discharge to
        cover import while PV arrays still have headroom to open their limits.
        Uncurtailed PV arrays (at setpoint_max) cannot produce more, so the
        battery is then allowed to cover remaining import.
        """
        for array in self._arrays:
            if not array.enabled:
                continue
            current_sp = self._current_setpoints.get(array.name, array.setpoint_max)
            if current_sp < array.setpoint_max:
                return True
        return False

    def _compute_battery_cmd(self, filtered_w: float, mode: str) -> dict[str, float]:
        """Compute proportional battery commands to pre-compensate the grid error.

        Priority:
          Export (filtered_w < 0): battery always charges to absorb excess solar.
            The PID then curtails PV only if the battery cannot absorb everything.
          Import (filtered_w > 0): battery discharges ONLY when all PV arrays are
            already at their maximum setpoint (no curtailment left to undo).
            As long as any array is curtailed, the PID opens PV limits first.

        positive cmd = discharge (battery injects power, reduces import)
        negative cmd = charge   (battery absorbs power, reduces export)

        In passive mode batteries may still charge (absorb excess), but will not
        discharge (that would be "opening" energy delivery, contradicting passive).

        Returns {battery_name: cmd_w}.
        """
        controlled = [
            b for b in self._batteries if b.control_enabled and b.setpoint_entity
        ]
        if not controlled:
            return {}

        targets: dict[str, float] = {}

        if filtered_w > 0:
            # Importing — consider discharging battery to reduce import
            if mode == MODE_PASSIVE:
                # Passive mode: do not discharge (would substitute for PV opening)
                return {}
            # PV-first: if any array is still curtailed, let the PID open it first
            if self._pv_has_curtailment():
                return {}
            # All PV arrays are at maximum — discharge battery to cover remaining import
            total_capacity = sum(
                b.max_discharge_w * b.measured_response_factor for b in controlled
            )
            if total_capacity <= 0:
                return {}
            for b in controlled:
                weight = b.max_discharge_w * b.measured_response_factor
                targets[b.name] = clamp(
                    filtered_w * weight / total_capacity, 0.0, b.max_discharge_w
                )
        else:
            # Exporting — charge batteries to absorb excess solar
            total_capacity = sum(
                b.max_charge_w * b.measured_response_factor for b in controlled
            )
            if total_capacity <= 0:
                return {}
            for b in controlled:
                weight = b.max_charge_w * b.measured_response_factor
                targets[b.name] = clamp(
                    filtered_w * weight / total_capacity, -b.max_charge_w, 0.0
                )

        return targets

    async def _apply_battery_targets(
        self, targets: dict[str, float], now: float
    ) -> dict[str, float]:
        """Write battery setpoints, respecting settling time and change threshold.

        Skips writes while the battery is still settling from the previous command.
        Skips writes below BATTERY_WRITE_THRESHOLD_W to avoid micro-jitter.
        Returns the current battery setpoints dict for inclusion in ZGCResult.
        """
        return await self._actuators.apply_battery_targets(
            targets=targets,
            now=now,
            batteries=self._batteries,
            current_battery_setpoints=self._current_battery_setpoints,
            battery_settling_until=self._battery_settling_until,
            battery_verify_at=self._battery_verify_at,
            write_numeric_entity=self._async_write_numeric_entity,
        )

    # ------------------------------------------------------------------
    # Battery clipping detection
    # ------------------------------------------------------------------

    def _detect_battery_clipping(self) -> bool:
        """True if any battery is at maximum charge power (absorbing all it can)."""
        return self._observer.detect_battery_clipping(self._batteries)

    # ------------------------------------------------------------------
    # Battery response verification
    # ------------------------------------------------------------------

    def _verify_battery_responses(self, now: float) -> dict[str, bool]:
        """Check if batteries delivered what was commanded after settling.

        For each pending verification that is due, read the battery sensor and
        call battery.update_response().  Logs once on transition to/from
        unresponsive.  Returns {battery_name: is_unresponsive}.
        """
        result: dict[str, bool] = {}
        due = [name for name, (at, _) in self._battery_verify_at.items() if now >= at]

        for name in due:
            _, commanded_w = self._battery_verify_at.pop(name)
            battery = next((b for b in self._batteries if b.name == name), None)
            if battery is None:
                continue

            if abs(commanded_w) < battery.verification_min_w:
                result[name] = battery.is_unresponsive()
                self._log_battery_response_event(
                    name,
                    commanded_w=commanded_w,
                    actual_w=0.0,
                    response_factor=battery.measured_response_factor,
                    state="skipped_small_command",
                )
                continue

            actual_w = self._read_sensor_safe(battery.sensor_entity, 0.0)
            was_unresponsive = battery.is_unresponsive()
            battery.update_response(actual_w, commanded_w)
            now_unresponsive = battery.is_unresponsive()

            if not was_unresponsive and now_unresponsive:
                _LOGGER.warning(
                    "Battery %s is not responding: commanded %.0f W, actual %.0f W",
                    name,
                    commanded_w,
                    actual_w,
                )
            elif was_unresponsive and not now_unresponsive:
                _LOGGER.info(
                    "Battery %s recovered: commanded %.0f W, actual %.0f W",
                    name,
                    commanded_w,
                    actual_w,
                )

            _LOGGER.debug(
                "Battery %s verified: commanded=%.0f W actual=%.0f W response_factor=%.2f",
                name,
                commanded_w,
                actual_w,
                battery.measured_response_factor,
            )
            response_state = "unresponsive" if now_unresponsive else "ok"
            if was_unresponsive and not now_unresponsive:
                response_state = "recovered"
            elif not was_unresponsive and now_unresponsive:
                response_state = "became_unresponsive"
            self._log_battery_response_event(
                name,
                commanded_w=commanded_w,
                actual_w=actual_w,
                response_factor=battery.measured_response_factor,
                state=response_state,
            )
            result[name] = now_unresponsive

        # Include batteries with no pending verification
        for battery in self._batteries:
            if battery.name not in result:
                result[battery.name] = battery.is_unresponsive()

        return result

    def _persist_battery_response_factors(self, now: float) -> None:
        """Persist measured_response_factor for all batteries (throttled to 60 s)."""
        if (
            now - self._last_battery_response_persist
            < BATTERY_RESPONSE_PERSIST_INTERVAL_S
        ):
            return
        factors = {b.name: b.measured_response_factor for b in self._batteries}
        if factors == self._last_persisted_battery_factors:
            return
        self._last_battery_response_persist = now
        self._last_persisted_battery_factors = dict(factors)
        self._mark_internal_update()
        self.hass.config_entries.async_update_entry(
            self._entry,
            options={**self._entry.options, "battery_response_factors": factors},
        )

    # ------------------------------------------------------------------
    # Setpoint distribution — PV arrays
    # ------------------------------------------------------------------

    async def _distribute_and_write(
        self, delta_w: float, now: float, mode: str = MODE_ACTIVE
    ) -> dict[str, float]:
        """Distribute delta_w across active PV arrays proportional to headroom.

        Returns a dict of {array_name: actual_delta_w_written}.
        """
        return await self._actuators.distribute_and_write(
            delta_w=delta_w,
            now=now,
            mode=mode,
            filtered_w=self._filtered_w,
            arrays=self._arrays,
            current_setpoints=self._current_setpoints,
            settling_until=self._settling_until,
            override_setpoints=self._override_setpoints,
            write_setpoint=self._write_setpoint,
            clamp_func=clamp,
        )

    async def _apply_switch_hysteresis(
        self, now: float, mode: str = MODE_ACTIVE
    ) -> dict[str, float]:
        """Apply on/off hysteresis for switch-based arrays."""
        return await self._actuators.apply_switch_hysteresis(
            now=now,
            mode=mode,
            filtered_w=self._filtered_w,
            arrays=self._arrays,
            current_setpoints=self._current_setpoints,
            settling_until=self._settling_until,
            override_setpoints=self._override_setpoints,
            write_setpoint=self._write_setpoint,
        )

    async def _write_setpoint(self, array: ArrayConfig, value: float) -> None:
        """Write a setpoint to the inverter entity."""
        await self._actuators.write_setpoint(array, value)

    # ------------------------------------------------------------------
    # RLS estimator
    # ------------------------------------------------------------------

    def _update_estimators(self, written: dict[str, float], now: float) -> None:
        """Two-step measurement: record delta, then observe response after settling."""
        for name, delta_w in written.items():
            self._pending_estimates[name] = (delta_w, self._filtered_w, now)

        for name, pending in list(self._pending_estimates.items()):
            prev_delta_sp_w, prev_grid_w, step_time = pending
            if name in written:
                continue
            array = self.get_array(name)
            settling = array.settling_time_s if array else DEFAULT_SETTLING_TIME_S
            if now - step_time < settling:
                continue
            delta_grid = self._filtered_w - prev_grid_w
            if abs(prev_delta_sp_w) > 1:
                estimator = self._estimators.get(name)
                if estimator:
                    estimator.update(prev_delta_sp_w, delta_grid)
                    if estimator.is_reliable and not self._expert_mode:
                        old_kp = self._pid.kp
                        new_kp = estimator.suggest_kp(old_kp, DEFAULT_RESPONSE_FACTOR)
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
        if self._entry.options.get(CONF_ESTIMATOR_STATE) == state_dict:
            return
        now = time.monotonic()
        if now - self._last_estimator_persist < 60.0:
            return
        self._last_estimator_persist = now
        self._mark_internal_update()
        self.hass.config_entries.async_update_entry(
            self._entry,
            options={**self._entry.options, CONF_ESTIMATOR_STATE: state_dict},
        )

    # ------------------------------------------------------------------
    # Result assembly
    # ------------------------------------------------------------------

    def _make_result(
        self,
        raw_w: float,
        mode: str,
        status: str,
        battery_clipping: bool = False,
        battery_setpoints: dict[str, float] | None = None,
        residual_w: float = 0.0,
        battery_unresponsive: dict[str, bool] | None = None,
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
            status=status,
            battery_clipping=battery_clipping,
            learning_status=learning_status,
            residual_w=residual_w,
            setpoints=setpoints,
            battery_setpoints=battery_setpoints or {},
            array_clipping=array_clipping,
            array_gain_k=array_gain_k,
            array_calibration=array_calib,
            battery_unresponsive=battery_unresponsive or {},
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

    @property
    def override_setpoints(self) -> dict[str, tuple[float, float]]:
        """Return active override setpoints keyed by array name."""
        return dict(self._override_setpoints)

    @property
    def settling_until(self) -> dict[str, float]:
        """Return settling end timestamps keyed by array name."""
        return dict(self._settling_until)

    @property
    def safe_state_applied(self) -> bool:
        """Return whether the fail-safe actuator state is currently applied."""
        return self._safe_state_applied

    @property
    def health_snapshot(self) -> dict[str, Any]:
        """Return the latest aggregated health snapshot."""
        return dict(self._last_health_snapshot)

    @property
    def control_cycle_log(self) -> list[dict[str, Any]]:
        """Return the recent per-cycle diagnostics log."""
        return list(self._control_cycle_log)

    @property
    def sensor_health_log(self) -> list[dict[str, Any]]:
        """Return recent sensor health transitions."""
        return list(self._sensor_health_log)

    @property
    def battery_response_log(self) -> list[dict[str, Any]]:
        """Return recent battery verification events."""
        return list(self._battery_response_log)

    @property
    def repair_event_log(self) -> list[dict[str, Any]]:
        """Return recent repair issue create/dismiss events."""
        return list(self._repair_event_log)

    @property
    def calibration_log(self) -> list[dict[str, Any]]:
        """Return recent calibration events."""
        return list(self._calibration_log)

    @property
    def is_calibrating(self) -> bool:
        """Return True while a calibration task is running."""
        return self._calibration_task is not None and not self._calibration_task.done()

    def async_start_calibration(
        self,
        arrays: list[ArrayConfig],
        progress_callback: Callable[[str, float], None],
    ) -> bool:
        """Create and track a calibration task.

        Returns False (and logs a warning) if calibration is already running.
        The task is stored so it can be cancelled on integration unload.
        """
        if self.is_calibrating:
            _LOGGER.warning("Calibration already in progress — ignoring new request")
            return False
        for array in arrays:
            self._log_calibration_event(array.name, "started")
        self._calibration_task = self.hass.async_create_task(
            self._async_calibration_wrapper(arrays, progress_callback)
        )
        return True

    async def _async_calibration_wrapper(
        self,
        arrays: list[ArrayConfig],
        progress_callback: Callable[[str, float], None],
    ) -> None:
        """Run calibration and apply results; handles cancellation and errors."""
        calibrator = ArrayCalibrator()
        try:
            results = await calibrator.run(
                self.hass,
                arrays,
                self.read_grid_w,
                self._calibration_write_setpoint,
                progress_callback,
                calib_max_grid_w=self._calib_max_grid_w,
                calib_stable_variance_pct=self._calib_stable_variance_pct,
                calib_stable_window_s=self._calib_stable_window_s,
                calib_baseline_samples=self._calib_baseline_samples,
                calib_settling_confirm_count=self._calib_settling_confirm_count,
                calib_settling_threshold_w=self._calib_settling_threshold_w,
                calib_min_pv_w=self._calib_min_pv_w,
                calib_grid_variance_factor=self._calib_grid_variance_factor,
                calib_inter_array_sleep_s=self._calib_inter_array_sleep_s,
                calib_pv_sensor_max_wait_s=self._calib_pv_sensor_max_wait_s,
            )
            await self.async_apply_calibration_results(results)
            for array_name in results:
                self._log_calibration_event(array_name, "applied")
        except asyncio.CancelledError:
            _LOGGER.info("Calibration cancelled (integration unloading or new request)")
            calibrator.abort()
            for array in arrays:
                self._log_calibration_event(array.name, "cancelled")
            raise
        except Exception as err:
            _LOGGER.error("Calibration failed with unexpected error: %s", err)
            for array in arrays:
                self._log_calibration_event(array.name, "failed", error=str(err))

    async def _calibration_write_setpoint(
        self, array: ArrayConfig, value: float
    ) -> None:
        """Write a setpoint during calibration, keeping coordinator state in sync.

        Updating _current_setpoints and _settling_until prevents the PID from
        computing stale deltas immediately after calibration restores the setpoint.
        """
        await self._actuators.write_setpoint(array, value)
        self._current_setpoints[array.name] = value
        self._settling_until[array.name] = time.monotonic() + array.settling_time_s

    async def async_run_calibration(
        self,
        arrays: list[ArrayConfig],
        progress_callback: Callable[[str, float], None],
    ) -> dict[str, Any]:
        """Run calibration directly (awaitable form used by tests).

        Production code should use async_start_calibration() instead.
        """
        calibrator = ArrayCalibrator()
        results = await calibrator.run(
            self.hass,
            arrays,
            self.read_grid_w,
            self._calibration_write_setpoint,
            progress_callback,
            calib_max_grid_w=self._calib_max_grid_w,
            calib_stable_variance_pct=self._calib_stable_variance_pct,
            calib_stable_window_s=self._calib_stable_window_s,
            calib_baseline_samples=self._calib_baseline_samples,
            calib_settling_confirm_count=self._calib_settling_confirm_count,
            calib_settling_threshold_w=self._calib_settling_threshold_w,
            calib_min_pv_w=self._calib_min_pv_w,
            calib_grid_variance_factor=self._calib_grid_variance_factor,
            calib_inter_array_sleep_s=self._calib_inter_array_sleep_s,
            calib_pv_sensor_max_wait_s=self._calib_pv_sensor_max_wait_s,
        )
        await self.async_apply_calibration_results(results)
        return results

    async def async_shutdown(self) -> None:
        """Cancel any running calibration task (called on integration unload)."""
        if self._calibration_task is not None and not self._calibration_task.done():
            self._calibration_task.cancel()
            with contextlib.suppress(asyncio.CancelledError):
                await self._calibration_task
        self._calibration_task = None

    async def async_apply_calibration_results(self, results: dict[str, Any]) -> None:
        """Persist and live-apply calibration results for matching arrays."""
        for array_name, result in results.items():
            array = self.get_array(array_name)
            if array is None:
                continue

            updates: dict[str, Any] = {
                "calibration_confidence": result.confidence,
            }
            self._log_calibration_event(
                array_name,
                "result",
                confidence=result.confidence,
            )
            if array.output_type != OUTPUT_TYPE_SWITCH:
                updates.update(
                    {
                        "w_per_unit": result.w_per_unit,
                        "settling_time_s": result.settling_time_s,
                    }
                )
            await self.async_update_array_config(array_name, updates)
