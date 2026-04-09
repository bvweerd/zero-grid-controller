"""Tests for the Zero Grid Controller config flow."""

from __future__ import annotations

from unittest.mock import AsyncMock, patch

import pytest
from homeassistant import config_entries
from homeassistant.core import HomeAssistant
from homeassistant.data_entry_flow import FlowResultType
from pytest_homeassistant_custom_component.common import MockConfigEntry

from custom_components.zero_grid_controller.const import (
    ARRAY_SUBENTRY_TYPE,
    BATTERY_SUBENTRY_TYPE,
    CONF_ARRAY_NAME,
    CONF_BATTERY_SETPOINT_ENTITY,
    CONF_CALIB_MAX_GRID_W,
    CONF_EXPERT_MODE,
    CONF_GRID_EXPORT_SENSORS,
    CONF_GRID_IMPORT_SENSORS,
    CONF_INVERTER_SPEED,
    CONF_MODE_GUARD_ENTITY,
    CONF_OUTPUT_TYPE,
    CONF_PV_POWER_ENTITY,
    CONF_RESPONSE_FACTOR,
    CONF_SENSOR_STALE_S,
    CONF_SETPOINT_ENTITY,
    DOMAIN,
    OUTPUT_TYPE_PERCENT,
    OUTPUT_TYPE_SWITCH,
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


async def test_wizard_mode_guard_requires_entity_when_enabled(
    hass: HomeAssistant,
) -> None:
    """Enabling the mode guard without an entity should stay on the same step."""
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
        user_input={"mode_guard_enabled": True},
    )
    assert result["type"] == FlowResultType.FORM
    assert result["step_id"] == "mode_guard"
    assert result["errors"][CONF_MODE_GUARD_ENTITY] == "required"


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


async def test_options_flow_preserves_existing_unknown_options(
    hass: HomeAssistant,
) -> None:
    """Options updates should merge with unrelated existing option keys."""
    entry = MockConfigEntry(
        domain=DOMAIN,
        unique_id=DOMAIN,
        data={
            "name": "ZGC",
            CONF_GRID_IMPORT_SENSORS: ["sensor.grid_import"],
            "invert_sign": False,
        },
        options={
            "retained_option": "keep-me",
            CONF_SENSOR_STALE_S: 25,
            CONF_CALIB_MAX_GRID_W: 2500.0,
        },
    )
    entry.add_to_hass(hass)

    result = await hass.config_entries.options.async_init(entry.entry_id)
    result = await hass.config_entries.options.async_configure(
        result["flow_id"],
        user_input={
            CONF_EXPERT_MODE: True,
            CONF_SENSOR_STALE_S: 30,
            CONF_CALIB_MAX_GRID_W: 3200.0,
        },
    )
    assert result["type"] == FlowResultType.CREATE_ENTRY
    assert result["data"]["retained_option"] == "keep-me"
    assert result["data"][CONF_EXPERT_MODE] is True
    assert result["data"][CONF_SENSOR_STALE_S] == 30
    assert result["data"][CONF_CALIB_MAX_GRID_W] == 3200.0


# ---------------------------------------------------------------------------
# Test 7: Add PV array subentry — multi-step (basics → setpoint_range)
# ---------------------------------------------------------------------------


async def test_subentry_flow_add_array(hass: HomeAssistant) -> None:
    """Test adding a PV array subentry via two-step flow."""
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

    # Step 1: basics
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
        },
    )
    # Step 2: setpoint range (routed because output_type != switch)
    assert result["type"] == FlowResultType.FORM
    assert result["step_id"] == "setpoint_range"

    result = await hass.config_entries.subentries.async_configure(
        result["flow_id"],
        user_input={
            "setpoint_min": 0.0,
            "setpoint_max": 100.0,
            CONF_INVERTER_SPEED: "normal",
            "response_speed": "normal",
        },
    )
    assert result["type"] == FlowResultType.CREATE_ENTRY
    assert result["title"] == "Roof South"
    assert result["data"][CONF_ARRAY_NAME] == "Roof South"
    assert result["data"][CONF_OUTPUT_TYPE] == OUTPUT_TYPE_PERCENT
    assert result["data"][CONF_RESPONSE_FACTOR] == 1.0


async def test_subentry_flow_duplicate_array_name_rejected(
    hass: HomeAssistant,
) -> None:
    """Array names must stay unique because runtime control references them."""
    entry = MockConfigEntry(
        domain=DOMAIN,
        unique_id=DOMAIN,
        data={
            "name": "ZGC",
            CONF_GRID_IMPORT_SENSORS: ["sensor.grid_import"],
            "invert_sign": False,
        },
        subentries_data=[
            {
                "subentry_type": ARRAY_SUBENTRY_TYPE,
                "title": "Roof South",
                "data": {
                    CONF_ARRAY_NAME: "Roof South",
                    CONF_SETPOINT_ENTITY: "number.inverter_limit_a",
                    CONF_OUTPUT_TYPE: OUTPUT_TYPE_PERCENT,
                },
                "unique_id": None,
            }
        ],
    )
    entry.add_to_hass(hass)

    result = await hass.config_entries.subentries.async_init(
        (entry.entry_id, ARRAY_SUBENTRY_TYPE),
        context={"source": config_entries.SOURCE_USER},
    )

    result = await hass.config_entries.subentries.async_configure(
        result["flow_id"],
        user_input={
            CONF_ARRAY_NAME: "Roof South",
            CONF_SETPOINT_ENTITY: "number.inverter_limit_b",
            CONF_OUTPUT_TYPE: OUTPUT_TYPE_PERCENT,
        },
    )
    assert result["type"] == FlowResultType.FORM
    assert result["errors"][CONF_ARRAY_NAME] == "duplicate_name"


async def test_subentry_reconfigure_array_duplicate_name_rejected(
    hass: HomeAssistant,
) -> None:
    """Reconfiguring an array should not allow colliding with another array name."""
    entry = MockConfigEntry(
        domain=DOMAIN,
        unique_id=DOMAIN,
        data={
            "name": "ZGC",
            CONF_GRID_IMPORT_SENSORS: ["sensor.grid_import"],
            "invert_sign": False,
        },
        subentries_data=[
            {
                "subentry_type": ARRAY_SUBENTRY_TYPE,
                "title": "Roof South",
                "data": {
                    CONF_ARRAY_NAME: "Roof South",
                    CONF_SETPOINT_ENTITY: "number.inverter_limit_a",
                    CONF_OUTPUT_TYPE: OUTPUT_TYPE_PERCENT,
                },
                "unique_id": None,
            },
            {
                "subentry_type": ARRAY_SUBENTRY_TYPE,
                "title": "Roof East",
                "data": {
                    CONF_ARRAY_NAME: "Roof East",
                    CONF_SETPOINT_ENTITY: "number.inverter_limit_b",
                    CONF_OUTPUT_TYPE: OUTPUT_TYPE_PERCENT,
                },
                "unique_id": None,
            },
        ],
    )
    entry.add_to_hass(hass)
    subentry_id = next(iter(entry.subentries))

    result = await hass.config_entries.subentries.async_init(
        (entry.entry_id, ARRAY_SUBENTRY_TYPE),
        context={
            "source": config_entries.SOURCE_RECONFIGURE,
            "subentry_id": subentry_id,
        },
    )
    result = await hass.config_entries.subentries.async_configure(
        result["flow_id"],
        user_input={
            CONF_ARRAY_NAME: "Roof East",
            CONF_SETPOINT_ENTITY: "number.inverter_limit_a",
            CONF_OUTPUT_TYPE: OUTPUT_TYPE_PERCENT,
        },
    )
    assert result["type"] == FlowResultType.FORM
    assert result["step_id"] == "reconfigure"
    assert result["errors"][CONF_ARRAY_NAME] == "duplicate_name"


# ---------------------------------------------------------------------------
# Test 8: Reconfigure PV array subentry — multi-step with recalibrate option
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

    # Step 1: reconfigure basics
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
        },
    )
    # Step 2: setpoint range
    assert result["type"] == FlowResultType.FORM
    assert result["step_id"] == "setpoint_range"

    result = await hass.config_entries.subentries.async_configure(
        result["flow_id"],
        user_input={
            "setpoint_min": 10.0,
            "setpoint_max": 100.0,
            CONF_INVERTER_SPEED: "fast",
            "response_speed": "fast",
        },
    )
    # Step 3: recalibrate option (reconfigure only)
    assert result["type"] == FlowResultType.FORM
    assert result["step_id"] == "recalibrate_option"

    result = await hass.config_entries.subentries.async_configure(
        result["flow_id"],
        user_input={"_recalibrate": False},
    )
    assert result["type"] == FlowResultType.ABORT
    assert result["reason"] == "reconfigure_successful"
    updated = entry.subentries[subentry_id]
    assert updated.data[CONF_ARRAY_NAME] == "Roof South Updated"
    assert updated.data[CONF_RESPONSE_FACTOR] == 2.0  # fast → 2.0


async def test_subentry_reconfigure_array_with_recalibrate_calls_service(
    hass: HomeAssistant,
) -> None:
    """Reconfigure with recalibrate enabled should call the recalibrate service."""
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
                },
                "unique_id": None,
            }
        ],
    )
    entry.add_to_hass(hass)
    subentry_id = next(iter(entry.subentries))
    with patch.object(type(hass.services), "async_call", new_callable=AsyncMock) as mock_async_call:
        result = await hass.config_entries.subentries.async_init(
            (entry.entry_id, ARRAY_SUBENTRY_TYPE),
            context={
                "source": config_entries.SOURCE_RECONFIGURE,
                "subentry_id": subentry_id,
            },
        )
        result = await hass.config_entries.subentries.async_configure(
            result["flow_id"],
            user_input={
                CONF_ARRAY_NAME: "Roof South Updated",
                CONF_SETPOINT_ENTITY: "number.inverter_limit",
                CONF_OUTPUT_TYPE: OUTPUT_TYPE_PERCENT,
            },
        )
        result = await hass.config_entries.subentries.async_configure(
            result["flow_id"],
            user_input={
                "setpoint_min": 0.0,
                "setpoint_max": 100.0,
                CONF_INVERTER_SPEED: "normal",
                "response_speed": "normal",
            },
        )
        result = await hass.config_entries.subentries.async_configure(
            result["flow_id"],
            user_input={"_recalibrate": True},
        )
        await hass.async_block_till_done()

    assert result["type"] == FlowResultType.ABORT
    assert result["reason"] == "reconfigure_successful"
    mock_async_call.assert_awaited_once_with(
        DOMAIN,
        "recalibrate",
        {"array_name": "Roof South Updated"},
        blocking=False,
    )


async def test_subentry_reconfigure_array_switch_output_requires_switch_entity(
    hass: HomeAssistant,
) -> None:
    """Reconfigure should reject a switch output backed by a number entity."""
    entry = MockConfigEntry(
        domain=DOMAIN,
        unique_id=DOMAIN,
        data={
            "name": "ZGC",
            CONF_GRID_IMPORT_SENSORS: ["sensor.grid_import"],
            "invert_sign": False,
        },
        subentries_data=[
            {
                "subentry_type": ARRAY_SUBENTRY_TYPE,
                "title": "Roof South",
                "data": {
                    CONF_ARRAY_NAME: "Roof South",
                    CONF_SETPOINT_ENTITY: "number.inverter_limit",
                    CONF_OUTPUT_TYPE: OUTPUT_TYPE_PERCENT,
                },
                "unique_id": None,
            }
        ],
    )
    entry.add_to_hass(hass)
    subentry_id = next(iter(entry.subentries))

    result = await hass.config_entries.subentries.async_init(
        (entry.entry_id, ARRAY_SUBENTRY_TYPE),
        context={
            "source": config_entries.SOURCE_RECONFIGURE,
            "subentry_id": subentry_id,
        },
    )
    result = await hass.config_entries.subentries.async_configure(
        result["flow_id"],
        user_input={
            CONF_ARRAY_NAME: "Roof South",
            CONF_SETPOINT_ENTITY: "number.inverter_limit",
            CONF_OUTPUT_TYPE: OUTPUT_TYPE_SWITCH,
        },
    )
    assert result["type"] == FlowResultType.FORM
    assert result["step_id"] == "reconfigure"
    assert result["errors"][CONF_SETPOINT_ENTITY] == "setpoint_entity_mismatch"


# ---------------------------------------------------------------------------
# Test 9: Add battery subentry — multi-step (basics → battery_control)
# ---------------------------------------------------------------------------


async def test_subentry_flow_add_battery(hass: HomeAssistant) -> None:
    """Test adding a battery subentry via two-step flow."""
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

    # Step 1: basics
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
        },
    )
    # Step 2: control settings
    assert result["type"] == FlowResultType.FORM
    assert result["step_id"] == "battery_control"

    result = await hass.config_entries.subentries.async_configure(
        result["flow_id"],
        user_input={
            "battery_control_enabled": False,
            "response_speed": "cautious",
        },
    )
    assert result["type"] == FlowResultType.CREATE_ENTRY
    assert result["title"] == "Home Battery"
    assert result["data"]["battery_sensor"] == "sensor.battery_power"
    assert result["data"][CONF_RESPONSE_FACTOR] == 0.5  # cautious → 0.5


# ---------------------------------------------------------------------------
# Test 9b: Battery control enabled without setpoint entity → validation error
# ---------------------------------------------------------------------------


async def test_subentry_battery_control_enabled_requires_setpoint(
    hass: HomeAssistant,
) -> None:
    """Battery control_enabled=True without setpoint entity returns an error."""
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
    result = await hass.config_entries.subentries.async_configure(
        result["flow_id"],
        user_input={
            "name": "Home Battery",
            "battery_sensor": "sensor.battery_power",
            "battery_max_charge_w": 3000.0,
            "battery_max_discharge_w": 3000.0,
        },
    )
    assert result["step_id"] == "battery_control"

    # Enable control without providing setpoint entity
    result = await hass.config_entries.subentries.async_configure(
        result["flow_id"],
        user_input={
            "battery_control_enabled": True,
            # battery_setpoint_entity intentionally absent
            "response_speed": "normal",
        },
    )
    assert result["type"] == FlowResultType.FORM
    assert result["step_id"] == "battery_control"
    assert "battery_setpoint_entity" in result["errors"]


async def test_subentry_flow_duplicate_battery_name_rejected(
    hass: HomeAssistant,
) -> None:
    """Battery names should remain unique across battery subentries."""
    entry = MockConfigEntry(
        domain=DOMAIN,
        unique_id=DOMAIN,
        data={
            "name": "ZGC",
            CONF_GRID_IMPORT_SENSORS: ["sensor.grid_import"],
            "invert_sign": False,
        },
        subentries_data=[
            {
                "subentry_type": BATTERY_SUBENTRY_TYPE,
                "title": "Home Battery",
                "data": {
                    "name": "Home Battery",
                    "battery_sensor": "sensor.battery_power_1",
                },
                "unique_id": None,
            }
        ],
    )
    entry.add_to_hass(hass)

    result = await hass.config_entries.subentries.async_init(
        (entry.entry_id, BATTERY_SUBENTRY_TYPE),
        context={"source": config_entries.SOURCE_USER},
    )
    result = await hass.config_entries.subentries.async_configure(
        result["flow_id"],
        user_input={
            "name": "Home Battery",
            "battery_sensor": "sensor.battery_power_2",
            "battery_max_charge_w": 3000.0,
            "battery_max_discharge_w": 3000.0,
        },
    )
    assert result["step_id"] == "battery_control"

    result = await hass.config_entries.subentries.async_configure(
        result["flow_id"],
        user_input={
            "battery_control_enabled": False,
            "response_speed": "normal",
        },
    )
    assert result["type"] == FlowResultType.FORM
    assert result["step_id"] == "battery_control"
    assert result["errors"]["name"] == "duplicate_name"


async def test_subentry_reconfigure_battery_duplicate_name_rejected(
    hass: HomeAssistant,
) -> None:
    """Battery reconfigure should reject colliding with another battery name."""
    entry = MockConfigEntry(
        domain=DOMAIN,
        unique_id=DOMAIN,
        data={
            "name": "ZGC",
            CONF_GRID_IMPORT_SENSORS: ["sensor.grid_import"],
            "invert_sign": False,
        },
        subentries_data=[
            {
                "subentry_type": BATTERY_SUBENTRY_TYPE,
                "title": "Home Battery",
                "data": {
                    "name": "Home Battery",
                    "battery_sensor": "sensor.battery_power_1",
                },
                "unique_id": None,
            },
            {
                "subentry_type": BATTERY_SUBENTRY_TYPE,
                "title": "Garage Battery",
                "data": {
                    "name": "Garage Battery",
                    "battery_sensor": "sensor.battery_power_2",
                },
                "unique_id": None,
            },
        ],
    )
    entry.add_to_hass(hass)
    subentry_id = next(iter(entry.subentries))

    result = await hass.config_entries.subentries.async_init(
        (entry.entry_id, BATTERY_SUBENTRY_TYPE),
        context={
            "source": config_entries.SOURCE_RECONFIGURE,
            "subentry_id": subentry_id,
        },
    )
    result = await hass.config_entries.subentries.async_configure(
        result["flow_id"],
        user_input={
            "name": "Garage Battery",
            "battery_sensor": "sensor.battery_power_1",
            "battery_max_charge_w": 3000.0,
            "battery_max_discharge_w": 3000.0,
        },
    )
    assert result["step_id"] == "battery_control"

    result = await hass.config_entries.subentries.async_configure(
        result["flow_id"],
        user_input={
            "battery_control_enabled": False,
            "response_speed": "normal",
        },
    )
    assert result["type"] == FlowResultType.FORM
    assert result["step_id"] == "battery_control"
    assert result["errors"]["name"] == "duplicate_name"


async def test_subentry_reconfigure_battery_enabled_requires_setpoint(
    hass: HomeAssistant,
) -> None:
    """Battery reconfigure should keep enforcing the setpoint requirement."""
    entry = MockConfigEntry(
        domain=DOMAIN,
        unique_id=DOMAIN,
        data={
            "name": "ZGC",
            CONF_GRID_IMPORT_SENSORS: ["sensor.grid_import"],
            "invert_sign": False,
        },
        subentries_data=[
            {
                "subentry_type": BATTERY_SUBENTRY_TYPE,
                "title": "Home Battery",
                "data": {
                    "name": "Home Battery",
                    "battery_sensor": "sensor.battery_power",
                    CONF_BATTERY_SETPOINT_ENTITY: "input_number.battery_limit",
                },
                "unique_id": None,
            }
        ],
    )
    entry.add_to_hass(hass)
    subentry_id = next(iter(entry.subentries))

    result = await hass.config_entries.subentries.async_init(
        (entry.entry_id, BATTERY_SUBENTRY_TYPE),
        context={
            "source": config_entries.SOURCE_RECONFIGURE,
            "subentry_id": subentry_id,
        },
    )
    result = await hass.config_entries.subentries.async_configure(
        result["flow_id"],
        user_input={
            "name": "Home Battery",
            "battery_sensor": "sensor.battery_power",
            "battery_max_charge_w": 3000.0,
            "battery_max_discharge_w": 3000.0,
        },
    )
    assert result["step_id"] == "battery_control"

    result = await hass.config_entries.subentries.async_configure(
        result["flow_id"],
        user_input={
            "battery_control_enabled": True,
            "response_speed": "normal",
        },
    )
    assert result["type"] == FlowResultType.FORM
    assert result["step_id"] == "battery_control"
    assert result["errors"][CONF_BATTERY_SETPOINT_ENTITY] == "setpoint_required"


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
    # Step 1: basics with pv_power_entity
    result = await hass.config_entries.subentries.async_configure(
        result["flow_id"],
        user_input={
            CONF_ARRAY_NAME: "Roof South",
            CONF_SETPOINT_ENTITY: "number.inverter_limit",
            CONF_OUTPUT_TYPE: OUTPUT_TYPE_PERCENT,
            CONF_PV_POWER_ENTITY: "sensor.pv_power",
        },
    )
    assert result["step_id"] == "setpoint_range"

    # Step 2: setpoint range
    result = await hass.config_entries.subentries.async_configure(
        result["flow_id"],
        user_input={
            "setpoint_min": 0.0,
            "setpoint_max": 100.0,
            CONF_INVERTER_SPEED: "normal",
            "response_speed": "normal",
        },
    )
    assert result["type"] == FlowResultType.CREATE_ENTRY
    assert result["data"][CONF_PV_POWER_ENTITY] == "sensor.pv_power"


async def test_subentry_array_switch_output_requires_switch_entity(
    hass: HomeAssistant,
) -> None:
    """Switch output type rejects a non-switch setpoint entity."""
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

    # Mismatch: output_type=switch but entity is number.* → error in step 1
    result = await hass.config_entries.subentries.async_configure(
        result["flow_id"],
        user_input={
            CONF_ARRAY_NAME: "Load Switch",
            CONF_SETPOINT_ENTITY: "number.inverter_limit",
            CONF_OUTPUT_TYPE: "switch",
        },
    )
    assert result["type"] == FlowResultType.FORM
    assert result["errors"][CONF_SETPOINT_ENTITY] == "setpoint_entity_mismatch"


async def test_subentry_array_numeric_output_requires_number_entity(
    hass: HomeAssistant,
) -> None:
    """Numeric output types reject switch entities."""
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
    hass.states.async_set("switch.inverter_enable", "on")

    result = await hass.config_entries.subentries.async_init(
        (entry.entry_id, ARRAY_SUBENTRY_TYPE),
        context={"source": config_entries.SOURCE_USER},
    )

    # Mismatch: output_type=percent but entity is switch.* → error in step 1
    result = await hass.config_entries.subentries.async_configure(
        result["flow_id"],
        user_input={
            CONF_ARRAY_NAME: "Roof South",
            CONF_SETPOINT_ENTITY: "switch.inverter_enable",
            CONF_OUTPUT_TYPE: OUTPUT_TYPE_PERCENT,
        },
    )
    assert result["type"] == FlowResultType.FORM
    assert result["errors"][CONF_SETPOINT_ENTITY] == "setpoint_entity_mismatch"


async def test_subentry_switch_array_stores_hysteresis_settings(
    hass: HomeAssistant,
) -> None:
    """Switch arrays persist their hysteresis thresholds and debounce settings."""
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
    hass.states.async_set("switch.inverter_enable", "off")

    result = await hass.config_entries.subentries.async_init(
        (entry.entry_id, ARRAY_SUBENTRY_TYPE),
        context={"source": config_entries.SOURCE_USER},
    )

    # Step 1: basics — output_type=switch routes to switch_params
    result = await hass.config_entries.subentries.async_configure(
        result["flow_id"],
        user_input={
            CONF_ARRAY_NAME: "PV Switch",
            CONF_SETPOINT_ENTITY: "switch.inverter_enable",
            CONF_OUTPUT_TYPE: "switch",
        },
    )
    assert result["type"] == FlowResultType.FORM
    assert result["step_id"] == "switch_params"

    # Step 2: switch_params
    result = await hass.config_entries.subentries.async_configure(
        result["flow_id"],
        user_input={
            "switch_on_threshold_w": 180.0,
            "switch_off_threshold_w": 70.0,
            "switch_debounce_s": 45,
            "response_speed": "normal",
        },
    )
    assert result["type"] == FlowResultType.CREATE_ENTRY
    assert result["data"]["switch_on_threshold_w"] == 180.0
    assert result["data"]["switch_off_threshold_w"] == 70.0
    assert result["data"]["switch_debounce_s"] == 45


async def test_subentry_array_rejects_invalid_setpoint_range(
    hass: HomeAssistant,
) -> None:
    """Numeric arrays should reject min values above max."""
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
        },
    )
    assert result["step_id"] == "setpoint_range"

    result = await hass.config_entries.subentries.async_configure(
        result["flow_id"],
        user_input={
            "setpoint_min": 100.0,
            "setpoint_max": 20.0,
            CONF_INVERTER_SPEED: "normal",
            "response_speed": "normal",
        },
    )
    assert result["type"] == FlowResultType.FORM
    assert result["step_id"] == "setpoint_range"
    assert result["errors"]["setpoint_max"] == "max_less_than_min"


async def test_subentry_switch_array_rejects_invalid_threshold_order(
    hass: HomeAssistant,
) -> None:
    """Switch arrays should require on-threshold above off-threshold."""
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

    result = await hass.config_entries.subentries.async_init(
        (entry.entry_id, ARRAY_SUBENTRY_TYPE),
        context={"source": config_entries.SOURCE_USER},
    )
    result = await hass.config_entries.subentries.async_configure(
        result["flow_id"],
        user_input={
            CONF_ARRAY_NAME: "PV Switch",
            CONF_SETPOINT_ENTITY: "switch.inverter_enable",
            CONF_OUTPUT_TYPE: OUTPUT_TYPE_SWITCH,
        },
    )
    assert result["step_id"] == "switch_params"

    result = await hass.config_entries.subentries.async_configure(
        result["flow_id"],
        user_input={
            "switch_on_threshold_w": 70.0,
            "switch_off_threshold_w": 70.0,
            "switch_debounce_s": 45,
            "response_speed": "normal",
        },
    )
    assert result["type"] == FlowResultType.FORM
    assert result["step_id"] == "switch_params"
    assert result["errors"]["switch_on_threshold_w"] == "must_exceed_off_threshold"


# ---------------------------------------------------------------------------
# Test 11: Mode guard with nonexistent entity → skip mode_mapping
# ---------------------------------------------------------------------------


async def test_wizard_mode_guard_nonexistent_entity(hass: HomeAssistant) -> None:
    """Mode guard with unknown entity should stay on the same step."""
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
    assert result["type"] == FlowResultType.FORM
    assert result["step_id"] == "mode_guard"
    assert result["errors"][CONF_MODE_GUARD_ENTITY] == "invalid_mode_guard_entity"
