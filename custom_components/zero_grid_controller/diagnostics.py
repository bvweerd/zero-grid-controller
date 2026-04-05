"""Diagnostics support for Zero Grid Controller."""

from __future__ import annotations

from typing import Any

from homeassistant.components.diagnostics import async_redact_data
from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant
from homeassistant.helpers import entity_registry as er

from .const import (
    CONF_BATTERY_SENSOR,
    CONF_BATTERY_SETPOINT_ENTITY,
    CONF_GRID_SENSOR,
    CONF_GRID_SENSOR_EXPORT,
    CONF_GRID_SENSOR_IMPORT,
    CONF_MODE_GUARD_ENTITY,
)

TO_REDACT: set[str] = {
    CONF_GRID_SENSOR,
    CONF_GRID_SENSOR_IMPORT,
    CONF_GRID_SENSOR_EXPORT,
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
        }

    estimator_states: dict[str, Any] = {}
    if coordinator is not None:
        for name, est in coordinator._estimators.items():
            estimator_states[name] = est.to_dict()

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
        "pid": pid_state,
        "estimators": estimator_states,
        "entities": entities,
    }
