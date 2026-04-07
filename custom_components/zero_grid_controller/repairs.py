"""Repair issues for Zero Grid Controller."""

from __future__ import annotations

from homeassistant.core import HomeAssistant
from homeassistant.helpers.issue_registry import (
    IssueSeverity,
    async_create_issue,
    async_delete_issue,
)

from .const import DOMAIN

ISSUE_GRID_SENSOR_UNAVAILABLE = "grid_sensor_unavailable"


def raise_grid_sensor_unavailable(hass: HomeAssistant) -> None:
    """Create a repair issue for an unavailable grid sensor."""
    async_create_issue(
        hass,
        DOMAIN,
        ISSUE_GRID_SENSOR_UNAVAILABLE,
        is_fixable=False,
        severity=IssueSeverity.ERROR,
        translation_key=ISSUE_GRID_SENSOR_UNAVAILABLE,
    )


def dismiss_grid_sensor_unavailable(hass: HomeAssistant) -> None:
    """Delete the repair issue for an unavailable grid sensor."""
    async_delete_issue(hass, DOMAIN, ISSUE_GRID_SENSOR_UNAVAILABLE)
