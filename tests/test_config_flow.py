"""Tests for the Zero Grid Controller config flow."""

from __future__ import annotations

import pytest
from homeassistant import config_entries
from homeassistant.core import HomeAssistant
from homeassistant.data_entry_flow import FlowResultType
from pytest_homeassistant_custom_component.common import MockConfigEntry

from custom_components.zero_grid_controller.const import (
    ARRAY_SUBENTRY_TYPE,
    BATTERY_SUBENTRY_TYPE,
    CONF_ARRAY_NAME,
    CONF_GRID_EXPORT_SENSORS,
    CONF_GRID_IMPORT_SENSORS,
    CONF_INVERTER_SPEED,
    CONF_OUTPUT_TYPE,
    CONF_PV_POWER_ENTITY,
    CONF_RESPONSE_FACTOR,
    CONF_SETPOINT_ENTITY,
    DOMAIN,
    OUTPUT_TYPE_PERCENT,
)


@pytest.fixture(autouse=True)
def auto_enable_custom_integrations(enable_custom_integrations):
    """Enable custom integrations for all tests in this module."""
    return


# ---------------------------------------------------------------------------
# Test 1: Minimal wizard — import sensors only, no mode guard → CREATE_ENTRY
# ---------------------------------------------------------------------------


async def test_full_wizard_minimal(hass: HomeAssistant) -> None:
    """Test full wizard with only import sensors, skipping mode guard."""
    hass.states.async_set("sensor.grid_import", "500")

    result = await hass.config_entries.flow.async_init(
        DOMAIN, context={"source": config_entries.SOURCE_USER}
    )
    assert result["type"] == FlowResultType.FORM
    assert result["step_id"] == "user"

    result = await hass.config_entries.flow.async_configure(
        result["flow_id"],
        user_input={
            "name": "My ZGC",
            CONF_GRID_IMPORT_SENSORS: ["sensor.grid_import"],
            "invert_sign": False,
        },
    )
    assert result["type"] == FlowResultType.FORM
    assert result["step_id"] == "mode_guard"

    result = await hass.config_entries.flow.async_configure(
        result["flow_id"],
        user_input={"mode_guard_enabled": False},
    )
    assert result["type"] == FlowResultType.CREATE_ENTRY
    assert result["title"] == "My ZGC"
    assert result["data"][CONF_GRID_IMPORT_SENSORS] == ["sensor.grid_import"]
    assert CONF_GRID_EXPORT_SENSORS not in result["data"]


# ---------------------------------------------------------------------------
# Test 2: Wizard with import + export sensors
# ---------------------------------------------------------------------------


async def test_full_wizard_with_export_sensors(hass: HomeAssistant) -> None:
    """Test wizard with both import and export sensors."""
    hass.states.async_set("sensor.grid_import", "500")
    hass.states.async_set("sensor.grid_export", "0")

    result = await hass.config_entries.flow.async_init(
        DOMAIN, context={"source": config_entries.SOURCE_USER}
    )

    result = await hass.config_entries.flow.async_configure(
        result["flow_id"],
        user_input={
            "name": "Split ZGC",
            CONF_GRID_IMPORT_SENSORS: ["sensor.grid_import"],
            CONF_GRID_EXPORT_SENSORS: ["sensor.grid_export"],
            "invert_sign": False,
        },
    )
    assert result["step_id"] == "mode_guard"

    result = await hass.config_entries.flow.async_configure(
        result["flow_id"],
        user_input={"mode_guard_enabled": False},
    )
    assert result["type"] == FlowResultType.CREATE_ENTRY
    assert result["data"][CONF_GRID_IMPORT_SENSORS] == ["sensor.grid_import"]
    assert result["data"][CONF_GRID_EXPORT_SENSORS] == ["sensor.grid_export"]


# ---------------------------------------------------------------------------
# Test 3: Wizard validation — missing import sensors → error
# ---------------------------------------------------------------------------


async def test_wizard_validation_missing_import_sensors(hass: HomeAssistant) -> None:
    """Test that submitting without import sensors triggers a validation error."""
    result = await hass.config_entries.flow.async_init(
        DOMAIN, context={"source": config_entries.SOURCE_USER}
    )

    result = await hass.config_entries.flow.async_configure(
        result["flow_id"],
        user_input={
            "name": "My ZGC",
            CONF_GRID_IMPORT_SENSORS: [],  # Empty list — custom validation rejects this
            "invert_sign": False,
        },
    )
    assert result["type"] == FlowResultType.FORM
    assert result["step_id"] == "user"
    assert CONF_GRID_IMPORT_SENSORS in result["errors"]


# ---------------------------------------------------------------------------
# Test 4: Wizard with mode guard enabled and mode mapping
# ---------------------------------------------------------------------------


async def test_wizard_with_mode_guard(hass: HomeAssistant) -> None:
    """Test wizard with mode guard that adds a mode_mapping step."""
    hass.states.async_set("sensor.grid_import", "0")
    hass.states.async_set(
        "input_select.optimizer", "home", {"options": ["home", "export", "away"]}
    )

    result = await hass.config_entries.flow.async_init(
        DOMAIN, context={"source": config_entries.SOURCE_USER}
    )

    result = await hass.config_entries.flow.async_configure(
        result["flow_id"],
        user_input={
            "name": "Guard ZGC",
            CONF_GRID_IMPORT_SENSORS: ["sensor.grid_import"],
            "invert_sign": False,
        },
    )
    assert result["step_id"] == "mode_guard"

    result = await hass.config_entries.flow.async_configure(
        result["flow_id"],
        user_input={
            "mode_guard_enabled": True,
            "mode_guard_entity": "input_select.optimizer",
        },
    )
    assert result["type"] == FlowResultType.FORM
    assert result["step_id"] == "mode_mapping"

    result = await hass.config_entries.flow.async_configure(
        result["flow_id"],
        user_input={"home": "active", "export": "passive", "away": "disabled"},
    )
    assert result["type"] == FlowResultType.CREATE_ENTRY
    assert result["data"]["mode_guard_enabled"] is True
    assert result["data"]["mode_guard_mapping"]["home"] == "active"
    assert result["data"]["mode_guard_mapping"]["export"] == "passive"
    assert result["data"]["mode_guard_mapping"]["away"] == "disabled"


# ---------------------------------------------------------------------------
# Test 5: Duplicate entry aborts
# ---------------------------------------------------------------------------


async def test_duplicate_entry_aborts(hass: HomeAssistant) -> None:
    """Test that a second entry attempt aborts with already_configured."""
    entry = MockConfigEntry(
        domain=DOMAIN,
        unique_id=DOMAIN,
        data={
            "name": "ZGC",
            CONF_GRID_IMPORT_SENSORS: ["sensor.grid_import"],
            "invert_sign": False,
        },
    )
    entry.add_to_hass(hass)

    result = await hass.config_entries.flow.async_init(
        DOMAIN, context={"source": config_entries.SOURCE_USER}
    )
    assert result["type"] == FlowResultType.ABORT
    assert result["reason"] == "already_configured"


# ---------------------------------------------------------------------------
# Test 6: Options flow — expert mode
# ---------------------------------------------------------------------------


async def test_options_flow_expert_mode(hass: HomeAssistant) -> None:
    """Test options flow enabling expert mode."""
    entry = MockConfigEntry(
        domain=DOMAIN,
        unique_id=DOMAIN,
        data={
            "name": "ZGC",
            CONF_GRID_IMPORT_SENSORS: ["sensor.grid_import"],
            "invert_sign": False,
        },
        options={},
    )
    entry.add_to_hass(hass)

    result = await hass.config_entries.options.async_init(entry.entry_id)
    assert result["type"] == FlowResultType.FORM
    assert result["step_id"] == "init"

    result = await hass.config_entries.options.async_configure(
        result["flow_id"],
        user_input={"expert_mode": True},
    )
    assert result["type"] == FlowResultType.CREATE_ENTRY
    assert result["data"]["expert_mode"] is True


# ---------------------------------------------------------------------------
# Test 7: Add PV array subentry
# ---------------------------------------------------------------------------


async def test_subentry_flow_add_array(hass: HomeAssistant) -> None:
    """Test adding a PV array subentry."""
    entry = MockConfigEntry(
        domain=DOMAIN,
        unique_id=DOMAIN,
        data={
            "name": "ZGC",
            CONF_GRID_IMPORT_SENSORS: ["sensor.grid_import"],
            "invert_sign": False,
        },
        options={},
    )
    entry.add_to_hass(hass)
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
            "response_speed": "normal",
            "setpoint_min": 0.0,
            "setpoint_max": 100.0,
        },
    )
    assert result["type"] == FlowResultType.CREATE_ENTRY
    assert result["title"] == "Roof South"
    assert result["data"][CONF_ARRAY_NAME] == "Roof South"
    assert result["data"][CONF_OUTPUT_TYPE] == OUTPUT_TYPE_PERCENT
    assert result["data"][CONF_RESPONSE_FACTOR] == 1.0


# ---------------------------------------------------------------------------
# Test 8: Reconfigure PV array subentry
# ---------------------------------------------------------------------------


async def test_subentry_reconfigure_array(hass: HomeAssistant) -> None:
    """Test reconfiguring an existing PV array subentry."""
    entry = MockConfigEntry(
        domain=DOMAIN,
        unique_id=DOMAIN,
        data={
            "name": "ZGC",
            CONF_GRID_IMPORT_SENSORS: ["sensor.grid_import"],
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
                    "response_factor": 1.0,
                    "settling_time_s": 15,
                    "setpoint_min": 0.0,
                    "setpoint_max": 100.0,
                    "w_per_unit": 10.0,
                    "calibration_confidence": "estimated",
                    "enabled": True,
                },
                "unique_id": None,
            }
        ],
    )
    entry.add_to_hass(hass)
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
            "response_speed": "fast",
            "setpoint_min": 10.0,
            "setpoint_max": 100.0,
            "_recalibrate": False,
        },
    )
    assert result["type"] == FlowResultType.ABORT
    assert result["reason"] == "reconfigure_successful"
    updated = entry.subentries[subentry_id]
    assert updated.data[CONF_ARRAY_NAME] == "Roof South Updated"
    assert updated.data[CONF_RESPONSE_FACTOR] == 2.0  # fast → 2.0


# ---------------------------------------------------------------------------
# Test 9: Add battery subentry
# ---------------------------------------------------------------------------


async def test_subentry_flow_add_battery(hass: HomeAssistant) -> None:
    """Test adding a battery subentry."""
    entry = MockConfigEntry(
        domain=DOMAIN,
        unique_id=DOMAIN,
        data={
            "name": "ZGC",
            CONF_GRID_IMPORT_SENSORS: ["sensor.grid_import"],
            "invert_sign": False,
        },
        options={},
    )
    entry.add_to_hass(hass)
    hass.states.async_set("sensor.battery_power", "0")

    result = await hass.config_entries.subentries.async_init(
        (entry.entry_id, BATTERY_SUBENTRY_TYPE),
        context={"source": config_entries.SOURCE_USER},
    )
    assert result["type"] == FlowResultType.FORM
    assert result["step_id"] == "user"

    result = await hass.config_entries.subentries.async_configure(
        result["flow_id"],
        user_input={
            "name": "Home Battery",
            "battery_sensor": "sensor.battery_power",
            "battery_max_charge_w": 3000.0,
            "battery_max_discharge_w": 3000.0,
            "battery_control_enabled": False,
            "response_speed": "cautious",
        },
    )
    assert result["type"] == FlowResultType.CREATE_ENTRY
    assert result["title"] == "Home Battery"
    assert result["data"]["battery_sensor"] == "sensor.battery_power"
    assert result["data"][CONF_RESPONSE_FACTOR] == 0.5  # cautious → 0.5


# ---------------------------------------------------------------------------
# Test 10: Array subentry with PV power entity
# ---------------------------------------------------------------------------


async def test_subentry_array_with_pv_entity(hass: HomeAssistant) -> None:
    """Array subentry stores pv_power_entity when provided."""
    entry = MockConfigEntry(
        domain=DOMAIN,
        unique_id=DOMAIN,
        data={
            "name": "ZGC",
            CONF_GRID_IMPORT_SENSORS: ["sensor.grid_import"],
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
            "response_speed": "normal",
            "setpoint_min": 0.0,
            "setpoint_max": 100.0,
            CONF_PV_POWER_ENTITY: "sensor.pv_power",
        },
    )
    assert result["type"] == FlowResultType.CREATE_ENTRY
    assert result["data"][CONF_PV_POWER_ENTITY] == "sensor.pv_power"


# ---------------------------------------------------------------------------
# Test 11: Mode guard with nonexistent entity → skip mode_mapping
# ---------------------------------------------------------------------------


async def test_wizard_mode_guard_nonexistent_entity(hass: HomeAssistant) -> None:
    """Mode guard with unknown entity → empty states list → skip mapping."""
    hass.states.async_set("sensor.grid_import", "0")

    result = await hass.config_entries.flow.async_init(
        DOMAIN, context={"source": config_entries.SOURCE_USER}
    )

    result = await hass.config_entries.flow.async_configure(
        result["flow_id"],
        user_input={
            "name": "Guard ZGC",
            CONF_GRID_IMPORT_SENSORS: ["sensor.grid_import"],
            "invert_sign": False,
        },
    )

    result = await hass.config_entries.flow.async_configure(
        result["flow_id"],
        user_input={
            "mode_guard_enabled": True,
            "mode_guard_entity": "input_select.nonexistent",
        },
    )
    # Entity not in hass.states → _mode_guard_states is empty → jump to done
    assert result["type"] == FlowResultType.CREATE_ENTRY
    assert result["data"]["mode_guard_enabled"] is True
