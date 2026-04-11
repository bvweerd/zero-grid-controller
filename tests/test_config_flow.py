"""Tests for config flow."""

from __future__ import annotations

import pytest
from pytest_homeassistant_custom_component.common import MockConfigEntry

from custom_components.zero_grid_controller.const import DOMAIN


@pytest.fixture(autouse=True)
def auto_enable_custom_integrations(enable_custom_integrations):
    return


async def test_user_step_creates_entry(hass):
    from homeassistant import config_entries

    result = await hass.config_entries.flow.async_init(
        DOMAIN, context={"source": config_entries.SOURCE_USER}
    )
    assert result["type"] == "form"
    assert result["step_id"] == "user"


async def test_user_step_error_no_sensors(hass):
    from homeassistant import config_entries

    result = await hass.config_entries.flow.async_init(
        DOMAIN, context={"source": config_entries.SOURCE_USER}
    )
    result = await hass.config_entries.flow.async_configure(
        result["flow_id"],
        user_input={
            "name": "Test",
            "grid_import_sensors": [],
            "grid_export_sensors": [],
        },
    )
    assert result["type"] == "form"
    assert "no_sensors" in result.get("errors", {}).get("base", "no_sensors")


async def test_user_step_success(hass):
    from homeassistant import config_entries

    result = await hass.config_entries.flow.async_init(
        DOMAIN, context={"source": config_entries.SOURCE_USER}
    )
    result = await hass.config_entries.flow.async_configure(
        result["flow_id"],
        user_input={
            "name": "My ZGC",
            "grid_import_sensors": ["sensor.grid_import"],
            "grid_export_sensors": ["sensor.grid_export"],
        },
    )
    assert result["type"] == "create_entry"
    assert result["title"] == "My ZGC"
