"""Tests for ArrayConfig helpers."""

from __future__ import annotations

from custom_components.zero_grid_controller.array import (
    ArrayConfig,
    array_config_from_subentry,
)
from custom_components.zero_grid_controller.const import OUTPUT_TYPE_SWITCH


def test_array_headroom_and_max_power_for_numeric():
    array = ArrayConfig(
        name="Solar",
        output_type="percent",
        setpoint_entity="number.solar_limit",
        w_per_unit=12.0,
        calibration_confidence="estimated",
        setpoint_min=10.0,
        setpoint_max=90.0,
        settling_time_s=15,
    )

    assert array.max_power_w == 1080.0
    assert array.headroom_up_w(50.0) == 480.0
    assert array.headroom_down_w(50.0) == 480.0
    assert array.w_to_setpoint(120.0) == 10.0


def test_array_helpers_for_switch_and_zero_w_per_unit():
    array = ArrayConfig(
        name="Switch Solar",
        output_type=OUTPUT_TYPE_SWITCH,
        setpoint_entity="switch.solar",
        w_per_unit=0.0,
        calibration_confidence="estimated",
        setpoint_min=0.0,
        setpoint_max=1.0,
        settling_time_s=0,
        switch_on_threshold_w=150.0,
    )

    assert array.is_switch is True
    assert array.max_power_w == 150.0
    assert array.w_to_setpoint(500.0) == 0.0


def test_array_config_from_subentry_defaults_name_and_thresholds():
    cfg = array_config_from_subentry(
        "sub-123",
        {
            "setpoint_entity": "number.limit",
        },
    )

    assert cfg.name == "sub-123"
    assert cfg.output_type == "percent"
    assert cfg.setpoint_min == 0.0
    assert cfg.setpoint_max == 100.0
