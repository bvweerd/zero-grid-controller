"""Tests for ActuatorManager."""

from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock

import pytest
from homeassistant.exceptions import HomeAssistantError

from custom_components.zero_grid_controller.actuator_manager import ActuatorManager
from custom_components.zero_grid_controller.array import ArrayConfig
from custom_components.zero_grid_controller.battery import BatteryConfig
from custom_components.zero_grid_controller.const import (
    OUTPUT_TYPE_PERCENT,
    OUTPUT_TYPE_SWITCH,
)


@pytest.fixture(autouse=True)
def auto_enable_custom_integrations(enable_custom_integrations):
    return


def _make_array(
    name: str = "Array1", output_type: str = OUTPUT_TYPE_PERCENT
) -> ArrayConfig:
    entity = "switch.array" if output_type == OUTPUT_TYPE_SWITCH else "number.array_sp"
    return ArrayConfig(
        name=name,
        output_type=output_type,
        setpoint_entity=entity,
        w_per_unit=10.0,
        calibration_confidence="estimated",
        setpoint_min=0.0,
        setpoint_max=100.0,
        settling_time_s=15,
    )


def _make_battery(name: str = "Battery1") -> BatteryConfig:
    return BatteryConfig(
        subentry_id="sub1",
        name=name,
        sensor_entity="sensor.battery_power",
        max_charge_w=5000.0,
        max_discharge_w=5000.0,
        setpoint_entity="number.battery_sp",
    )


@pytest.fixture
def hass_mock():
    hass = MagicMock()
    hass.states.get.return_value = MagicMock(state="50.0")
    hass.services.async_call = AsyncMock()
    return hass


async def test_write_numeric_entity_calls_service(hass_mock):
    manager = ActuatorManager(hass_mock)
    await manager.write_numeric_entity("number.test", 42.0)
    hass_mock.services.async_call.assert_called_once()
    call_args = hass_mock.services.async_call.call_args[0]
    assert call_args[0] == "number"
    assert call_args[1] == "set_value"
    assert call_args[2]["value"] == 42.0


async def test_write_numeric_entity_input_number(hass_mock):
    manager = ActuatorManager(hass_mock)
    await manager.write_numeric_entity("input_number.test", 10.0)
    assert hass_mock.services.async_call.call_args[0][0] == "input_number"


async def test_write_numeric_entity_unsupported_domain(hass_mock):
    manager = ActuatorManager(hass_mock)
    with pytest.raises(ValueError, match="Unsupported"):
        await manager.write_numeric_entity("sensor.test", 10.0)


async def test_write_numeric_entity_raises_on_unavailable(hass_mock):
    hass_mock.states.get.return_value = MagicMock(state="unavailable")
    manager = ActuatorManager(hass_mock)
    with pytest.raises(HomeAssistantError, match="unavailable"):
        await manager.write_numeric_entity("number.test", 10.0)
    hass_mock.services.async_call.assert_not_called()


async def test_write_setpoint_numeric(hass_mock):
    manager = ActuatorManager(hass_mock)
    array = _make_array(output_type=OUTPUT_TYPE_PERCENT)
    await manager.write_setpoint(array, 80.0)
    hass_mock.services.async_call.assert_called_once()


async def test_write_setpoint_switch_turn_on(hass_mock):
    manager = ActuatorManager(hass_mock)
    array = _make_array(output_type=OUTPUT_TYPE_SWITCH)
    hass_mock.states.get.return_value = MagicMock(state="off")
    await manager.write_setpoint(array, 1.0)
    call = hass_mock.services.async_call.call_args[0]
    assert call[0] == "switch"
    assert call[1] == "turn_on"


async def test_write_setpoint_switch_turn_off(hass_mock):
    manager = ActuatorManager(hass_mock)
    array = _make_array(output_type=OUTPUT_TYPE_SWITCH)
    hass_mock.states.get.return_value = MagicMock(state="on")
    await manager.write_setpoint(array, 0.0)
    call = hass_mock.services.async_call.call_args[0]
    assert call[1] == "turn_off"


async def test_write_setpoint_switch_skips_unavailable(hass_mock):
    manager = ActuatorManager(hass_mock)
    array = _make_array(output_type=OUTPUT_TYPE_SWITCH)
    hass_mock.states.get.return_value = MagicMock(state="unknown")

    await manager.write_setpoint(array, 1.0)

    hass_mock.services.async_call.assert_not_called()


async def test_enter_safe_state_sets_arrays_to_max(hass_mock):
    manager = ActuatorManager(hass_mock)
    array = _make_array()
    current_setpoints: dict[str, float] = {"Array1": 50.0}
    await manager.enter_safe_state([array], [], current_setpoints)
    assert current_setpoints["Array1"] == 100.0


async def test_enter_safe_state_sets_battery_to_zero(hass_mock):
    manager = ActuatorManager(hass_mock)
    battery = _make_battery()
    await manager.enter_safe_state([], [battery], {})
    assert hass_mock.services.async_call.called
    call = hass_mock.services.async_call.call_args[0]
    assert call[2]["value"] == 0.0


async def test_enter_safe_state_skips_switch_arrays(hass_mock):
    manager = ActuatorManager(hass_mock)
    switch_array = _make_array(output_type=OUTPUT_TYPE_SWITCH)
    current_setpoints: dict[str, float] = {}
    await manager.enter_safe_state([switch_array], [], current_setpoints)
    hass_mock.services.async_call.assert_not_called()


async def test_enter_safe_state_logs_array_write_failure(hass_mock, caplog):
    manager = ActuatorManager(hass_mock)
    array = _make_array()
    manager.write_setpoint = AsyncMock(side_effect=RuntimeError("boom"))

    await manager.enter_safe_state([array], [], {})

    assert "Failed to set Array1 to safe state" in caplog.text


async def test_enter_safe_state_logs_battery_write_failure(hass_mock, caplog):
    manager = ActuatorManager(hass_mock)
    battery = _make_battery()
    manager.write_numeric_entity = AsyncMock(side_effect=RuntimeError("boom"))

    await manager.enter_safe_state([], [battery], {})

    assert "Failed to set battery Battery1 to 0" in caplog.text
