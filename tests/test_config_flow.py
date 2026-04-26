"""Tests for config flow."""

from __future__ import annotations

from types import SimpleNamespace

import pytest
from pytest_homeassistant_custom_component.common import MockConfigEntry

from custom_components.zero_grid_controller.const import (
    ARRAY_SUBENTRY_TYPE,
    BATTERY_SUBENTRY_TYPE,
    CONF_ARRAY_NAME,
    CONF_BATTERY_MAX_CHARGE_W,
    CONF_BATTERY_MAX_DISCHARGE_W,
    CONF_BATTERY_SENSOR,
    CONF_BATTERY_SETPOINT_ENTITY,
    CONF_GRID_EXPORT_SENSORS,
    CONF_GRID_IMPORT_SENSORS,
    CONF_NAME,
    CONF_OUTPUT_TYPE,
    CONF_POWER_SENSOR_ENTITY,
    CONF_SETPOINT_ENTITY,
    CONF_SETPOINT_MAX,
    CONF_SETPOINT_MIN,
    CONF_SWITCH_OFF_THRESHOLD_W,
    CONF_SWITCH_ON_THRESHOLD_W,
    DOMAIN,
    OUTPUT_TYPE_PERCENT,
    OUTPUT_TYPE_SWITCH,
)


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


def _base_entry(**kwargs):
    return MockConfigEntry(
        domain=DOMAIN,
        title="Zero Grid",
        data={
            CONF_NAME: "Zero Grid",
            CONF_GRID_IMPORT_SENSORS: ["sensor.grid_import"],
            CONF_GRID_EXPORT_SENSORS: ["sensor.grid_export"],
            **kwargs,
        },
        options={},
    )


def _array_subentry(
    *,
    subentry_id: str = "array-1",
    name: str = "Solar",
    output_type: str = OUTPUT_TYPE_PERCENT,
    extra: dict | None = None,
):
    data = {
        CONF_ARRAY_NAME: name,
        CONF_OUTPUT_TYPE: output_type,
        CONF_SETPOINT_ENTITY: "number.solar_limit",
        CONF_SETPOINT_MIN: 0.0,
        CONF_SETPOINT_MAX: 100.0,
    }
    if extra:
        data.update(extra)
    return {
        "subentry_id": subentry_id,
        "subentry_type": ARRAY_SUBENTRY_TYPE,
        "title": name,
        "data": data,
    }


def _battery_subentry(
    *,
    subentry_id: str = "battery-1",
    name: str = "Battery",
):
    return {
        "subentry_id": subentry_id,
        "subentry_type": BATTERY_SUBENTRY_TYPE,
        "title": name,
        "data": {
            CONF_NAME: name,
            CONF_BATTERY_SENSOR: "sensor.battery_power",
            CONF_BATTERY_MAX_CHARGE_W: 4000.0,
            CONF_BATTERY_MAX_DISCHARGE_W: 5000.0,
            CONF_BATTERY_SETPOINT_ENTITY: "number.battery_limit",
        },
    }


async def test_options_flow_updates_main_settings(hass):
    entry = _base_entry()
    entry.add_to_hass(hass)

    result = await hass.config_entries.options.async_init(entry.entry_id)
    assert result["type"] == "form"
    assert result["step_id"] == "init"

    result = await hass.config_entries.options.async_configure(
        result["flow_id"],
        {
            CONF_NAME: "Updated ZGC",
            CONF_GRID_IMPORT_SENSORS: ["sensor.import_a"],
            CONF_GRID_EXPORT_SENSORS: ["sensor.export_a"],
            "deadband_w": 35.0,
            "ewm_alpha": 0.5,
            "aggressiveness": "fast",
        },
    )
    assert result["type"] == "create_entry"
    assert result["data"][CONF_NAME] == "Updated ZGC"


async def test_array_subentry_add_numeric_flow(hass):
    from homeassistant import config_entries

    entry = _base_entry()
    entry.add_to_hass(hass)

    result = await hass.config_entries.subentries.async_init(
        (entry.entry_id, ARRAY_SUBENTRY_TYPE),
        context={"source": config_entries.SOURCE_USER},
    )
    assert result["type"] == "form"
    assert result["step_id"] == "user"

    result = await hass.config_entries.subentries.async_configure(
        result["flow_id"],
        {
            CONF_ARRAY_NAME: "Garage PV",
            CONF_OUTPUT_TYPE: OUTPUT_TYPE_PERCENT,
        },
    )
    assert result["type"] == "form"
    assert result["step_id"] == "numeric_params"

    result = await hass.config_entries.subentries.async_configure(
        result["flow_id"],
        {
            CONF_SETPOINT_ENTITY: "number.garage_limit",
            CONF_POWER_SENSOR_ENTITY: "sensor.garage_power",
            CONF_SETPOINT_MIN: 5.0,
            CONF_SETPOINT_MAX: 95.0,
        },
    )
    assert result["type"] == "create_entry"
    assert result["title"] == "Garage PV"
    assert result["data"][CONF_POWER_SENSOR_ENTITY] == "sensor.garage_power"
    assert result["data"]["w_per_unit"] == 10.0
    assert result["data"]["calibration_confidence"] == "estimated"


async def test_array_subentry_duplicate_name_rejected(hass):
    from homeassistant import config_entries

    entry = _base_entry()
    object.__setattr__(
        entry, "subentries", {"array-1": entry.subentries.get("array-1")}
    )
    object.__setattr__(entry, "subentries", {})
    entry = MockConfigEntry(
        domain=DOMAIN,
        title="Zero Grid",
        data=entry.data,
        options={},
        subentries_data=(_array_subentry(name="Solar"),),
    )
    entry.add_to_hass(hass)

    result = await hass.config_entries.subentries.async_init(
        (entry.entry_id, ARRAY_SUBENTRY_TYPE),
        context={"source": config_entries.SOURCE_USER},
    )
    result = await hass.config_entries.subentries.async_configure(
        result["flow_id"],
        {
            CONF_ARRAY_NAME: "Solar",
            CONF_OUTPUT_TYPE: OUTPUT_TYPE_PERCENT,
        },
    )
    assert result["type"] == "form"
    assert result["errors"][CONF_ARRAY_NAME] == "duplicate_name"


async def test_array_subentry_numeric_validation(hass):
    from homeassistant import config_entries

    entry = _base_entry()
    entry.add_to_hass(hass)

    result = await hass.config_entries.subentries.async_init(
        (entry.entry_id, ARRAY_SUBENTRY_TYPE),
        context={"source": config_entries.SOURCE_USER},
    )
    result = await hass.config_entries.subentries.async_configure(
        result["flow_id"],
        {
            CONF_ARRAY_NAME: "Solar",
            CONF_OUTPUT_TYPE: OUTPUT_TYPE_PERCENT,
        },
    )
    result = await hass.config_entries.subentries.async_configure(
        result["flow_id"],
        {
            CONF_SETPOINT_ENTITY: "number.solar_limit",
            CONF_POWER_SENSOR_ENTITY: "sensor.solar_power",
            CONF_SETPOINT_MIN: 80.0,
            CONF_SETPOINT_MAX: 20.0,
        },
    )
    assert result["type"] == "form"
    assert result["errors"][CONF_SETPOINT_MIN] == "min_gte_max"


async def test_array_subentry_switch_validation_and_reconfigure(hass):
    from homeassistant import config_entries

    entry = MockConfigEntry(
        domain=DOMAIN,
        title="Zero Grid",
        data=_base_entry().data,
        options={},
        subentries_data=(
            _array_subentry(
                subentry_id="array-1",
                name="Switch PV",
                output_type=OUTPUT_TYPE_SWITCH,
                extra={
                    CONF_SETPOINT_ENTITY: "switch.solar_array",
                    CONF_SWITCH_ON_THRESHOLD_W: 200.0,
                    CONF_SWITCH_OFF_THRESHOLD_W: 100.0,
                },
            ),
        ),
    )
    entry.add_to_hass(hass)

    result = await hass.config_entries.subentries.async_init(
        (entry.entry_id, ARRAY_SUBENTRY_TYPE),
        context={"source": config_entries.SOURCE_USER},
    )
    result = await hass.config_entries.subentries.async_configure(
        result["flow_id"],
        {
            CONF_ARRAY_NAME: "Switch PV 2",
            CONF_OUTPUT_TYPE: OUTPUT_TYPE_SWITCH,
        },
    )
    assert result["step_id"] == "switch_params"

    result = await hass.config_entries.subentries.async_configure(
        result["flow_id"],
        {
            CONF_SETPOINT_ENTITY: "switch.solar_array_2",
            CONF_SWITCH_ON_THRESHOLD_W: 100.0,
            CONF_SWITCH_OFF_THRESHOLD_W: 120.0,
        },
    )
    assert result["type"] == "form"
    assert result["errors"][CONF_SWITCH_OFF_THRESHOLD_W] == "off_gte_on"

    result = await hass.config_entries.subentries.async_init(
        (entry.entry_id, ARRAY_SUBENTRY_TYPE),
        context={
            "source": config_entries.SOURCE_RECONFIGURE,
            "subentry_id": "array-1",
        },
    )
    assert result["type"] == "form"
    assert result["step_id"] == "reconfigure"

    result = await hass.config_entries.subentries.async_configure(
        result["flow_id"],
        {
            CONF_ARRAY_NAME: "Switch PV",
            CONF_OUTPUT_TYPE: OUTPUT_TYPE_SWITCH,
        },
    )
    assert result["step_id"] == "switch_params"

    result = await hass.config_entries.subentries.async_configure(
        result["flow_id"],
        {
            CONF_SETPOINT_ENTITY: "switch.updated_array",
            CONF_SWITCH_ON_THRESHOLD_W: 250.0,
            CONF_SWITCH_OFF_THRESHOLD_W: 150.0,
        },
    )
    assert result["type"] == "abort"
    assert result["reason"] == "reconfigure_successful"
    assert (
        entry.subentries["array-1"].data[CONF_SETPOINT_ENTITY] == "switch.updated_array"
    )


async def test_battery_subentry_add_duplicate_and_reconfigure(hass):
    from homeassistant import config_entries

    entry = MockConfigEntry(
        domain=DOMAIN,
        title="Zero Grid",
        data=_base_entry().data,
        options={},
        subentries_data=(
            _battery_subentry(subentry_id="battery-1", name="Home Battery"),
        ),
    )
    entry.add_to_hass(hass)

    result = await hass.config_entries.subentries.async_init(
        (entry.entry_id, BATTERY_SUBENTRY_TYPE),
        context={"source": config_entries.SOURCE_USER},
    )
    result = await hass.config_entries.subentries.async_configure(
        result["flow_id"],
        {
            CONF_NAME: "Home Battery",
            CONF_BATTERY_SENSOR: "sensor.battery_power",
            CONF_BATTERY_MAX_CHARGE_W: 4000.0,
            CONF_BATTERY_MAX_DISCHARGE_W: 5000.0,
            CONF_BATTERY_SETPOINT_ENTITY: "number.battery_limit",
        },
    )
    assert result["type"] == "form"
    assert result["errors"][CONF_NAME] == "duplicate_name"

    result = await hass.config_entries.subentries.async_init(
        (entry.entry_id, BATTERY_SUBENTRY_TYPE),
        context={
            "source": config_entries.SOURCE_RECONFIGURE,
            "subentry_id": "battery-1",
        },
    )
    assert result["step_id"] == "reconfigure"

    result = await hass.config_entries.subentries.async_configure(
        result["flow_id"],
        {
            CONF_NAME: "Home Battery Updated",
            CONF_BATTERY_SENSOR: "sensor.battery_power",
            CONF_BATTERY_MAX_CHARGE_W: 4500.0,
            CONF_BATTERY_MAX_DISCHARGE_W: 5200.0,
            CONF_BATTERY_SETPOINT_ENTITY: "number.updated_battery_limit",
        },
    )
    assert result["type"] == "abort"
    assert result["reason"] == "reconfigure_successful"
    assert entry.subentries["battery-1"].data[CONF_NAME] == "Home Battery Updated"


def test_array_and_battery_name_helpers_ignore_current_id():
    from custom_components.zero_grid_controller.config_flow import (
        ArraySubEntryFlow,
        BatterySubEntryFlow,
    )

    array_flow = ArraySubEntryFlow()
    battery_flow = BatterySubEntryFlow()
    entry = SimpleNamespace(
        subentries={
            "array-1": SimpleNamespace(
                subentry_id="array-1",
                subentry_type=ARRAY_SUBENTRY_TYPE,
                data={CONF_ARRAY_NAME: "Solar"},
            ),
            "battery-1": SimpleNamespace(
                subentry_id="battery-1",
                subentry_type=BATTERY_SUBENTRY_TYPE,
                data={CONF_NAME: "Battery"},
            ),
        }
    )
    array_flow._get_entry = lambda: entry
    battery_flow._get_entry = lambda: entry

    assert array_flow._array_name_exists("Solar", current_id="array-1") is False
    assert battery_flow._name_exists("Battery", current_id="battery-1") is False
    assert array_flow._array_name_exists("Solar", current_id=None) is True
    assert battery_flow._name_exists("Battery", current_id=None) is True


# ---------------------------------------------------------------------------
# Edge-case tests added during review
# ---------------------------------------------------------------------------


async def test_array_subentry_empty_name_rejected(hass):
    """An empty (whitespace-only) array name must return name_required error."""
    from homeassistant import config_entries

    entry = _base_entry()
    entry.add_to_hass(hass)

    result = await hass.config_entries.subentries.async_init(
        (entry.entry_id, ARRAY_SUBENTRY_TYPE),
        context={"source": config_entries.SOURCE_USER},
    )
    result = await hass.config_entries.subentries.async_configure(
        result["flow_id"],
        {CONF_ARRAY_NAME: "   ", CONF_OUTPUT_TYPE: OUTPUT_TYPE_PERCENT},
    )
    assert result["type"] == "form"
    assert result["errors"].get(CONF_ARRAY_NAME) == "name_required"


async def test_battery_subentry_empty_name_rejected(hass):
    """An empty (whitespace-only) battery name must return name_required error."""
    from homeassistant import config_entries

    entry = _base_entry()
    entry.add_to_hass(hass)

    result = await hass.config_entries.subentries.async_init(
        (entry.entry_id, BATTERY_SUBENTRY_TYPE),
        context={"source": config_entries.SOURCE_USER},
    )
    result = await hass.config_entries.subentries.async_configure(
        result["flow_id"],
        {
            CONF_NAME: "   ",
            CONF_BATTERY_SENSOR: "sensor.battery_power",
            CONF_BATTERY_MAX_CHARGE_W: 3000.0,
            CONF_BATTERY_MAX_DISCHARGE_W: 5000.0,
            CONF_BATTERY_SETPOINT_ENTITY: "number.battery_limit",
        },
    )
    assert result["type"] == "form"
    assert result["errors"].get(CONF_NAME) == "name_required"


async def test_array_switch_off_threshold_gte_on_rejected(hass):
    """off_threshold >= on_threshold must return off_gte_on error."""
    from homeassistant import config_entries

    entry = _base_entry()
    entry.add_to_hass(hass)

    result = await hass.config_entries.subentries.async_init(
        (entry.entry_id, ARRAY_SUBENTRY_TYPE),
        context={"source": config_entries.SOURCE_USER},
    )
    result = await hass.config_entries.subentries.async_configure(
        result["flow_id"],
        {CONF_ARRAY_NAME: "Switch PV", CONF_OUTPUT_TYPE: OUTPUT_TYPE_SWITCH},
    )
    # off_threshold (200) >= on_threshold (100): invalid
    result = await hass.config_entries.subentries.async_configure(
        result["flow_id"],
        {
            CONF_SETPOINT_ENTITY: "switch.solar_array",
            CONF_SWITCH_ON_THRESHOLD_W: 100.0,
            CONF_SWITCH_OFF_THRESHOLD_W: 200.0,
        },
    )
    assert result["type"] == "form"
    assert result["errors"].get(CONF_SWITCH_OFF_THRESHOLD_W) == "off_gte_on"


async def test_array_reconfigure_keeps_same_name(hass):
    """Reconfiguring an array and keeping its own name must not raise duplicate_name."""
    from homeassistant import config_entries

    entry = MockConfigEntry(
        domain=DOMAIN,
        title="Zero Grid",
        data=_base_entry().data,
        options={},
        subentries_data=(_array_subentry(subentry_id="array-1", name="Solar"),),
    )
    entry.add_to_hass(hass)

    result = await hass.config_entries.subentries.async_init(
        (entry.entry_id, ARRAY_SUBENTRY_TYPE),
        context={
            "source": config_entries.SOURCE_RECONFIGURE,
            "subentry_id": "array-1",
        },
    )
    assert result["step_id"] == "reconfigure"

    # Submit with the same name "Solar" — should NOT get duplicate_name
    result = await hass.config_entries.subentries.async_configure(
        result["flow_id"],
        {CONF_ARRAY_NAME: "Solar", CONF_OUTPUT_TYPE: OUTPUT_TYPE_PERCENT},
    )
    # Should advance to params step, not show an error
    assert result["type"] in ("form", "create_entry", "abort")
    assert result.get("errors", {}).get(CONF_ARRAY_NAME) != "duplicate_name"


async def test_array_reconfigure_duplicate_name_rejected(hass):
    """Reconfiguring an array to an existing different array's name must fail."""
    from homeassistant import config_entries

    entry = MockConfigEntry(
        domain=DOMAIN,
        title="Zero Grid",
        data=_base_entry().data,
        options={},
        subentries_data=(
            _array_subentry(subentry_id="array-1", name="Solar"),
            _array_subentry(subentry_id="array-2", name="Garage"),
        ),
    )
    entry.add_to_hass(hass)

    result = await hass.config_entries.subentries.async_init(
        (entry.entry_id, ARRAY_SUBENTRY_TYPE),
        context={
            "source": config_entries.SOURCE_RECONFIGURE,
            "subentry_id": "array-1",
        },
    )
    # Try to rename "Solar" to "Garage" (already taken by array-2)
    result = await hass.config_entries.subentries.async_configure(
        result["flow_id"],
        {CONF_ARRAY_NAME: "Garage", CONF_OUTPUT_TYPE: OUTPUT_TYPE_PERCENT},
    )
    assert result["type"] == "form"
    assert result["errors"].get(CONF_ARRAY_NAME) == "duplicate_name"
