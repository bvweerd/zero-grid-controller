"""Unit tests for ActuatorManager."""

from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock

import pytest

from custom_components.zero_grid_controller.actuator_manager import ActuatorManager
from custom_components.zero_grid_controller.array import ArrayConfig
from custom_components.zero_grid_controller.battery import BatteryConfig
from custom_components.zero_grid_controller.const import (
    DEFAULT_BATTERY_SETTLING_TIME_S,
    OUTPUT_TYPE_PERCENT,
    OUTPUT_TYPE_SWITCH,
    OUTPUT_TYPE_WATT,
)
from custom_components.zero_grid_controller.utils import clamp


@pytest.fixture(autouse=True)
def auto_enable_custom_integrations(enable_custom_integrations):
    """Enable custom integrations for all tests in this module."""
    return


def _make_hass() -> MagicMock:
    hass = MagicMock()
    hass.services.async_call = AsyncMock()
    return hass


def _make_array(
    name: str = "South",
    output_type: str = OUTPUT_TYPE_PERCENT,
    setpoint_min: float = 0.0,
    setpoint_max: float = 100.0,
    w_per_unit: float = 10.0,
    settling_time_s: float = 15.0,
    switch_on_threshold_w: float = 100.0,
    switch_off_threshold_w: float = 50.0,
    switch_debounce_s: float = 30.0,
    response_factor: float = 1.0,
) -> ArrayConfig:
    return ArrayConfig(
        name=name,
        setpoint_entity="number.pv_limit" if output_type != OUTPUT_TYPE_SWITCH else "switch.pv",
        output_type=output_type,
        pv_power_entity=None,
        setpoint_min=setpoint_min,
        setpoint_max=setpoint_max,
        w_per_unit=w_per_unit,
        settling_time_s=settling_time_s,
        switch_on_threshold_w=switch_on_threshold_w,
        switch_off_threshold_w=switch_off_threshold_w,
        switch_debounce_s=switch_debounce_s,
        response_factor=response_factor,
        calibration_confidence="estimated",
        enabled=True,
    )


def _make_battery(
    name: str = "Home Battery",
    max_charge_w: float = 5000.0,
    max_discharge_w: float = 5000.0,
    control_enabled: bool = True,
    setpoint_entity: str | None = "number.battery_setpoint",
    settling_time_s: float = DEFAULT_BATTERY_SETTLING_TIME_S,
) -> BatteryConfig:
    return BatteryConfig(
        subentry_id="bsub1",
        name=name,
        sensor_entity="sensor.battery_power",
        max_charge_w=max_charge_w,
        max_discharge_w=max_discharge_w,
        control_enabled=control_enabled,
        setpoint_entity=setpoint_entity,
        settling_time_s=float(settling_time_s),
    )


# ---------------------------------------------------------------------------
# write_numeric_entity
# ---------------------------------------------------------------------------


async def test_write_numeric_entity_number_domain() -> None:
    """write_numeric_entity calls number.set_value for number.* entities."""
    hass = _make_hass()
    am = ActuatorManager(hass)
    await am.write_numeric_entity("number.pv_limit", 75.0)
    hass.services.async_call.assert_called_once_with(
        "number", "set_value", {"entity_id": "number.pv_limit", "value": 75.0}, blocking=True
    )


async def test_write_numeric_entity_input_number_domain() -> None:
    """write_numeric_entity calls input_number.set_value for input_number.* entities."""
    hass = _make_hass()
    am = ActuatorManager(hass)
    await am.write_numeric_entity("input_number.pv_limit", 50.0)
    hass.services.async_call.assert_called_once_with(
        "input_number", "set_value", {"entity_id": "input_number.pv_limit", "value": 50.0}, blocking=True
    )


async def test_write_numeric_entity_unsupported_domain_raises() -> None:
    """write_numeric_entity raises ValueError for unsupported domains."""
    hass = _make_hass()
    am = ActuatorManager(hass)
    with pytest.raises(ValueError, match="Unsupported"):
        await am.write_numeric_entity("sensor.something", 10.0)


# ---------------------------------------------------------------------------
# write_setpoint — number type
# ---------------------------------------------------------------------------


async def test_write_setpoint_number_type() -> None:
    """write_setpoint writes to a number entity for percent/watt arrays."""
    hass = _make_hass()
    am = ActuatorManager(hass)
    array = _make_array(output_type=OUTPUT_TYPE_PERCENT)
    await am.write_setpoint(array, 80.0)
    hass.services.async_call.assert_called_once_with(
        "number", "set_value", {"entity_id": "number.pv_limit", "value": 80.0}, blocking=True
    )


async def test_write_setpoint_watt_type() -> None:
    """write_setpoint delegates watt-type arrays to write_numeric_entity."""
    hass = _make_hass()
    am = ActuatorManager(hass)
    array = _make_array(output_type=OUTPUT_TYPE_WATT)
    await am.write_setpoint(array, 2500.0)
    hass.services.async_call.assert_called_once()
    _, kwargs = hass.services.async_call.call_args
    assert kwargs.get("blocking") is True


# ---------------------------------------------------------------------------
# write_setpoint — switch type
# ---------------------------------------------------------------------------


async def test_write_setpoint_switch_on() -> None:
    """write_setpoint sends turn_on for value > 0 on switch arrays."""
    hass = _make_hass()
    am = ActuatorManager(hass)
    array = _make_array(output_type=OUTPUT_TYPE_SWITCH)
    await am.write_setpoint(array, 1.0)
    hass.services.async_call.assert_called_once_with(
        "switch", "turn_on", {"entity_id": "switch.pv"}, blocking=True
    )


async def test_write_setpoint_switch_off() -> None:
    """write_setpoint sends turn_off for value == 0 on switch arrays."""
    hass = _make_hass()
    am = ActuatorManager(hass)
    array = _make_array(output_type=OUTPUT_TYPE_SWITCH)
    await am.write_setpoint(array, 0.0)
    hass.services.async_call.assert_called_once_with(
        "switch", "turn_off", {"entity_id": "switch.pv"}, blocking=True
    )


# ---------------------------------------------------------------------------
# enter_safe_state
# ---------------------------------------------------------------------------


async def test_enter_safe_state_sets_arrays_to_max() -> None:
    """enter_safe_state writes setpoint_max to all enabled arrays."""
    hass = _make_hass()
    am = ActuatorManager(hass)
    array = _make_array(setpoint_max=100.0)
    current_setpoints: dict[str, float] = {"South": 60.0}
    current_battery_setpoints: dict[str, float | None] = {}
    write_sp = AsyncMock()
    write_num = AsyncMock()

    await am.enter_safe_state(
        arrays=[array],
        batteries=[],
        current_setpoints=current_setpoints,
        current_battery_setpoints=current_battery_setpoints,
        write_setpoint=write_sp,
        write_numeric_entity=write_num,
    )

    write_sp.assert_called_once_with(array, 100.0)
    assert current_setpoints["South"] == 100.0


async def test_enter_safe_state_sets_battery_to_zero() -> None:
    """enter_safe_state writes 0 W to controlled batteries."""
    hass = _make_hass()
    am = ActuatorManager(hass)
    battery = _make_battery()
    current_battery_setpoints: dict[str, float | None] = {"Home Battery": 1000.0}
    write_num = AsyncMock()

    await am.enter_safe_state(
        arrays=[],
        batteries=[battery],
        current_setpoints={},
        current_battery_setpoints=current_battery_setpoints,
        write_setpoint=AsyncMock(),
        write_numeric_entity=write_num,
    )

    write_num.assert_called_once_with("number.battery_setpoint", 0.0)
    assert current_battery_setpoints["Home Battery"] == 0.0


async def test_enter_safe_state_skips_disabled_array() -> None:
    """enter_safe_state skips arrays that are not enabled."""
    hass = _make_hass()
    am = ActuatorManager(hass)
    array = _make_array()
    array.enabled = False
    write_sp = AsyncMock()

    await am.enter_safe_state(
        arrays=[array],
        batteries=[],
        current_setpoints={},
        current_battery_setpoints={},
        write_setpoint=write_sp,
        write_numeric_entity=AsyncMock(),
    )

    write_sp.assert_not_called()


# ---------------------------------------------------------------------------
# apply_battery_targets
# ---------------------------------------------------------------------------


async def test_apply_battery_targets_writes_new_setpoint() -> None:
    """apply_battery_targets writes a battery setpoint when threshold is exceeded."""
    hass = _make_hass()
    am = ActuatorManager(hass)
    battery = _make_battery(settling_time_s=3.0)
    targets = {"Home Battery": -2000.0}
    current_battery_setpoints: dict[str, float | None] = {"Home Battery": None}
    settling: dict[str, float] = {}
    verify_at: dict[str, tuple[float, float]] = {}
    write_num = AsyncMock()

    now = 1000.0
    await am.apply_battery_targets(
        targets=targets,
        now=now,
        batteries=[battery],
        current_battery_setpoints=current_battery_setpoints,
        battery_settling_until=settling,
        battery_verify_at=verify_at,
        write_numeric_entity=write_num,
    )

    write_num.assert_called_once_with("number.battery_setpoint", -2000.0)
    assert current_battery_setpoints["Home Battery"] == -2000.0
    assert settling["Home Battery"] == pytest.approx(now + battery.settling_time_s)
    assert "Home Battery" in verify_at


async def test_apply_battery_targets_skips_during_settling() -> None:
    """apply_battery_targets skips writes while the battery is still settling."""
    hass = _make_hass()
    am = ActuatorManager(hass)
    battery = _make_battery()
    targets = {"Home Battery": -3000.0}
    current_battery_setpoints: dict[str, float | None] = {"Home Battery": -2000.0}
    settling = {"Home Battery": 2000.0}  # settling_until > now
    verify_at: dict[str, tuple[float, float]] = {}
    write_num = AsyncMock()

    await am.apply_battery_targets(
        targets=targets,
        now=1000.0,
        batteries=[battery],
        current_battery_setpoints=current_battery_setpoints,
        battery_settling_until=settling,
        battery_verify_at=verify_at,
        write_numeric_entity=write_num,
    )

    write_num.assert_not_called()


async def test_apply_battery_targets_skips_below_threshold() -> None:
    """apply_battery_targets skips writes smaller than BATTERY_WRITE_THRESHOLD_W."""
    hass = _make_hass()
    am = ActuatorManager(hass)
    battery = _make_battery()
    current_battery_setpoints: dict[str, float | None] = {"Home Battery": -2000.0}
    targets = {"Home Battery": -2002.0}  # only 2 W delta, below 5 W threshold
    settling: dict[str, float] = {}
    verify_at: dict[str, tuple[float, float]] = {}
    write_num = AsyncMock()

    await am.apply_battery_targets(
        targets=targets,
        now=1000.0,
        batteries=[battery],
        current_battery_setpoints=current_battery_setpoints,
        battery_settling_until=settling,
        battery_verify_at=verify_at,
        write_numeric_entity=write_num,
    )

    write_num.assert_not_called()


# ---------------------------------------------------------------------------
# distribute_and_write
# ---------------------------------------------------------------------------


async def test_distribute_and_write_curtails_proportional_to_headroom() -> None:
    """distribute_and_write splits delta_w across arrays by headroom."""
    hass = _make_hass()
    am = ActuatorManager(hass)

    # Two arrays: one with 50 % headroom, one with 25 % headroom
    array_a = _make_array("A", setpoint_max=100.0, w_per_unit=10.0)
    array_b = _make_array("B", setpoint_max=100.0, w_per_unit=10.0)
    # Array A at 50 (50 % headroom down), array B at 75 (25 % headroom down)
    current_setpoints = {"A": 50.0, "B": 75.0}
    settling_until: dict[str, float] = {}
    overrides: dict[str, tuple[float, float]] = {}
    write_sp = AsyncMock()

    # delta_w = 300 W (import → need to curtail, i.e. reduce setpoint)
    await am.distribute_and_write(
        delta_w=300.0,
        now=0.0,
        mode="active",
        filtered_w=300.0,
        arrays=[array_a, array_b],
        current_setpoints=current_setpoints,
        settling_until=settling_until,
        override_setpoints=overrides,
        write_setpoint=write_sp,
        clamp_func=clamp,
    )

    # Both arrays should have been written
    assert write_sp.call_count == 2
    # Array A (more headroom) should receive a larger share
    new_a = current_setpoints["A"]
    new_b = current_setpoints["B"]
    assert new_a < 50.0  # curtailed
    assert new_b < 75.0  # curtailed


async def test_distribute_and_write_respects_settling(hass) -> None:
    """distribute_and_write skips arrays that are still settling."""
    hass = _make_hass()
    am = ActuatorManager(hass)
    array = _make_array("South")
    current_setpoints = {"South": 80.0}
    settling_until = {"South": 9999.0}  # settling until far in the future
    write_sp = AsyncMock()

    await am.distribute_and_write(
        delta_w=200.0,
        now=0.0,
        mode="active",
        filtered_w=200.0,
        arrays=[array],
        current_setpoints=current_setpoints,
        settling_until=settling_until,
        override_setpoints={},
        write_setpoint=write_sp,
        clamp_func=clamp,
    )

    write_sp.assert_not_called()


# ---------------------------------------------------------------------------
# apply_switch_hysteresis
# ---------------------------------------------------------------------------


async def test_apply_switch_hysteresis_turns_on_above_threshold() -> None:
    """apply_switch_hysteresis turns on the switch when grid import exceeds threshold."""
    hass = _make_hass()
    am = ActuatorManager(hass)
    array = _make_array(
        output_type=OUTPUT_TYPE_SWITCH,
        switch_on_threshold_w=100.0,
        switch_off_threshold_w=50.0,
    )
    current_setpoints = {"South": 0.0}  # currently off
    write_sp = AsyncMock()

    await am.apply_switch_hysteresis(
        now=0.0,
        mode="active",
        filtered_w=200.0,  # above on-threshold
        arrays=[array],
        current_setpoints=current_setpoints,
        settling_until={},
        override_setpoints={},
        write_setpoint=write_sp,
    )

    write_sp.assert_called_once_with(array, array.setpoint_max)


async def test_apply_switch_hysteresis_turns_off_below_threshold() -> None:
    """apply_switch_hysteresis turns off the switch when export exceeds threshold."""
    hass = _make_hass()
    am = ActuatorManager(hass)
    array = _make_array(
        output_type=OUTPUT_TYPE_SWITCH,
        switch_on_threshold_w=100.0,
        switch_off_threshold_w=50.0,
    )
    current_setpoints = {"South": array.setpoint_max}  # currently on
    write_sp = AsyncMock()

    await am.apply_switch_hysteresis(
        now=0.0,
        mode="active",
        filtered_w=-100.0,  # exporting: |−100| > off_threshold 50
        arrays=[array],
        current_setpoints=current_setpoints,
        settling_until={},
        override_setpoints={},
        write_setpoint=write_sp,
    )

    write_sp.assert_called_once_with(array, array.setpoint_min)


async def test_apply_switch_hysteresis_passive_mode_no_turn_on() -> None:
    """In passive mode the switch must not be turned on."""
    hass = _make_hass()
    am = ActuatorManager(hass)
    array = _make_array(output_type=OUTPUT_TYPE_SWITCH, switch_on_threshold_w=100.0)
    current_setpoints = {"South": 0.0}  # currently off
    write_sp = AsyncMock()

    await am.apply_switch_hysteresis(
        now=0.0,
        mode="passive",
        filtered_w=500.0,  # would normally trigger turn-on
        arrays=[array],
        current_setpoints=current_setpoints,
        settling_until={},
        override_setpoints={},
        write_setpoint=write_sp,
    )

    write_sp.assert_not_called()
