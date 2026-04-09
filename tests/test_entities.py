"""Tests for Zero Grid Controller entity classes."""

from __future__ import annotations

from unittest.mock import MagicMock

import pytest

from custom_components.zero_grid_controller.array import ArrayConfig
from custom_components.zero_grid_controller.binary_sensor import (
    ZGCBatteryClippingBinarySensor,
)
from custom_components.zero_grid_controller.const import (
    ARRAY_SUBENTRY_TYPE,
    CONF_KP,
    CONF_SETTLING_TIME_S,
    CONF_W_PER_UNIT,
    DEFAULT_KP,
    OUTPUT_TYPE_PERCENT,
    OUTPUT_TYPE_SWITCH,
    OUTPUT_TYPE_WATT,
)
from custom_components.zero_grid_controller.coordinator import ZGCResult
from custom_components.zero_grid_controller.number import (
    ZGCArraySettlingTimeNumber,
    ZGCArrayWPerUnitNumber,
    ZGCDeadbandNumber,
    ZGCEwmAlphaNumber,
    ZGCKdNumber,
    ZGCKiNumber,
    ZGCKpNumber,
    ZGCOutputMaxNumber,
)
from custom_components.zero_grid_controller.sensor import (
    ZGCArrayCalibrationSensor,
    ZGCArrayClippingSensor,
    ZGCArrayGainSensor,
    ZGCArraySetpointSensor,
    ZGCGridFilteredSensor,
    ZGCGridRawSensor,
    ZGCLearningSensor,
    ZGCModeSensor,
    ZGCPIDComponentSensor,
    ZGCPIDOutputSensor,
    ZGCStatusSensor,
)
from custom_components.zero_grid_controller.switch import ZGCArrayEnableSwitch


def _make_array(enabled: bool = True) -> ArrayConfig:
    """Create a minimal ArrayConfig for testing."""
    return ArrayConfig(
        name="test_array",
        enabled=enabled,
        output_type=OUTPUT_TYPE_PERCENT,
        setpoint_entity="number.inverter_limit",
        pv_power_entity=None,
        w_per_unit=10.0,
        calibration_confidence="estimated",
        setpoint_min=0.0,
        setpoint_max=100.0,
        settling_time_s=15,
    )


def _make_coordinator(
    array: ArrayConfig | None = None, data: ZGCResult | None = None
) -> MagicMock:
    """Create a mock coordinator."""
    coordinator = MagicMock()
    coordinator.get_array.return_value = array
    coordinator.data = data
    return coordinator


def _make_entry(options: dict | None = None, data: dict | None = None) -> MagicMock:
    """Create a mock config entry."""
    entry = MagicMock()
    entry.entry_id = "test_entry_id"
    entry.options = options or {}
    entry.data = data or {}
    return entry


# ---------------------------------------------------------------------------
# Test 1: ZGCArrayEnableSwitch.is_on — enabled=True
# ---------------------------------------------------------------------------


def test_zgc_array_enable_switch_is_on() -> None:
    """Test that the enable switch reports on when the array is enabled."""
    array = _make_array(enabled=True)
    coordinator = _make_coordinator(array=array)
    entry = _make_entry()
    device = MagicMock()
    switch = ZGCArrayEnableSwitch(
        coordinator, entry, device, "subentry_1", "test_array"
    )
    assert switch.is_on is True


# ---------------------------------------------------------------------------
# Test 2: ZGCArrayEnableSwitch.is_on — enabled=False
# ---------------------------------------------------------------------------


def test_zgc_array_enable_switch_is_off() -> None:
    """Test that the enable switch reports off when the array is disabled."""
    array = _make_array(enabled=False)
    coordinator = _make_coordinator(array=array)
    entry = _make_entry()
    device = MagicMock()
    switch = ZGCArrayEnableSwitch(
        coordinator, entry, device, "subentry_1", "test_array"
    )
    assert switch.is_on is False


# ---------------------------------------------------------------------------
# Test 3: ZGCKpNumber.native_value — kp in options
# ---------------------------------------------------------------------------


def test_zgc_number_native_value() -> None:
    """Test that ZGCKpNumber returns the kp value from options."""
    coordinator = _make_coordinator()
    entry = _make_entry(options={CONF_KP: 0.8})
    device = MagicMock()
    number = ZGCKpNumber(coordinator, entry, device)
    assert number.native_value == 0.8


# ---------------------------------------------------------------------------
# Test 4: ZGCKpNumber.native_value — fallback to default
# ---------------------------------------------------------------------------


def test_zgc_number_default_value() -> None:
    """Test that ZGCKpNumber returns DEFAULT_KP when not set in options."""
    coordinator = _make_coordinator()
    entry = _make_entry(options={}, data={})
    device = MagicMock()
    number = ZGCKpNumber(coordinator, entry, device)
    assert number.native_value == DEFAULT_KP


# ---------------------------------------------------------------------------
# Test 5: ZGCGridFilteredSensor.native_value — None when no data
# ---------------------------------------------------------------------------


def test_zgc_grid_filtered_sensor_none_when_no_data() -> None:
    """Test that grid filtered sensor returns None when coordinator.data is None."""
    coordinator = _make_coordinator(data=None)
    entry = _make_entry()
    device = MagicMock()
    sensor = ZGCGridFilteredSensor(coordinator, entry, device)
    assert sensor.native_value is None


# ---------------------------------------------------------------------------
# Test 6: ZGCModeSensor.native_value — returns mode string
# ---------------------------------------------------------------------------


def test_zgc_mode_sensor_returns_mode() -> None:
    """Test that the mode sensor returns the mode from coordinator data."""
    result = ZGCResult(
        grid_raw_w=100.0,
        grid_filtered_w=100.0,
        pid_output_w=0.0,
        pid_p_w=0.0,
        pid_i_w=0.0,
        pid_d_w=0.0,
        mode="active",
        status="active",
        battery_clipping=False,
        learning_status="Learning...",
    )
    coordinator = _make_coordinator(data=result)
    entry = _make_entry()
    device = MagicMock()
    sensor = ZGCModeSensor(coordinator, entry, device)
    assert sensor.native_value == "active"


def test_zgc_status_sensor_returns_status() -> None:
    """Test that the status sensor returns the status from coordinator data."""
    result = ZGCResult(
        grid_raw_w=100.0,
        grid_filtered_w=100.0,
        pid_output_w=0.0,
        pid_p_w=0.0,
        pid_i_w=0.0,
        pid_d_w=0.0,
        mode="active",
        status="deadband",
        battery_clipping=True,
        learning_status="Learning...",
    )
    coordinator = _make_coordinator(data=result)
    entry = _make_entry()
    device = MagicMock()
    sensor = ZGCStatusSensor(coordinator, entry, device)
    assert sensor.native_value == "deadband"


def test_zgc_battery_clipping_binary_sensor_is_on() -> None:
    """Battery clipping is exposed as a binary sensor."""
    result = ZGCResult(
        grid_raw_w=0.0,
        grid_filtered_w=0.0,
        pid_output_w=0.0,
        pid_p_w=0.0,
        pid_i_w=0.0,
        pid_d_w=0.0,
        mode="active",
        status="active",
        battery_clipping=True,
        learning_status="Learning...",
    )
    coordinator = _make_coordinator(data=result)
    entry = _make_entry()
    device = MagicMock()
    sensor = ZGCBatteryClippingBinarySensor(coordinator, entry, device)
    assert sensor.is_on is True


# ---------------------------------------------------------------------------
# array.py: ArrayConfig convenience methods
# ---------------------------------------------------------------------------


def _make_array_with(
    output_type=OUTPUT_TYPE_PERCENT, w_per_unit=10.0, enabled=True
) -> ArrayConfig:
    return ArrayConfig(
        name="test",
        enabled=enabled,
        output_type=output_type,
        setpoint_entity="number.sp",
        pv_power_entity=None,
        w_per_unit=w_per_unit,
        calibration_confidence="estimated",
        setpoint_min=0.0,
        setpoint_max=100.0,
        settling_time_s=15,
    )


def test_array_is_clipping_active_zero_setpoint() -> None:
    """is_clipping_active returns True when setpoint_w <= 0 (fully curtailed)."""
    array = _make_array_with()
    assert array.is_clipping_active(0.0, 0.0) is True
    assert array.is_clipping_active(-5.0, 0.0) is True


def test_array_setpoint_to_w() -> None:
    """setpoint_to_w converts a setpoint value to Watts."""
    array = _make_array_with(w_per_unit=23.0)
    assert array.setpoint_to_w(80.0) == pytest.approx(1840.0)


def test_array_w_to_setpoint_normal() -> None:
    """w_to_setpoint converts Watts to a setpoint delta."""
    array = _make_array_with(w_per_unit=10.0)
    assert array.w_to_setpoint(50.0) == pytest.approx(5.0)


def test_array_w_to_setpoint_zero_w_per_unit() -> None:
    """w_to_setpoint returns 0 when w_per_unit is 0 (no divide-by-zero)."""
    array = _make_array_with(w_per_unit=0.0)
    assert array.w_to_setpoint(50.0) == 0.0


# ---------------------------------------------------------------------------
# ZGCArrayEnableSwitch: async_turn_on / async_turn_off
# ---------------------------------------------------------------------------


async def test_switch_turn_on() -> None:
    """async_turn_on enables the array via coordinator."""
    array = _make_array(enabled=False)
    coordinator = _make_coordinator(array=array)
    coordinator.async_update_array_config = MagicMock()
    entry = _make_entry()
    device = MagicMock()
    switch = ZGCArrayEnableSwitch(
        coordinator, entry, device, "subentry_1", "test_array"
    )
    switch.hass = MagicMock()
    switch.async_write_ha_state = MagicMock()

    await switch.async_turn_on()

    coordinator.async_update_array_config.assert_called_once_with(
        "test_array", {"enabled": True}
    )
    switch.async_write_ha_state.assert_called_once()


async def test_switch_turn_off() -> None:
    """async_turn_off disables the array via coordinator."""
    array = _make_array(enabled=True)
    coordinator = _make_coordinator(array=array)
    coordinator.async_update_array_config = MagicMock()
    entry = _make_entry()
    device = MagicMock()
    switch = ZGCArrayEnableSwitch(
        coordinator, entry, device, "subentry_1", "test_array"
    )
    switch.hass = MagicMock()
    switch.async_write_ha_state = MagicMock()

    await switch.async_turn_off()

    coordinator.async_update_array_config.assert_called_once_with(
        "test_array", {"enabled": False}
    )
    switch.async_write_ha_state.assert_called_once()


def test_switch_is_on_no_array() -> None:
    """is_on returns True when get_array returns None (default safe state)."""
    coordinator = _make_coordinator(array=None)
    entry = _make_entry()
    device = MagicMock()
    switch = ZGCArrayEnableSwitch(
        coordinator, entry, device, "subentry_1", "missing_array"
    )
    assert switch.is_on is True


# ---------------------------------------------------------------------------
# Platform setup: sensor.py async_setup_entry with subentries
# ---------------------------------------------------------------------------


async def test_sensor_setup_entry_with_subentry() -> None:
    """Sensor platform creates per-array entities for subentries."""
    from custom_components.zero_grid_controller.sensor import (
        async_setup_entry as sensor_setup,
    )

    coordinator = MagicMock()
    coordinator.get_array.return_value = _make_array()
    device = MagicMock()
    array_device = MagicMock()

    runtime_data = MagicMock()
    runtime_data.coordinator = coordinator
    runtime_data.device = device
    runtime_data.array_devices = {"sub1": array_device}

    subentry = MagicMock()
    subentry.subentry_type = ARRAY_SUBENTRY_TYPE
    subentry.subentry_id = "sub1"
    subentry.data = {"array_name": "PV West"}

    entry = MagicMock()
    entry.runtime_data = runtime_data
    entry.entry_id = "test_entry"
    entry.subentries = {"sub1": subentry}

    entities_added = []
    await sensor_setup(None, entry, lambda e, **_: entities_added.extend(e))

    # 9 main + 4 per-array = 13 total
    assert len(entities_added) == 13


async def test_sensor_setup_entry_subentry_device_none() -> None:
    """Sensor platform skips subentries when device is not found."""
    from custom_components.zero_grid_controller.sensor import (
        async_setup_entry as sensor_setup,
    )

    coordinator = MagicMock()
    device = MagicMock()

    runtime_data = MagicMock()
    runtime_data.coordinator = coordinator
    runtime_data.device = device
    runtime_data.array_devices = {}  # no array device

    subentry = MagicMock()
    subentry.subentry_type = ARRAY_SUBENTRY_TYPE
    subentry.subentry_id = "sub1"
    subentry.data = {"array_name": "PV West"}

    entry = MagicMock()
    entry.runtime_data = runtime_data
    entry.entry_id = "test_entry"
    entry.subentries = {"sub1": subentry}

    entities_added = []
    await sensor_setup(None, entry, lambda e, **_: entities_added.extend(e))

    # Only 9 main entities (subentry skipped because no device)
    assert len(entities_added) == 9


# ---------------------------------------------------------------------------
# Platform setup: switch.py async_setup_entry with subentries
# ---------------------------------------------------------------------------


async def test_switch_setup_entry_with_subentry() -> None:
    """Switch platform creates enable/disable switch for each subentry."""
    from custom_components.zero_grid_controller.switch import (
        async_setup_entry as switch_setup,
    )

    coordinator = MagicMock()
    array_device = MagicMock()

    runtime_data = MagicMock()
    runtime_data.coordinator = coordinator
    runtime_data.array_devices = {"sub1": array_device}

    subentry = MagicMock()
    subentry.subentry_type = ARRAY_SUBENTRY_TYPE
    subentry.subentry_id = "sub1"
    subentry.data = {"array_name": "PV West"}

    entry = MagicMock()
    entry.runtime_data = runtime_data
    entry.entry_id = "test_entry"
    entry.subentries = {"sub1": subentry}

    entities_added = []
    await switch_setup(None, entry, lambda e, **_: entities_added.extend(e))

    assert len(entities_added) == 2  # master switch + array switch


# ---------------------------------------------------------------------------
# Platform setup: number.py async_setup_entry with subentries
# ---------------------------------------------------------------------------


async def test_number_setup_entry_with_subentry() -> None:
    """Number platform creates per-array number entities for subentries."""
    from custom_components.zero_grid_controller.number import (
        async_setup_entry as number_setup,
    )

    coordinator = MagicMock()
    device = MagicMock()
    array_device = MagicMock()

    runtime_data = MagicMock()
    runtime_data.coordinator = coordinator
    runtime_data.device = device
    runtime_data.array_devices = {"sub1": array_device}

    subentry = MagicMock()
    subentry.subentry_type = ARRAY_SUBENTRY_TYPE
    subentry.subentry_id = "sub1"
    subentry.data = {"array_name": "PV West"}

    entry = MagicMock()
    entry.runtime_data = runtime_data
    entry.entry_id = "test_entry"
    entry.subentries = {"sub1": subentry}

    entities_added = []
    await number_setup(None, entry, lambda e, **_: entities_added.extend(e))

    # 6 main + 2 per-array = 8 total
    assert len(entities_added) == 8


# ---------------------------------------------------------------------------
# ZGCNumberBase: entity_registry_enabled_default
# ---------------------------------------------------------------------------


def test_number_entity_registry_enabled_default_expert_mode() -> None:
    """Number entity is enabled by default in expert mode."""
    coordinator = _make_coordinator()
    entry = _make_entry(options={"expert_mode": True})
    device = MagicMock()
    number = ZGCKpNumber(coordinator, entry, device)
    assert number.entity_registry_enabled_default is True


def test_number_entity_registry_enabled_default_no_expert_mode() -> None:
    """Number entity is disabled by default when not in expert mode."""
    coordinator = _make_coordinator()
    entry = _make_entry(options={})
    device = MagicMock()
    number = ZGCKpNumber(coordinator, entry, device)
    assert number.entity_registry_enabled_default is False


# ---------------------------------------------------------------------------
# ZGCKpNumber: async_set_native_value and _on_value_changed
# ---------------------------------------------------------------------------


async def test_kp_number_set_value() -> None:
    """Setting Kp updates config entry options and calls set_gains."""
    coordinator = _make_coordinator()
    coordinator.pid = MagicMock()
    coordinator.pid.ki = 0.05
    coordinator.pid.kd = 0.0
    entry = _make_entry(options={})
    device = MagicMock()
    number = ZGCKpNumber(coordinator, entry, device)
    number.hass = MagicMock()
    number.async_write_ha_state = MagicMock()

    await number.async_set_native_value(0.8)

    number.hass.config_entries.async_update_entry.assert_called_once()
    coordinator.pid.set_gains.assert_called_once_with(0.8, 0.05, 0.0)


async def test_ki_number_on_value_changed() -> None:
    """ZGCKiNumber._on_value_changed calls set_gains on PID."""
    coordinator = _make_coordinator()
    coordinator.pid = MagicMock()
    coordinator.pid.kp = 0.5
    coordinator.pid.kd = 0.0
    entry = _make_entry()
    device = MagicMock()
    number = ZGCKiNumber(coordinator, entry, device)
    await number._on_value_changed(0.02)
    coordinator.pid.set_gains.assert_called_once_with(0.5, 0.02, 0.0)


async def test_kd_number_on_value_changed() -> None:
    """ZGCKdNumber._on_value_changed calls set_gains on PID."""
    coordinator = _make_coordinator()
    coordinator.pid = MagicMock()
    coordinator.pid.kp = 0.5
    coordinator.pid.ki = 0.05
    entry = _make_entry()
    device = MagicMock()
    number = ZGCKdNumber(coordinator, entry, device)
    await number._on_value_changed(0.01)
    coordinator.pid.set_gains.assert_called_once_with(0.5, 0.05, 0.01)


async def test_ewm_alpha_number_on_value_changed() -> None:
    """ZGCEwmAlphaNumber._on_value_changed updates EWM alpha on coordinator."""
    coordinator = _make_coordinator()
    coordinator.set_ewm_alpha = MagicMock()
    entry = _make_entry()
    device = MagicMock()
    number = ZGCEwmAlphaNumber(coordinator, entry, device)
    await number._on_value_changed(0.3)
    coordinator.set_ewm_alpha.assert_called_once_with(0.3)


async def test_deadband_number_on_value_changed() -> None:
    """ZGCDeadbandNumber._on_value_changed updates deadband on coordinator."""
    coordinator = _make_coordinator()
    coordinator.set_deadband = MagicMock()
    entry = _make_entry()
    device = MagicMock()
    number = ZGCDeadbandNumber(coordinator, entry, device)
    await number._on_value_changed(50.0)
    coordinator.set_deadband.assert_called_once_with(50.0)


async def test_output_max_number_on_value_changed() -> None:
    """ZGCOutputMaxNumber._on_value_changed updates output limits on PID."""
    coordinator = _make_coordinator()
    coordinator.pid = MagicMock()
    entry = _make_entry()
    device = MagicMock()
    number = ZGCOutputMaxNumber(coordinator, entry, device)
    await number._on_value_changed(5000.0)
    coordinator.pid.set_output_limits.assert_called_once_with(-5000.0, 5000.0)


async def test_array_settling_time_on_value_changed() -> None:
    """ZGCArraySettlingTimeNumber._on_value_changed updates settling_time_s."""
    coordinator = _make_coordinator()
    coordinator.apply_array_config_update = MagicMock()
    entry = _make_entry()
    device = MagicMock()
    number = ZGCArraySettlingTimeNumber(
        coordinator, entry, device, "subentry_1", "PV West"
    )
    await number._on_value_changed(30.0)
    coordinator.apply_array_config_update.assert_called_once_with(
        "PV West", {CONF_SETTLING_TIME_S: 30}
    )


async def test_array_w_per_unit_on_value_changed() -> None:
    """ZGCArrayWPerUnitNumber._on_value_changed updates w_per_unit."""
    coordinator = _make_coordinator()
    coordinator.apply_array_config_update = MagicMock()
    entry = _make_entry()
    device = MagicMock()
    number = ZGCArrayWPerUnitNumber(coordinator, entry, device, "subentry_1", "PV West")
    await number._on_value_changed(23.0)
    coordinator.apply_array_config_update.assert_called_once_with(
        "PV West", {CONF_W_PER_UNIT: 23.0}
    )


def test_array_switch_unique_id_uses_subentry_id() -> None:
    """Array switch identity should not depend on the mutable array name."""
    coordinator = _make_coordinator(array=_make_array(enabled=True))
    entry = _make_entry()
    device = MagicMock()

    switch_a = ZGCArrayEnableSwitch(coordinator, entry, device, "subentry_1", "PV West")
    switch_b = ZGCArrayEnableSwitch(coordinator, entry, device, "subentry_1", "PV East")

    assert switch_a.unique_id == switch_b.unique_id


def test_array_settling_time_number_reads_real_config_key() -> None:
    """Per-array numbers should read the actual subentry config key."""
    coordinator = _make_coordinator(array=_make_array())
    coordinator.get_array_subentry.return_value = MagicMock(
        data={CONF_SETTLING_TIME_S: 30}
    )
    entry = _make_entry()
    device = MagicMock()

    number = ZGCArraySettlingTimeNumber(
        coordinator, entry, device, "subentry_1", "PV West"
    )

    assert number.native_value == 30.0


# ---------------------------------------------------------------------------
# Sensor native_value tests — with data
# ---------------------------------------------------------------------------


def _make_result_with_arrays() -> ZGCResult:
    return ZGCResult(
        grid_raw_w=200.0,
        grid_filtered_w=195.0,
        pid_output_w=30.0,
        pid_p_w=15.0,
        pid_i_w=12.0,
        pid_d_w=3.0,
        mode="active",
        status="active",
        battery_clipping=True,
        learning_status="Calibrated ✓",
        setpoints={"PV West": 75.0},
        array_clipping={"PV West": True},
        array_gain_k={"PV West": 0.95},
        array_calibration={"PV West": "measured"},
    )


def test_grid_raw_sensor_with_data() -> None:
    """ZGCGridRawSensor returns grid_raw_w rounded to 1 decimal."""
    result = _make_result_with_arrays()
    coordinator = _make_coordinator(data=result)
    sensor = ZGCGridRawSensor(coordinator, _make_entry(), MagicMock())
    assert sensor.native_value == pytest.approx(200.0, abs=0.01)


def test_pid_output_sensor_with_data() -> None:
    """ZGCPIDOutputSensor returns pid_output_w rounded to 1 decimal."""
    result = _make_result_with_arrays()
    coordinator = _make_coordinator(data=result)
    sensor = ZGCPIDOutputSensor(coordinator, _make_entry(), MagicMock())
    assert sensor.native_value == pytest.approx(30.0, abs=0.01)


def test_pid_component_sensor_with_data() -> None:
    """ZGCPIDComponentSensor returns the requested PID component."""
    result = _make_result_with_arrays()
    coordinator = _make_coordinator(data=result)
    sensor = ZGCPIDComponentSensor(coordinator, _make_entry(), MagicMock(), "p")
    assert sensor.native_value == pytest.approx(15.0, abs=0.01)


def test_battery_clipping_sensor_on() -> None:
    """Battery clipping binary sensor is on when battery is clipping."""
    result = _make_result_with_arrays()
    coordinator = _make_coordinator(data=result)
    sensor = ZGCBatteryClippingBinarySensor(coordinator, _make_entry(), MagicMock())
    assert sensor.is_on is True


def test_battery_clipping_sensor_off() -> None:
    """Battery clipping binary sensor is off when battery is not clipping."""
    result = ZGCResult(
        grid_raw_w=0.0,
        grid_filtered_w=0.0,
        pid_output_w=0.0,
        pid_p_w=0.0,
        pid_i_w=0.0,
        pid_d_w=0.0,
        mode="active",
        status="active",
        battery_clipping=False,
        learning_status="Learning...",
    )
    coordinator = _make_coordinator(data=result)
    sensor = ZGCBatteryClippingBinarySensor(coordinator, _make_entry(), MagicMock())
    assert sensor.is_on is False


def test_battery_clipping_sensor_no_data() -> None:
    """Battery clipping binary sensor returns None when coordinator.data is None."""
    coordinator = _make_coordinator(data=None)
    sensor = ZGCBatteryClippingBinarySensor(coordinator, _make_entry(), MagicMock())
    assert sensor.is_on is None


def test_learning_sensor_with_data() -> None:
    """ZGCLearningSensor returns the learning_status string."""
    result = _make_result_with_arrays()
    coordinator = _make_coordinator(data=result)
    sensor = ZGCLearningSensor(coordinator, _make_entry(), MagicMock())
    assert sensor.native_value == "Calibrated ✓"


def test_learning_sensor_no_data() -> None:
    """ZGCLearningSensor returns None when coordinator.data is None."""
    coordinator = _make_coordinator(data=None)
    sensor = ZGCLearningSensor(coordinator, _make_entry(), MagicMock())
    assert sensor.native_value is None


# ---------------------------------------------------------------------------
# Array sensor tests
# ---------------------------------------------------------------------------


def test_array_setpoint_sensor_unit_percent() -> None:
    """ZGCArraySetpointSensor returns PERCENTAGE for percent output type."""
    from homeassistant.const import PERCENTAGE

    array = _make_array_with(output_type=OUTPUT_TYPE_PERCENT)
    coordinator = _make_coordinator(array=array)
    sensor = ZGCArraySetpointSensor(
        coordinator, _make_entry(), MagicMock(), "subentry_1", "test"
    )
    assert sensor.native_unit_of_measurement == PERCENTAGE


def test_array_setpoint_sensor_unit_watt() -> None:
    """ZGCArraySetpointSensor returns UnitOfPower.WATT for watt output type."""
    from homeassistant.const import UnitOfPower

    array = _make_array_with(output_type=OUTPUT_TYPE_WATT)
    coordinator = _make_coordinator(array=array)
    sensor = ZGCArraySetpointSensor(
        coordinator, _make_entry(), MagicMock(), "subentry_1", "test"
    )
    assert sensor.native_unit_of_measurement == UnitOfPower.WATT


def test_array_setpoint_sensor_unit_switch() -> None:
    """ZGCArraySetpointSensor returns None for switch output type."""
    array = _make_array_with(output_type=OUTPUT_TYPE_SWITCH)
    coordinator = _make_coordinator(array=array)
    sensor = ZGCArraySetpointSensor(
        coordinator, _make_entry(), MagicMock(), "subentry_1", "test"
    )
    assert sensor.native_unit_of_measurement is None


def test_array_setpoint_sensor_unit_no_array() -> None:
    """ZGCArraySetpointSensor returns None when array is not found."""
    coordinator = _make_coordinator(array=None)
    sensor = ZGCArraySetpointSensor(
        coordinator, _make_entry(), MagicMock(), "subentry_1", "test"
    )
    assert sensor.native_unit_of_measurement is None


def test_array_setpoint_sensor_with_data() -> None:
    """ZGCArraySetpointSensor returns rounded setpoint."""
    result = _make_result_with_arrays()
    array = _make_array_with()
    coordinator = _make_coordinator(array=array, data=result)
    sensor = ZGCArraySetpointSensor(
        coordinator, _make_entry(), MagicMock(), "subentry_1", "PV West"
    )
    assert sensor.native_value == pytest.approx(75.0, abs=0.01)


def test_array_setpoint_sensor_missing_setpoint() -> None:
    """ZGCArraySetpointSensor returns None when setpoint is not found."""
    result = _make_result_with_arrays()
    array = _make_array_with()
    coordinator = _make_coordinator(array=array, data=result)
    sensor = ZGCArraySetpointSensor(
        coordinator, _make_entry(), MagicMock(), "subentry_1", "PV Unknown"
    )
    assert sensor.native_value is None


def test_array_clipping_sensor_on() -> None:
    """ZGCArrayClippingSensor returns 'on' when clipping is active."""
    result = _make_result_with_arrays()
    coordinator = _make_coordinator(data=result)
    sensor = ZGCArrayClippingSensor(
        coordinator, _make_entry(), MagicMock(), "subentry_1", "PV West"
    )
    assert sensor.native_value == "on"


def test_array_clipping_sensor_no_data() -> None:
    """ZGCArrayClippingSensor returns None when coordinator.data is None."""
    coordinator = _make_coordinator(data=None)
    sensor = ZGCArrayClippingSensor(
        coordinator, _make_entry(), MagicMock(), "subentry_1", "PV West"
    )
    assert sensor.native_value is None


def test_array_gain_sensor_with_data() -> None:
    """ZGCArrayGainSensor returns rounded gain estimate."""
    result = _make_result_with_arrays()
    coordinator = _make_coordinator(data=result)
    sensor = ZGCArrayGainSensor(
        coordinator, _make_entry(), MagicMock(), "subentry_1", "PV West"
    )
    assert sensor.native_value == pytest.approx(0.95, abs=0.001)


def test_array_gain_sensor_none_gain() -> None:
    """ZGCArrayGainSensor returns None when gain is None (estimator not reliable)."""
    result = ZGCResult(
        grid_raw_w=0.0,
        grid_filtered_w=0.0,
        pid_output_w=0.0,
        pid_p_w=0.0,
        pid_i_w=0.0,
        pid_d_w=0.0,
        mode="active",
        status="active",
        battery_clipping=False,
        learning_status="Learning...",
        array_gain_k={"PV West": None},
    )
    coordinator = _make_coordinator(data=result)
    sensor = ZGCArrayGainSensor(
        coordinator, _make_entry(), MagicMock(), "subentry_1", "PV West"
    )
    assert sensor.native_value is None


def test_array_gain_sensor_no_data() -> None:
    """ZGCArrayGainSensor returns None when coordinator.data is None."""
    coordinator = _make_coordinator(data=None)
    sensor = ZGCArrayGainSensor(
        coordinator, _make_entry(), MagicMock(), "subentry_1", "PV West"
    )
    assert sensor.native_value is None


def test_array_calibration_sensor_with_data() -> None:
    """ZGCArrayCalibrationSensor returns the calibration confidence string."""
    result = _make_result_with_arrays()
    coordinator = _make_coordinator(data=result)
    sensor = ZGCArrayCalibrationSensor(
        coordinator, _make_entry(), MagicMock(), "subentry_1", "PV West"
    )
    assert sensor.native_value == "measured"


def test_array_calibration_sensor_no_data() -> None:
    """ZGCArrayCalibrationSensor returns None when coordinator.data is None."""
    coordinator = _make_coordinator(data=None)
    sensor = ZGCArrayCalibrationSensor(
        coordinator, _make_entry(), MagicMock(), "subentry_1", "PV West"
    )
    assert sensor.native_value is None


# ---------------------------------------------------------------------------
# Missing None-data branches for main sensors
# ---------------------------------------------------------------------------


def test_grid_raw_sensor_no_data() -> None:
    """ZGCGridRawSensor returns None when coordinator.data is None."""
    coordinator = _make_coordinator(data=None)
    sensor = ZGCGridRawSensor(coordinator, _make_entry(), MagicMock())
    assert sensor.native_value is None


def test_pid_output_sensor_no_data() -> None:
    """ZGCPIDOutputSensor returns None when coordinator.data is None."""
    coordinator = _make_coordinator(data=None)
    sensor = ZGCPIDOutputSensor(coordinator, _make_entry(), MagicMock())
    assert sensor.native_value is None


def test_pid_component_sensor_no_data() -> None:
    """ZGCPIDComponentSensor returns None when coordinator.data is None."""
    coordinator = _make_coordinator(data=None)
    sensor = ZGCPIDComponentSensor(coordinator, _make_entry(), MagicMock(), "i")
    assert sensor.native_value is None


def test_array_setpoint_sensor_no_data() -> None:
    """ZGCArraySetpointSensor returns None when coordinator.data is None."""
    coordinator = _make_coordinator(data=None)
    sensor = ZGCArraySetpointSensor(
        coordinator, _make_entry(), MagicMock(), "subentry_1", "PV West"
    )
    assert sensor.native_value is None


# ---------------------------------------------------------------------------
# Wrong-subentry-type skips in platform setup
# ---------------------------------------------------------------------------


async def test_sensor_setup_entry_skips_wrong_subentry_type() -> None:
    """Sensor platform skips subentries with non-ARRAY_SUBENTRY_TYPE."""
    from custom_components.zero_grid_controller.sensor import (
        async_setup_entry as sensor_setup,
    )

    coordinator = MagicMock()
    device = MagicMock()

    runtime_data = MagicMock()
    runtime_data.coordinator = coordinator
    runtime_data.device = device
    runtime_data.array_devices = {}

    subentry_wrong = MagicMock()
    subentry_wrong.subentry_type = "wrong_type"

    entry = MagicMock()
    entry.runtime_data = runtime_data
    entry.entry_id = "test_entry"
    entry.subentries = {"sub_wrong": subentry_wrong}

    entities_added = []
    await sensor_setup(None, entry, lambda e: entities_added.extend(e))

    # Only the 9 main entities, nothing for the wrong-type subentry
    assert len(entities_added) == 9


async def test_switch_setup_entry_skips_wrong_subentry_type() -> None:
    """Switch platform skips subentries with non-ARRAY_SUBENTRY_TYPE."""
    from custom_components.zero_grid_controller.switch import (
        async_setup_entry as switch_setup,
    )

    coordinator = MagicMock()
    runtime_data = MagicMock()
    runtime_data.coordinator = coordinator
    runtime_data.array_devices = {}

    subentry_wrong = MagicMock()
    subentry_wrong.subentry_type = "wrong_type"

    entry = MagicMock()
    entry.runtime_data = runtime_data
    entry.entry_id = "test_entry"
    entry.subentries = {"sub_wrong": subentry_wrong}

    entities_added = []
    await switch_setup(None, entry, lambda e: entities_added.extend(e))

    assert len(entities_added) == 1  # master switch always created


async def test_switch_setup_entry_skips_missing_device() -> None:
    """Switch platform skips subentries when device is not found in array_devices."""
    from custom_components.zero_grid_controller.switch import (
        async_setup_entry as switch_setup,
    )

    coordinator = MagicMock()
    runtime_data = MagicMock()
    runtime_data.coordinator = coordinator
    runtime_data.array_devices = {}  # no device for the subentry

    subentry = MagicMock()
    subentry.subentry_type = ARRAY_SUBENTRY_TYPE
    subentry.subentry_id = "sub1"
    subentry.data = {"array_name": "PV West"}

    entry = MagicMock()
    entry.runtime_data = runtime_data
    entry.entry_id = "test_entry"
    entry.subentries = {"sub1": subentry}

    entities_added = []
    await switch_setup(None, entry, lambda e: entities_added.extend(e))

    assert (
        len(entities_added) == 1
    )  # master switch always created, array skipped (no device)


async def test_number_setup_entry_skips_wrong_subentry_type() -> None:
    """Number platform skips subentries with non-ARRAY_SUBENTRY_TYPE."""
    from custom_components.zero_grid_controller.number import (
        async_setup_entry as number_setup,
    )

    coordinator = MagicMock()
    device = MagicMock()
    runtime_data = MagicMock()
    runtime_data.coordinator = coordinator
    runtime_data.device = device
    runtime_data.array_devices = {}

    subentry_wrong = MagicMock()
    subentry_wrong.subentry_type = "wrong_type"

    entry = MagicMock()
    entry.runtime_data = runtime_data
    entry.entry_id = "test_entry"
    entry.subentries = {"sub_wrong": subentry_wrong}

    entities_added = []
    await number_setup(None, entry, lambda e: entities_added.extend(e))

    # Only 6 main number entities
    assert len(entities_added) == 6


async def test_number_setup_entry_skips_missing_device() -> None:
    """Number platform skips subentries when device is not found."""
    from custom_components.zero_grid_controller.number import (
        async_setup_entry as number_setup,
    )

    coordinator = MagicMock()
    device = MagicMock()
    runtime_data = MagicMock()
    runtime_data.coordinator = coordinator
    runtime_data.device = device
    runtime_data.array_devices = {}  # no array device

    subentry = MagicMock()
    subentry.subentry_type = ARRAY_SUBENTRY_TYPE
    subentry.subentry_id = "sub1"
    subentry.data = {"array_name": "PV West"}

    entry = MagicMock()
    entry.runtime_data = runtime_data
    entry.entry_id = "test_entry"
    entry.subentries = {"sub1": subentry}

    entities_added = []
    await number_setup(None, entry, lambda e: entities_added.extend(e))

    assert len(entities_added) == 6
