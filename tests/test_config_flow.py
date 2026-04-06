"""Tests for the Zero Grid Controller config flow."""
from __future__ import annotations

import pytest
from homeassistant import config_entries
from homeassistant.core import HomeAssistant
from homeassistant.data_entry_flow import FlowResultType
from pytest_homeassistant_custom_component.common import MockConfigEntry

from custom_components.zero_grid_controller.const import (
    ARRAY_SUBENTRY_TYPE,
    CONF_ARRAY_NAME,
    CONF_GRID_MEASUREMENT_TYPE,
    CONF_GRID_SENSOR,
    CONF_GRID_SENSOR_EXPORT,
    CONF_GRID_SENSOR_IMPORT,
    CONF_INVERTER_SPEED,
    CONF_OUTPUT_TYPE,
    CONF_POWER_CONSUMPTION_SENSORS,
    CONF_POWER_PRODUCTION_SENSORS,
    CONF_PV_POWER_ENTITY,
    CONF_RESPONSE_FACTOR,
    CONF_SETPOINT_ENTITY,
    DOMAIN,
    OUTPUT_TYPE_PERCENT,
)


# ---------------------------------------------------------------------------
# Helper to register the integration's custom_components path
# ---------------------------------------------------------------------------

@pytest.fixture(autouse=True)
def auto_enable_custom_integrations(enable_custom_integrations):
    """Enable custom integrations for all tests in this module."""
    return


# ---------------------------------------------------------------------------
# Test 1: Full wizard — net mode, no battery, no mode guard → CREATE_ENTRY
# ---------------------------------------------------------------------------

async def test_full_wizard_net_mode(hass: HomeAssistant) -> None:
    """Test the full wizard with net measurement type and no optional steps."""
    result = await hass.config_entries.flow.async_init(
        DOMAIN, context={"source": config_entries.SOURCE_USER}
    )
    assert result["type"] == FlowResultType.FORM
    assert result["step_id"] == "user"

    # Submit step 1: user
    result = await hass.config_entries.flow.async_configure(
        result["flow_id"],
        user_input={
            "name": "My ZGC",
            CONF_GRID_MEASUREMENT_TYPE: "net",
            CONF_GRID_SENSOR: "sensor.grid_power",
            "invert_sign": False,
        },
    )
    assert result["type"] == FlowResultType.FORM
    assert result["step_id"] == "battery"

    # Submit step 2: battery — no battery
    result = await hass.config_entries.flow.async_configure(
        result["flow_id"],
        user_input={"has_battery": False},
    )
    assert result["type"] == FlowResultType.FORM
    assert result["step_id"] == "mode_guard"

    # Submit step 3: mode guard — disabled
    result = await hass.config_entries.flow.async_configure(
        result["flow_id"],
        user_input={"mode_guard_enabled": False},
    )
    assert result["type"] == FlowResultType.CREATE_ENTRY
    assert result["title"] == "My ZGC"
    assert result["data"][CONF_GRID_SENSOR] == "sensor.grid_power"
    assert result["data"][CONF_GRID_MEASUREMENT_TYPE] == "net"


# ---------------------------------------------------------------------------
# Test 2: Wizard validation — net mode without sensor → error
# ---------------------------------------------------------------------------

async def test_wizard_validation_net_missing_sensor(hass: HomeAssistant) -> None:
    """Test that submitting net mode without a sensor triggers a validation error."""
    result = await hass.config_entries.flow.async_init(
        DOMAIN, context={"source": config_entries.SOURCE_USER}
    )
    assert result["type"] == FlowResultType.FORM

    result = await hass.config_entries.flow.async_configure(
        result["flow_id"],
        user_input={
            "name": "My ZGC",
            CONF_GRID_MEASUREMENT_TYPE: "net",
            # No grid_sensor provided
            "invert_sign": False,
        },
    )
    assert result["type"] == FlowResultType.FORM
    assert result["step_id"] == "user"
    assert CONF_GRID_SENSOR in result["errors"]


# ---------------------------------------------------------------------------
# Test 3: Wizard — split mode with import/export sensors → CREATE_ENTRY
# ---------------------------------------------------------------------------

async def test_wizard_split_mode(hass: HomeAssistant) -> None:
    """Test wizard with split measurement mode."""
    result = await hass.config_entries.flow.async_init(
        DOMAIN, context={"source": config_entries.SOURCE_USER}
    )

    result = await hass.config_entries.flow.async_configure(
        result["flow_id"],
        user_input={
            "name": "Split ZGC",
            CONF_GRID_MEASUREMENT_TYPE: "split",
            CONF_GRID_SENSOR_IMPORT: "sensor.grid_import",
            CONF_GRID_SENSOR_EXPORT: "sensor.grid_export",
            "invert_sign": False,
        },
    )
    assert result["type"] == FlowResultType.FORM
    assert result["step_id"] == "battery"

    result = await hass.config_entries.flow.async_configure(
        result["flow_id"],
        user_input={"has_battery": False},
    )
    assert result["step_id"] == "mode_guard"

    result = await hass.config_entries.flow.async_configure(
        result["flow_id"],
        user_input={"mode_guard_enabled": False},
    )
    assert result["type"] == FlowResultType.CREATE_ENTRY
    assert result["data"][CONF_GRID_MEASUREMENT_TYPE] == "split"
    assert result["data"][CONF_GRID_SENSOR_IMPORT] == "sensor.grid_import"
    assert result["data"][CONF_GRID_SENSOR_EXPORT] == "sensor.grid_export"


# ---------------------------------------------------------------------------
# Test 4: Wizard — computed mode with consumption/production sensors
# ---------------------------------------------------------------------------

async def test_wizard_computed_mode(hass: HomeAssistant) -> None:
    """Test wizard with computed measurement mode."""
    result = await hass.config_entries.flow.async_init(
        DOMAIN, context={"source": config_entries.SOURCE_USER}
    )

    result = await hass.config_entries.flow.async_configure(
        result["flow_id"],
        user_input={
            "name": "Computed ZGC",
            CONF_GRID_MEASUREMENT_TYPE: "computed",
            CONF_POWER_CONSUMPTION_SENSORS: ["sensor.consumption"],
            CONF_POWER_PRODUCTION_SENSORS: ["sensor.production"],
            "invert_sign": False,
        },
    )
    assert result["type"] == FlowResultType.FORM
    assert result["step_id"] == "battery"

    result = await hass.config_entries.flow.async_configure(
        result["flow_id"],
        user_input={"has_battery": False},
    )
    result = await hass.config_entries.flow.async_configure(
        result["flow_id"],
        user_input={"mode_guard_enabled": False},
    )
    assert result["type"] == FlowResultType.CREATE_ENTRY
    assert result["data"][CONF_GRID_MEASUREMENT_TYPE] == "computed"
    assert result["data"][CONF_POWER_CONSUMPTION_SENSORS] == ["sensor.consumption"]
    assert result["data"][CONF_POWER_PRODUCTION_SENSORS] == ["sensor.production"]


# ---------------------------------------------------------------------------
# Test 5: Wizard — with mode guard enabled and mode mapping
# ---------------------------------------------------------------------------

async def test_wizard_with_mode_guard(hass: HomeAssistant) -> None:
    """Test wizard with mode guard enabled, which adds a mode_mapping step."""
    # Set up a mock input_select state
    hass.states.async_set(
        "input_select.mode", "export", {"options": ["home", "export", "away"]}
    )

    result = await hass.config_entries.flow.async_init(
        DOMAIN, context={"source": config_entries.SOURCE_USER}
    )

    result = await hass.config_entries.flow.async_configure(
        result["flow_id"],
        user_input={
            "name": "Guard ZGC",
            CONF_GRID_MEASUREMENT_TYPE: "net",
            CONF_GRID_SENSOR: "sensor.grid_power",
            "invert_sign": False,
        },
    )
    # Battery step
    result = await hass.config_entries.flow.async_configure(
        result["flow_id"],
        user_input={"has_battery": False},
    )
    # Mode guard step — enable it
    result = await hass.config_entries.flow.async_configure(
        result["flow_id"],
        user_input={
            "mode_guard_enabled": True,
            "mode_guard_entity": "input_select.mode",
        },
    )
    assert result["type"] == FlowResultType.FORM
    assert result["step_id"] == "mode_mapping"

    # Mode mapping step — map each option
    result = await hass.config_entries.flow.async_configure(
        result["flow_id"],
        user_input={
            "home": "active",
            "export": "passive",
            "away": "disabled",
        },
    )
    assert result["type"] == FlowResultType.CREATE_ENTRY
    assert result["data"]["mode_guard_enabled"] is True
    assert result["data"]["mode_guard_mapping"]["home"] == "active"
    assert result["data"]["mode_guard_mapping"]["export"] == "passive"
    assert result["data"]["mode_guard_mapping"]["away"] == "disabled"


# ---------------------------------------------------------------------------
# Test 6: Duplicate entry aborts
# ---------------------------------------------------------------------------

async def test_duplicate_entry_aborts(hass: HomeAssistant) -> None:
    """Test that trying to add a second entry aborts with already_configured."""
    # Create a first entry
    entry = MockConfigEntry(
        domain=DOMAIN,
        unique_id=DOMAIN,
        data={
            "name": "ZGC",
            CONF_GRID_MEASUREMENT_TYPE: "net",
            CONF_GRID_SENSOR: "sensor.grid_power",
            "invert_sign": False,
        },
    )
    entry.add_to_hass(hass)

    # Try to init a second flow
    result = await hass.config_entries.flow.async_init(
        DOMAIN, context={"source": config_entries.SOURCE_USER}
    )
    assert result["type"] == FlowResultType.ABORT
    assert result["reason"] == "already_configured"


# ---------------------------------------------------------------------------
# Test 7: Options flow — speed
# ---------------------------------------------------------------------------

async def test_options_flow_speed(hass: HomeAssistant) -> None:
    """Test options flow selecting a response speed."""
    entry = MockConfigEntry(
        domain=DOMAIN,
        unique_id=DOMAIN,
        data={
            "name": "ZGC",
            CONF_GRID_MEASUREMENT_TYPE: "net",
            CONF_GRID_SENSOR: "sensor.grid_power",
            "invert_sign": False,
        },
        options={},
    )
    entry.add_to_hass(hass)

    result = await hass.config_entries.options.async_init(entry.entry_id)
    assert result["type"] == FlowResultType.MENU
    assert result["step_id"] == "init"

    result = await hass.config_entries.options.async_configure(
        result["flow_id"],
        user_input={"next_step_id": "speed"},
    )
    assert result["type"] == FlowResultType.FORM
    assert result["step_id"] == "speed"

    result = await hass.config_entries.options.async_configure(
        result["flow_id"],
        user_input={"response_speed": "fast"},
    )
    assert result["type"] == FlowResultType.CREATE_ENTRY
    assert result["data"][CONF_RESPONSE_FACTOR] == 2.0


# ---------------------------------------------------------------------------
# Test 8: Options flow — expert mode
# ---------------------------------------------------------------------------

async def test_options_flow_expert(hass: HomeAssistant) -> None:
    """Test options flow enabling expert mode."""
    entry = MockConfigEntry(
        domain=DOMAIN,
        unique_id=DOMAIN,
        data={
            "name": "ZGC",
            CONF_GRID_MEASUREMENT_TYPE: "net",
            CONF_GRID_SENSOR: "sensor.grid_power",
            "invert_sign": False,
        },
        options={},
    )
    entry.add_to_hass(hass)

    result = await hass.config_entries.options.async_init(entry.entry_id)
    result = await hass.config_entries.options.async_configure(
        result["flow_id"],
        user_input={"next_step_id": "expert"},
    )
    assert result["type"] == FlowResultType.FORM
    assert result["step_id"] == "expert"

    result = await hass.config_entries.options.async_configure(
        result["flow_id"],
        user_input={"expert_mode": True},
    )
    assert result["type"] == FlowResultType.CREATE_ENTRY
    assert result["data"]["expert_mode"] is True


# ---------------------------------------------------------------------------
# Test 9: Add array subentry
# ---------------------------------------------------------------------------

async def test_subentry_flow_add_array(hass: HomeAssistant) -> None:
    """Test adding a PV array subentry via the subentry flow."""
    entry = MockConfigEntry(
        domain=DOMAIN,
        unique_id=DOMAIN,
        data={
            "name": "ZGC",
            CONF_GRID_MEASUREMENT_TYPE: "net",
            CONF_GRID_SENSOR: "sensor.grid_power",
            "invert_sign": False,
        },
        options={},
    )
    entry.add_to_hass(hass)

    # Simulate an entity existing for the selector
    hass.states.async_set("number.inverter_limit", "100")

    result = await hass.config_entries.subentries.async_init(
        (entry.entry_id, ARRAY_SUBENTRY_TYPE),
        context={"source": config_entries.SOURCE_USER},
    )
    assert result["type"] == FlowResultType.FORM
    assert result["step_id"] == "user"

    result = await hass.config_entries.subentries.async_configure(
        result["flow_id"],
        user_input={
            CONF_ARRAY_NAME: "Roof South",
            CONF_SETPOINT_ENTITY: "number.inverter_limit",
            CONF_OUTPUT_TYPE: OUTPUT_TYPE_PERCENT,
            CONF_INVERTER_SPEED: "normal",
            "setpoint_min": 0.0,
            "setpoint_max": 100.0,
            "priority": 1,
        },
    )
    assert result["type"] == FlowResultType.CREATE_ENTRY
    assert result["title"] == "Roof South"
    assert result["data"][CONF_ARRAY_NAME] == "Roof South"
    assert result["data"][CONF_OUTPUT_TYPE] == OUTPUT_TYPE_PERCENT


# ---------------------------------------------------------------------------
# Test 10: Reconfigure array subentry
# ---------------------------------------------------------------------------

async def test_subentry_reconfigure(hass: HomeAssistant) -> None:
    """Test reconfiguring an existing PV array subentry."""
    entry = MockConfigEntry(
        domain=DOMAIN,
        unique_id=DOMAIN,
        data={
            "name": "ZGC",
            CONF_GRID_MEASUREMENT_TYPE: "net",
            CONF_GRID_SENSOR: "sensor.grid_power",
            "invert_sign": False,
        },
        options={},
        subentries_data=[
            {
                "subentry_type": ARRAY_SUBENTRY_TYPE,
                "title": "Roof South",
                "data": {
                    CONF_ARRAY_NAME: "Roof South",
                    CONF_SETPOINT_ENTITY: "number.inverter_limit",
                    CONF_OUTPUT_TYPE: OUTPUT_TYPE_PERCENT,
                    CONF_INVERTER_SPEED: "normal",
                    "settling_time_s": 15,
                    "setpoint_min": 0.0,
                    "setpoint_max": 100.0,
                    "priority": 1,
                    "w_per_unit": 10.0,
                    "calibration_confidence": "estimated",
                    "enabled": True,
                },
                "unique_id": None,
            }
        ],
    )
    entry.add_to_hass(hass)

    # Get the subentry id
    subentry_id = next(iter(entry.subentries))

    hass.states.async_set("number.inverter_limit", "100")

    result = await hass.config_entries.subentries.async_init(
        (entry.entry_id, ARRAY_SUBENTRY_TYPE),
        context={
            "source": config_entries.SOURCE_RECONFIGURE,
            "subentry_id": subentry_id,
        },
    )
    assert result["type"] == FlowResultType.FORM
    assert result["step_id"] == "reconfigure"

    result = await hass.config_entries.subentries.async_configure(
        result["flow_id"],
        user_input={
            CONF_ARRAY_NAME: "Roof South Updated",
            CONF_SETPOINT_ENTITY: "number.inverter_limit",
            CONF_OUTPUT_TYPE: OUTPUT_TYPE_PERCENT,
            CONF_INVERTER_SPEED: "fast",
            "setpoint_min": 10.0,
            "setpoint_max": 100.0,
            "priority": 2,
        },
    )
    assert result["type"] == FlowResultType.ABORT
    assert result["reason"] == "reconfigure_successful"
    # Check the subentry was updated
    updated_subentry = entry.subentries[subentry_id]
    assert updated_subentry.data[CONF_ARRAY_NAME] == "Roof South Updated"
    assert updated_subentry.data["priority"] == 2


# ---------------------------------------------------------------------------
# Test 11: Wizard validation — split mode missing sensors → error
# ---------------------------------------------------------------------------

async def test_wizard_validation_split_missing_sensors(hass: HomeAssistant) -> None:
    """Test that split mode without import/export sensors triggers validation error."""
    result = await hass.config_entries.flow.async_init(
        DOMAIN, context={"source": config_entries.SOURCE_USER}
    )

    result = await hass.config_entries.flow.async_configure(
        result["flow_id"],
        user_input={
            "name": "My ZGC",
            CONF_GRID_MEASUREMENT_TYPE: "split",
            # Missing CONF_GRID_SENSOR_IMPORT and CONF_GRID_SENSOR_EXPORT
            "invert_sign": False,
        },
    )
    assert result["type"] == FlowResultType.FORM
    assert result["step_id"] == "user"
    assert "base" in result["errors"]
    assert result["errors"]["base"] == "split_sensors_required"


# ---------------------------------------------------------------------------
# Test 12: Wizard validation — computed mode missing sensors → error
# ---------------------------------------------------------------------------

async def test_wizard_validation_computed_missing_sensors(hass: HomeAssistant) -> None:
    """Test that computed mode without consumption/production sensors triggers error."""
    result = await hass.config_entries.flow.async_init(
        DOMAIN, context={"source": config_entries.SOURCE_USER}
    )

    result = await hass.config_entries.flow.async_configure(
        result["flow_id"],
        user_input={
            "name": "My ZGC",
            CONF_GRID_MEASUREMENT_TYPE: "computed",
            # Missing CONF_POWER_CONSUMPTION_SENSORS and CONF_POWER_PRODUCTION_SENSORS
            "invert_sign": False,
        },
    )
    assert result["type"] == FlowResultType.FORM
    assert result["step_id"] == "user"
    assert "base" in result["errors"]
    assert result["errors"]["base"] == "computed_sensors_required"


# ---------------------------------------------------------------------------
# Test 13: Wizard — battery step with has_battery=True stores battery data
# ---------------------------------------------------------------------------

async def test_wizard_battery_step_with_battery(hass: HomeAssistant) -> None:
    """Battery step with has_battery=True stores battery config data."""
    result = await hass.config_entries.flow.async_init(
        DOMAIN, context={"source": config_entries.SOURCE_USER}
    )

    result = await hass.config_entries.flow.async_configure(
        result["flow_id"],
        user_input={
            "name": "Battery ZGC",
            CONF_GRID_MEASUREMENT_TYPE: "net",
            CONF_GRID_SENSOR: "sensor.grid_power",
            "invert_sign": False,
        },
    )
    assert result["step_id"] == "battery"

    # Submit battery step with has_battery=True
    result = await hass.config_entries.flow.async_configure(
        result["flow_id"],
        user_input={
            "has_battery": True,
            "battery_sensor": "sensor.battery_power",
            "battery_max_charge_w": 3000.0,
            "battery_control_enabled": False,
        },
    )
    assert result["step_id"] == "mode_guard"

    result = await hass.config_entries.flow.async_configure(
        result["flow_id"],
        user_input={"mode_guard_enabled": False},
    )
    assert result["type"] == FlowResultType.CREATE_ENTRY
    # battery_sensor should be stored, but has_battery key should be removed
    assert "has_battery" not in result["data"]
    assert result["data"].get("battery_sensor") == "sensor.battery_power"


# ---------------------------------------------------------------------------
# Test 14: Mode guard with no entity options → skip mode_mapping
# ---------------------------------------------------------------------------

async def test_wizard_mode_guard_no_entity_options(hass: HomeAssistant) -> None:
    """Mode guard enabled but entity has no options → skip mode_mapping step."""
    # Set up a state without 'options' attribute
    hass.states.async_set("sensor.simple_sensor", "on")

    result = await hass.config_entries.flow.async_init(
        DOMAIN, context={"source": config_entries.SOURCE_USER}
    )

    result = await hass.config_entries.flow.async_configure(
        result["flow_id"],
        user_input={
            "name": "Guard ZGC",
            CONF_GRID_MEASUREMENT_TYPE: "net",
            CONF_GRID_SENSOR: "sensor.grid_power",
            "invert_sign": False,
        },
    )
    result = await hass.config_entries.flow.async_configure(
        result["flow_id"],
        user_input={"has_battery": False},
    )
    # Enable mode guard with a sensor that has no 'options' attribute
    result = await hass.config_entries.flow.async_configure(
        result["flow_id"],
        user_input={
            "mode_guard_enabled": True,
            "mode_guard_entity": "sensor.simple_sensor",
        },
    )
    # Should have gone to mode_mapping step with the state value as options
    assert result["type"] == FlowResultType.FORM
    assert result["step_id"] == "mode_mapping"


# ---------------------------------------------------------------------------
# Test 15: Options flow — recalibrate step
# ---------------------------------------------------------------------------

async def test_options_flow_recalibrate(hass: HomeAssistant) -> None:
    """Options flow recalibrate step triggers the recalibrate service."""
    from unittest.mock import AsyncMock as _AsyncMock

    entry = MockConfigEntry(
        domain=DOMAIN,
        unique_id=DOMAIN,
        data={
            "name": "ZGC",
            CONF_GRID_MEASUREMENT_TYPE: "net",
            CONF_GRID_SENSOR: "sensor.grid_power",
            "invert_sign": False,
        },
        options={},
    )
    entry.add_to_hass(hass)

    # Register the recalibrate service so the options flow can call it
    hass.services.async_register(DOMAIN, "recalibrate", _AsyncMock())

    result = await hass.config_entries.options.async_init(entry.entry_id)
    assert result["type"] == FlowResultType.MENU

    result = await hass.config_entries.options.async_configure(
        result["flow_id"],
        user_input={"next_step_id": "recalibrate"},
    )
    assert result["type"] == FlowResultType.FORM
    assert result["step_id"] == "recalibrate"

    result = await hass.config_entries.options.async_configure(
        result["flow_id"],
        user_input={},
    )
    assert result["type"] == FlowResultType.CREATE_ENTRY


# ---------------------------------------------------------------------------
# Test 16: Array subentry with PV power entity
# ---------------------------------------------------------------------------

async def test_subentry_flow_with_pv_entity(hass: HomeAssistant) -> None:
    """Array subentry flow stores PV power entity when provided."""
    entry = MockConfigEntry(
        domain=DOMAIN,
        unique_id=DOMAIN,
        data={
            "name": "ZGC",
            CONF_GRID_MEASUREMENT_TYPE: "net",
            CONF_GRID_SENSOR: "sensor.grid_power",
            "invert_sign": False,
        },
        options={},
    )
    entry.add_to_hass(hass)

    hass.states.async_set("number.inverter_limit", "100")
    hass.states.async_set("sensor.pv_power", "1000")

    result = await hass.config_entries.subentries.async_init(
        (entry.entry_id, ARRAY_SUBENTRY_TYPE),
        context={"source": config_entries.SOURCE_USER},
    )

    result = await hass.config_entries.subentries.async_configure(
        result["flow_id"],
        user_input={
            CONF_ARRAY_NAME: "Roof South",
            CONF_SETPOINT_ENTITY: "number.inverter_limit",
            CONF_OUTPUT_TYPE: OUTPUT_TYPE_PERCENT,
            CONF_INVERTER_SPEED: "normal",
            "setpoint_min": 0.0,
            "setpoint_max": 100.0,
            "priority": 1,
            "pv_power_entity": "sensor.pv_power",
        },
    )
    assert result["type"] == FlowResultType.CREATE_ENTRY
    assert result["data"]["pv_power_entity"] == "sensor.pv_power"


# ---------------------------------------------------------------------------
# Test 17: Mode guard enabled with nonexistent entity → skip mode_mapping (line 353)
# ---------------------------------------------------------------------------

async def test_wizard_mode_guard_nonexistent_entity(hass: HomeAssistant) -> None:
    """Mode guard enabled with entity not in states → _mode_guard_states empty → skip mapping."""
    result = await hass.config_entries.flow.async_init(
        DOMAIN, context={"source": config_entries.SOURCE_USER}
    )

    result = await hass.config_entries.flow.async_configure(
        result["flow_id"],
        user_input={
            "name": "Guard ZGC",
            CONF_GRID_MEASUREMENT_TYPE: "net",
            CONF_GRID_SENSOR: "sensor.grid_power",
            "invert_sign": False,
        },
    )
    result = await hass.config_entries.flow.async_configure(
        result["flow_id"],
        user_input={"has_battery": False},
    )
    # Enable mode guard with an entity that doesn't exist in hass.states
    result = await hass.config_entries.flow.async_configure(
        result["flow_id"],
        user_input={
            "mode_guard_enabled": True,
            "mode_guard_entity": "input_select.nonexistent",
        },
    )
    # state is None → _mode_guard_states stays [] → line 353 → async_step_done → CREATE_ENTRY
    assert result["type"] == FlowResultType.CREATE_ENTRY
    assert result["data"]["mode_guard_enabled"] is True
