"""Diagnostics support for Zero Grid Controller."""

from __future__ import annotations

import time
from typing import Any

from homeassistant.components.diagnostics import async_redact_data
from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant
from homeassistant.helpers import entity_registry as er
from homeassistant.helpers.issue_registry import async_get as ir_async_get

from .const import (
    CONF_BATTERY_SENSOR,
    CONF_BATTERY_SETPOINT_ENTITY,
    CONF_GRID_EXPORT_SENSORS,
    CONF_GRID_IMPORT_SENSORS,
    CONF_MODE_GUARD_ENTITY,
    DEFAULT_RESPONSE_FACTOR,
    DOMAIN,
)

TO_REDACT: set[str] = {
    CONF_GRID_IMPORT_SENSORS,
    CONF_GRID_EXPORT_SENSORS,
    CONF_BATTERY_SENSOR,
    CONF_BATTERY_SETPOINT_ENTITY,
    CONF_MODE_GUARD_ENTITY,
}


async def async_get_config_entry_diagnostics(
    hass: HomeAssistant, entry: ConfigEntry
) -> dict[str, Any]:
    """Return diagnostics for this config entry."""
    runtime = entry.runtime_data

    coordinator = getattr(runtime, "coordinator", None)
    coord_data: dict[str, Any] = {}
    if coordinator is not None and coordinator.data is not None:
        d = coordinator.data
        coord_data = {
            "grid_raw_w": d.grid_raw_w,
            "grid_filtered_w": d.grid_filtered_w,
            "pid_output_w": d.pid_output_w,
            "pid_components": {
                "p": d.pid_p_w,
                "i": d.pid_i_w,
                "d": d.pid_d_w,
            },
            "mode": d.mode,
            "battery_clipping": d.battery_clipping,
            "learning_status": d.learning_status,
            "setpoints": d.setpoints,
            "array_clipping": d.array_clipping,
            "array_gain_k": d.array_gain_k,
            "array_calibration": d.array_calibration,
        }

    pid_state: dict[str, Any] = {}
    if coordinator is not None:
        pid = coordinator.pid
        pid_state = {
            "kp": pid.kp,
            "ki": pid.ki,
            "kd": pid.kd,
            "integral": pid.integral,
            "output_min": pid._output_min,
            "output_max": pid._output_max,
        }

    # Array configurations
    array_configs: dict[str, Any] = {}
    if coordinator is not None:
        for array in coordinator.arrays:
            array_configs[array.name] = {
                "output_type": array.output_type,
                "setpoint_min": array.setpoint_min,
                "setpoint_max": array.setpoint_max,
                "settling_time_s": array.settling_time_s,
                "max_power_w": array.max_power_w,
                "enabled": array.enabled,
            }

    # Active override setpoints (show only remaining seconds)
    now = time.monotonic()
    override_setpoints: dict[str, Any] = {}
    if coordinator is not None:
        for name, (value, expires_at) in coordinator._override_setpoints.items():
            remaining = max(0.0, expires_at - now)
            override_setpoints[name] = {
                "value": value,
                "remaining_seconds": round(remaining, 1),
            }

    # Settling state per array (seconds remaining, 0 if not settling)
    settling_state: dict[str, float] = {}
    if coordinator is not None:
        for array in coordinator.arrays:
            until = coordinator._settling_until.get(array.name, 0.0)
            settling_state[array.name] = round(max(0.0, until - now), 1)

    # Estimator details
    estimator_states: dict[str, Any] = {}
    if coordinator is not None:
        for array in coordinator.arrays:
            est = coordinator.get_estimator(array.name)
            if est is not None:
                est_dict = est.to_dict()
                est_dict["is_reliable"] = est.is_reliable
                est_dict["estimated_gain"] = est.estimated_gain
                est_dict["suggested_kp"] = est.suggest_kp(
                    coordinator.pid.kp, DEFAULT_RESPONSE_FACTOR
                )
                estimator_states[array.name] = est_dict

    # Repair issues
    issue_registry = ir_async_get(hass)
    repair_issues = [
        {
            "issue_id": issue.issue_id,
            "severity": issue.severity.value if issue.severity else None,
            "is_fixable": issue.is_fixable,
            "translation_key": issue.translation_key,
        }
        for issue in issue_registry.issues.values()
        if issue.domain == DOMAIN
    ]

    # Coordinator timing
    coordinator_timing: dict[str, Any] = {}
    if coordinator is not None:
        coordinator_timing = {
            "last_update_success": coordinator.last_update_success,
            "last_update_success_time": (
                coordinator.last_update_success_time.isoformat()
                if coordinator.last_update_success_time is not None
                else None
            ),
            "update_interval_s": (
                coordinator.update_interval.total_seconds()
                if coordinator.update_interval is not None
                else None
            ),
        }

    ent_reg = er.async_get(hass)
    entities = [
        {
            "entity_id": e.entity_id,
            "unique_id": e.unique_id,
            "state": (s := hass.states.get(e.entity_id)) and s.state,
        }
        for e in er.async_entries_for_config_entry(ent_reg, entry.entry_id)
    ]

    return {
        "config_entry": {
            "entry_id": entry.entry_id,
            "title": entry.title,
            "data": async_redact_data(dict(entry.data), TO_REDACT),
            "options": async_redact_data(dict(entry.options), TO_REDACT),
            "subentries": {
                sub.title: {
                    "type": sub.subentry_type,
                    "data": dict(sub.data),
                }
                for sub in entry.subentries.values()
            },
        },
        "coordinator": coord_data,
        "coordinator_timing": coordinator_timing,
        "pid": pid_state,
        "arrays": array_configs,
        "override_setpoints": override_setpoints,
        "settling_state": settling_state,
        "estimators": estimator_states,
        "repair_issues": repair_issues,
        "entities": entities,
    }
