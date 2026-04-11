"""Diagnostics for Zero Grid Controller."""

from __future__ import annotations

from typing import Any

from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant

from .coordinator import ZeroGridCoordinator


async def async_get_config_entry_diagnostics(
    hass: HomeAssistant, entry: ConfigEntry
) -> dict[str, Any]:
    """Return diagnostics for a config entry."""
    coordinator: ZeroGridCoordinator = entry.runtime_data.coordinator
    result = coordinator.data

    return {
        "status": result.status if result else None,
        "grid_raw_w": result.grid_raw_w if result else None,
        "grid_filtered_w": result.grid_filtered_w if result else None,
        "pid_output_w": result.pid_output_w if result else None,
        "setpoints": dict(result.setpoints) if result else {},
        "battery_setpoints": dict(result.battery_setpoints) if result else {},
        "pid": {
            "kp": coordinator._pid.kp,
            "ki": coordinator._pid.ki,
            "kd": coordinator._pid.kd,
            "integral": round(coordinator._pid.integral, 3),
        },
        "arrays": [
            {
                "name": a.name,
                "output_type": a.output_type,
                "setpoint_min": a.setpoint_min,
                "setpoint_max": a.setpoint_max,
                "w_per_unit": a.w_per_unit,
                "settling_time_s": a.settling_time_s,
                "calibration_confidence": a.calibration_confidence,
            }
            for a in coordinator.arrays
        ],
        "batteries": [
            {
                "name": b.name,
                "max_charge_w": b.max_charge_w,
                "max_discharge_w": b.max_discharge_w,
            }
            for b in coordinator.batteries
        ],
        "config": {
            "deadband_w": coordinator._deadband_w,
            "ewm_alpha": coordinator._ewm_alpha,
            "aggressiveness": coordinator._aggressiveness,
            "controller_enabled": coordinator._enabled,
        },
    }
