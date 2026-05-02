"""Main coordinator for Zero Grid Controller."""

from __future__ import annotations

import logging
from datetime import timedelta
from typing import TYPE_CHECKING

from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant

if TYPE_CHECKING:
    from .sensor import ZGCCalibrationProgressSensor
from homeassistant.helpers.update_coordinator import DataUpdateCoordinator, UpdateFailed

from .actuator_manager import ActuatorManager
from .array import array_config_from_subentry
from .battery import battery_config_from_subentry
from .calibrator import ArrayCalibrator, CalibrationResult
from .const import (
    AGGRESSIVENESS_FACTORS,
    AGGRESSIVENESS_KI_RATIO,
    ARRAY_SUBENTRY_TYPE,
    BATTERY_SUBENTRY_TYPE,
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
    CONTROL_INTERVAL_S,
    DEFAULT_AGGRESSIVENESS,
    DEFAULT_DEADBAND_W,
    DEFAULT_EWM_ALPHA,
    DEFAULT_KD,
    DEFAULT_KI,
    DEFAULT_KP,
    DEFAULT_OUTPUT_MAX_W,
    DOMAIN,
    LOAD_SUBENTRY_TYPE,
)
from .control_engine import ControlCycleResult, ControlEngine
from .load import load_config_from_subentry
from .pid import PIDController
from .repairs import dismiss_grid_sensor_unavailable, raise_grid_sensor_unavailable

_LOGGER = logging.getLogger(__name__)

PARALLEL_UPDATES = 0

# Backward-compatible alias: sensor.py and tests import ZGCResult from coordinator.
ZGCResult = ControlCycleResult


class ZeroGridCoordinator(DataUpdateCoordinator[ZGCResult]):
    """Central coordinator: reads grid, runs PID, distributes setpoints.

    This class is responsible for HA integration concerns only:
    - Config entry lifecycle (init, reload, persist calibration)
    - Grid sensor reading and HA repair notifications
    - Service and calibration orchestration

    All stateful control logic lives in ControlEngine.
    """

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
        self._actuators = ActuatorManager(hass)
        self._calibrator: ArrayCalibrator | None = None
        self._grid_sensor_unavailable: bool = False
        # Set by sensor.py after entity registration
        self.calibration_progress_sensor: ZGCCalibrationProgressSensor | None = None

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

        new_pid = PIDController(
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

        self.arrays = []
        self.batteries = []
        self.loads = []

        for subentry in entry.subentries.values():
            if subentry.subentry_type == ARRAY_SUBENTRY_TYPE:
                self.arrays.append(
                    array_config_from_subentry(subentry.subentry_id, subentry.data)
                )
            elif subentry.subentry_type == BATTERY_SUBENTRY_TYPE:
                self.batteries.append(
                    battery_config_from_subentry(subentry.subentry_id, subentry.data)
                )
            elif subentry.subentry_type == LOAD_SUBENTRY_TYPE:
                self.loads.append(
                    load_config_from_subentry(subentry.subentry_id, subentry.data)
                )

        if not hasattr(self, "_engine"):
            # First initialization — create the engine
            self._engine: ControlEngine = ControlEngine(
                hass=self.hass,
                pid=new_pid,
                actuators=self._actuators,
                ewm_alpha=self._ewm_alpha,
                deadband_w=self._deadband_w,
            )
        else:
            # Reload — update params, preserve all setpoint and filter state
            self._engine.update_params(new_pid, self._ewm_alpha, self._deadband_w)

    # ------------------------------------------------------------------
    # Public helpers
    # ------------------------------------------------------------------

    def reload_config(self) -> None:
        """Reload config entry data, preserving filter and PID integral state."""
        old_filtered = self._engine.filtered_w
        old_integral = self._engine.pid.integral

        self._init_from_entry(self._entry)

        # Restore continuity across the reload
        self._engine.restore_filter_state(old_filtered, old_integral)

        # Prune setpoint dicts so removed subentries don't leave stale state
        self._engine.prune_stale_keys(
            active_array_names={a.name for a in self.arrays},
            active_battery_names={b.name for b in self.batteries},
            active_load_names={ld.name for ld in self.loads},
        )

    async def start_calibration(self) -> list[CalibrationResult]:
        """Run calibration on all numeric arrays and persist results."""
        if self._calibrator is not None:
            _LOGGER.warning("Calibration already running")
            return []

        _sensor = self.calibration_progress_sensor
        if _sensor is not None:
            _sensor.set_status("running")

        self._calibrator = ArrayCalibrator(
            hass=self.hass,
            arrays=self.arrays,
            current_setpoints=self._engine.current_setpoints,
            aggressiveness=self._aggressiveness,
            write_setpoint=self._actuators.write_setpoint,
            sensor_reader=self,
            on_array_done=self._on_calibration_array_done,
        )
        try:
            results = await self._calibrator.run()
        except Exception:
            if _sensor is not None:
                _sensor.set_status("failed")
            raise
        finally:
            self._calibrator = None

        all_ok = all(r.success for r in results)
        if _sensor is not None:
            _sensor.set_status("done" if all_ok else "failed")

        await self._persist_calibration_results(results)
        return results

    def _on_calibration_array_done(self, completed: int, total: int) -> None:
        """Called by calibrator after each array completes."""
        _LOGGER.info("Calibration: %d / %d arrays done", completed, total)

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
            grid_raw = await self._read_grid()
            if grid_raw is None:
                if not self._grid_sensor_unavailable:
                    self._grid_sensor_unavailable = True
                    raise_grid_sensor_unavailable(self.hass)
                _LOGGER.warning("Grid sensor(s) unavailable, entering safe state")
            elif self._grid_sensor_unavailable:
                self._grid_sensor_unavailable = False
                dismiss_grid_sensor_unavailable(self.hass)

            return await self._engine.run_cycle(
                grid_raw=grid_raw,
                arrays=self.arrays,
                batteries=self.batteries,
                loads=self.loads,
                enabled=self._enabled,
                calibrating=self._calibrator is not None,
            )
        except Exception as err:
            raise UpdateFailed(
                translation_domain=DOMAIN,
                translation_key="coordinator_update_failed",
                translation_placeholders={"error": str(err)},
            ) from err

    # ------------------------------------------------------------------
    # HA enable/disable and PID control
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
        self._engine.pid.reset()

    # ------------------------------------------------------------------
    # Sensor reading (satisfies SensorReader protocol)
    # ------------------------------------------------------------------

    def read_sensor_safe(self, entity_id: str) -> float | None:
        """Read a sensor state as float, returning None on unavailable/non-numeric."""
        state = self.hass.states.get(entity_id)
        if state is None or state.state in ("unavailable", "unknown"):
            return None
        try:
            return float(state.state)
        except ValueError:
            return None

    def entity_state(self, entity_id: str) -> str | None:
        """Return entity state string, or None if missing/unavailable/unknown."""
        state = self.hass.states.get(entity_id)
        if state is None or state.state in ("unavailable", "unknown"):
            return None
        return str(state.state)

    async def _read_grid(self) -> float | None:
        """Read and sum grid import/export sensors. Returns None on failure."""
        total = 0.0
        for entity_id in self._import_sensors:
            val = self.read_sensor_safe(entity_id)
            if val is None:
                return None
            total += val
        for entity_id in self._export_sensors:
            val = self.read_sensor_safe(entity_id)
            if val is None:
                return None
            total -= val
        return total

    # ------------------------------------------------------------------
    # Calibration persistence
    # ------------------------------------------------------------------

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
        self._engine.pid.set_gains(kp, ki, self._engine.pid.kd)

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
