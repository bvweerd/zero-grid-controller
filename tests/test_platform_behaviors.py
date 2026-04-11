"""Tests for platform entities and setup helpers."""

from __future__ import annotations

from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from pytest_homeassistant_custom_component.common import MockConfigEntry

from custom_components.zero_grid_controller import ZGCData
from custom_components.zero_grid_controller.array import ArrayConfig
from custom_components.zero_grid_controller.button import (
    ZGCRecalibrateButton,
    ZGCResetPIDButton,
)
from custom_components.zero_grid_controller.const import (
    ARRAY_SUBENTRY_TYPE,
    BATTERY_SUBENTRY_TYPE,
    DOMAIN,
)
from custom_components.zero_grid_controller.coordinator import ZGCResult, ZeroGridCoordinator
from custom_components.zero_grid_controller.number import (
    ZGCDeadbandNumber,
    ZGCFilterAlphaNumber,
)
from custom_components.zero_grid_controller.sensor import (
    ZGCArraySetpointSensor,
    ZGCBatterySetpointSensor,
    ZGCGridFilteredSensor,
    ZGCGridRawSensor,
    ZGCPIDOutputSensor,
    ZGCStatusSensor,
    async_setup_entry as sensor_async_setup_entry,
)
from custom_components.zero_grid_controller.switch import ZGCEnableSwitch


@pytest.fixture(autouse=True)
def auto_enable_custom_integrations(enable_custom_integrations):
    return


def _entry_with_subentries():
    return MockConfigEntry(
        domain=DOMAIN,
        title="Zero Grid",
        data={
            "name": "Zero Grid",
            "grid_import_sensors": ["sensor.grid_import"],
            "grid_export_sensors": ["sensor.grid_export"],
        },
        options={},
        subentries_data=(
            {
                "subentry_id": "array-1",
                "subentry_type": ARRAY_SUBENTRY_TYPE,
                "title": "Solar",
                "data": {
                    "array_name": "Solar",
                    "output_type": "percent",
                    "setpoint_entity": "number.solar_limit",
                    "setpoint_min": 0.0,
                    "setpoint_max": 100.0,
                },
            },
            {
                "subentry_id": "battery-1",
                "subentry_type": BATTERY_SUBENTRY_TYPE,
                "title": "Battery",
                "data": {
                    "name": "Battery",
                    "battery_sensor": "sensor.battery_power",
                    "battery_max_charge_w": 4000.0,
                    "battery_max_discharge_w": 5000.0,
                    "battery_setpoint_entity": "number.battery_limit",
                },
            },
            {
                "subentry_id": "array-missing",
                "subentry_type": ARRAY_SUBENTRY_TYPE,
                "title": "Missing",
                "data": {
                    "array_name": "Missing",
                    "output_type": "percent",
                    "setpoint_entity": "number.missing_limit",
                    "setpoint_min": 0.0,
                    "setpoint_max": 100.0,
                },
            },
        ),
    )


def _make_coordinator(hass, entry):
    coordinator = ZeroGridCoordinator(hass, entry)
    coordinator.data = ZGCResult(
        grid_raw_w=123.4,
        grid_filtered_w=120.1,
        pid_output_w=-20.6,
        status="active",
        setpoints={"Solar": 42.345},
        battery_setpoints={"Battery": -250.44},
    )
    return coordinator


async def test_sensor_platform_setup_adds_main_and_subentry_entities(hass):
    entry = _entry_with_subentries()
    entry.add_to_hass(hass)
    coordinator = _make_coordinator(hass, entry)
    entry.runtime_data = ZGCData(
        coordinator=coordinator,
        device=SimpleNamespace(id="main"),
        array_devices={"array-1": SimpleNamespace(id="array")},
        battery_devices={"battery-1": SimpleNamespace(id="battery")},
    )

    added = []

    def async_add_entities(entities, config_subentry_id=None):
        added.append((entities, config_subentry_id))

    await sensor_async_setup_entry(hass, entry, async_add_entities)

    assert len(added[0][0]) == 4
    assert isinstance(added[0][0][0], ZGCGridRawSensor)
    assert added[1][1] == "array-1"
    assert isinstance(added[1][0][0], ZGCArraySetpointSensor)
    assert added[2][1] == "battery-1"
    assert isinstance(added[2][0][0], ZGCBatterySetpointSensor)


def test_sensor_entities_expose_native_values_and_none_branch(hass):
    entry = MockConfigEntry(domain=DOMAIN, title="Zero Grid", data={}, options={})
    coordinator = MagicMock()
    coordinator.data = ZGCResult(
        grid_raw_w=123.44,
        grid_filtered_w=120.11,
        pid_output_w=-20.66,
        status="active",
        setpoints={"Solar": 42.345},
        battery_setpoints={"Battery": -250.44},
    )
    device = SimpleNamespace(id="main")

    assert ZGCGridRawSensor(coordinator, entry, device).native_value == 123.4
    assert ZGCGridFilteredSensor(coordinator, entry, device).native_value == 120.1
    assert ZGCPIDOutputSensor(coordinator, entry, device).native_value == -20.7
    assert ZGCStatusSensor(coordinator, entry, device).native_value == "active"
    assert ZGCArraySetpointSensor(coordinator, entry, device, "array-1", "Solar").native_value == 42.34
    assert ZGCBatterySetpointSensor(coordinator, entry, device, "battery-1", "Battery").native_value == -250.4

    coordinator.data = None
    assert ZGCArraySetpointSensor(coordinator, entry, device, "array-1", "Solar").native_value is None
    assert ZGCBatterySetpointSensor(coordinator, entry, device, "battery-1", "Battery").native_value is None

    coordinator.data = ZGCResult(0.0, 0.0, 0.0, "idle", {}, {})
    assert ZGCArraySetpointSensor(coordinator, entry, device, "array-1", "Solar").native_value is None
    assert ZGCBatterySetpointSensor(coordinator, entry, device, "battery-1", "Battery").native_value is None


async def test_button_entities_trigger_pid_reset_and_calibration(hass):
    entry = MockConfigEntry(domain=DOMAIN, title="Zero Grid", data={}, options={})
    coordinator = MagicMock()
    coordinator._pid.reset = MagicMock()
    coordinator.arrays = [
        ArrayConfig(
            name="Solar",
            output_type="percent",
            setpoint_entity="number.solar_limit",
            w_per_unit=10.0,
            calibration_confidence="estimated",
            setpoint_min=0.0,
            setpoint_max=100.0,
            settling_time_s=15,
        )
    ]
    coordinator.start_calibration = AsyncMock(return_value=[])
    coordinator.hass = hass

    reset_button = ZGCResetPIDButton(coordinator, entry, SimpleNamespace())
    await reset_button.async_press()
    coordinator._pid.reset.assert_called_once()

    recalibrate_button = ZGCRecalibrateButton(coordinator, entry, SimpleNamespace())
    captured = {}

    def _capture_task(coro):
        captured["coro"] = coro
        coro.close()
        return MagicMock()

    with patch.object(hass, "async_create_task", side_effect=_capture_task) as mock_create_task:
        await recalibrate_button.async_press()
    mock_create_task.assert_called_once()


async def test_recalibrate_button_logs_when_no_arrays(hass, caplog):
    entry = MockConfigEntry(domain=DOMAIN, title="Zero Grid", data={}, options={})
    coordinator = MagicMock()
    coordinator.arrays = []
    coordinator.hass = hass

    button = ZGCRecalibrateButton(coordinator, entry, SimpleNamespace())
    await button.async_press()

    assert "No arrays to calibrate" in caplog.text


async def test_number_entities_persist_and_reload(hass):
    entry = MockConfigEntry(
        domain=DOMAIN,
        title="Zero Grid",
        data={"deadband_w": 20.0, "ewm_alpha": 0.3},
        options={},
    )
    coordinator = MagicMock()
    coordinator.reload_config = MagicMock()
    device = SimpleNamespace()

    deadband = ZGCDeadbandNumber(coordinator, entry, device)
    alpha = ZGCFilterAlphaNumber(coordinator, entry, device)
    deadband.hass = hass
    alpha.hass = hass
    deadband.async_write_ha_state = MagicMock()
    alpha.async_write_ha_state = MagicMock()

    with patch.object(hass.config_entries, "async_update_entry") as mock_update:
        await deadband.async_set_native_value(25.0)
        await alpha.async_set_native_value(0.6)

    assert deadband.native_value == 20.0
    assert alpha.native_value == 0.3
    assert mock_update.call_count == 2
    assert coordinator.reload_config.call_count == 2


async def test_enable_switch_updates_entry_option(hass):
    entry = MockConfigEntry(
        domain=DOMAIN,
        title="Zero Grid",
        data={},
        options={},
    )
    coordinator = MagicMock()
    coordinator._enabled = False
    entity = ZGCEnableSwitch(coordinator, entry, SimpleNamespace())
    entity.hass = hass
    entity.async_write_ha_state = MagicMock()

    with patch.object(hass.config_entries, "async_update_entry") as mock_update:
        await entity.async_turn_on()
        assert entity.is_on is True
        await entity.async_turn_off()
        assert entity.is_on is False

    assert mock_update.call_count == 2
