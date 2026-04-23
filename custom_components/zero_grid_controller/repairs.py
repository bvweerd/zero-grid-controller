"""Repair issues for Zero Grid Controller."""

from __future__ import annotations

import logging

from homeassistant.core import HomeAssistant
from homeassistant.helpers import issue_registry as ir

from .const import DOMAIN

_LOGGER = logging.getLogger(__name__)

ISSUE_GRID_SENSOR_UNAVAILABLE = "grid_sensor_unavailable"


def raise_grid_sensor_unavailable(hass: HomeAssistant) -> None:
    """Raise a repair issue when all configured grid sensors are unavailable."""
    ir.async_create_issue(
        hass,
        DOMAIN,
        ISSUE_GRID_SENSOR_UNAVAILABLE,
        is_fixable=False,
        severity=ir.IssueSeverity.WARNING,
        translation_key=ISSUE_GRID_SENSOR_UNAVAILABLE,
    )


def dismiss_grid_sensor_unavailable(hass: HomeAssistant) -> None:
    """Dismiss the grid sensor unavailable repair issue."""
    ir.async_delete_issue(hass, DOMAIN, ISSUE_GRID_SENSOR_UNAVAILABLE)
