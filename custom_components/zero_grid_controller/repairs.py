"""Repair issues for Zero Grid Controller."""

from __future__ import annotations

import logging

from homeassistant.core import HomeAssistant
from homeassistant.helpers.issue_registry import (
    IssueSeverity,
    async_create_issue,
    async_delete_issue,
)

from .const import DOMAIN

_LOGGER = logging.getLogger(__name__)

ISSUE_GRID_SENSOR_UNAVAILABLE = "grid_sensor_unavailable"
ISSUE_GRID_SENSOR_STALE = "grid_sensor_stale"
ISSUE_BATTERY_UNRESPONSIVE = "battery_unresponsive"
ISSUE_SAFE_STATE_ACTIVE = "safe_state_active"
ISSUE_MODE_GUARD_INVALID_STATE = "mode_guard_invalid_state"
ISSUE_ARRAY_CONFIGURATION_PROBLEM = "array_configuration_problem"
ISSUE_CALIBRATION_NOT_CONVERGING = "calibration_not_converging"


def _slugify(value: str) -> str:
    """Convert a label into a stable issue-id suffix."""
    chars = [c.lower() if c.isalnum() else "_" for c in value.strip()]
    slug = "".join(chars).strip("_")
    while "__" in slug:
        slug = slug.replace("__", "_")
    return slug or "unnamed"


def issue_id(base: str, suffix: str | None = None) -> str:
    """Build a stable issue id from a base key and optional suffix."""
    if not suffix:
        return base
    return f"{base}_{_slugify(suffix)}"


def create_issue(
    hass: HomeAssistant,
    issue_key: str,
    *,
    suffix: str | None = None,
    severity: IssueSeverity = IssueSeverity.ERROR,
    placeholders: dict[str, str] | None = None,
) -> str:
    """Create a repair issue and return its full issue_id."""
    full_issue_id = issue_id(issue_key, suffix)
    try:
        async_create_issue(
            hass,
            DOMAIN,
            full_issue_id,
            is_fixable=False,
            severity=severity,
            translation_key=issue_key,
            translation_placeholders=placeholders,
        )
    except Exception as err:  # pragma: no cover - issue registry availability
        _LOGGER.debug("Failed to create issue %s: %s", full_issue_id, err)
    return full_issue_id


def dismiss_issue(
    hass: HomeAssistant,
    issue_key: str,
    *,
    suffix: str | None = None,
) -> str:
    """Delete a repair issue and return its full issue_id."""
    full_issue_id = issue_id(issue_key, suffix)
    try:
        async_delete_issue(hass, DOMAIN, full_issue_id)
    except Exception as err:  # pragma: no cover - issue registry availability
        _LOGGER.debug("Failed to delete issue %s: %s", full_issue_id, err)
    return full_issue_id


def raise_grid_sensor_unavailable(hass: HomeAssistant) -> None:
    """Create a repair issue for an unavailable grid sensor."""
    create_issue(hass, ISSUE_GRID_SENSOR_UNAVAILABLE)


def dismiss_grid_sensor_unavailable(hass: HomeAssistant) -> None:
    """Delete the repair issue for an unavailable grid sensor."""
    dismiss_issue(hass, ISSUE_GRID_SENSOR_UNAVAILABLE)
