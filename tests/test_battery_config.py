"""Tests for BatteryConfig dataclass."""

from __future__ import annotations

import pytest

from custom_components.zero_grid_controller.battery import (
    BatteryConfig,
    battery_config_from_subentry,
)


@pytest.fixture(autouse=True)
def auto_enable_custom_integrations(enable_custom_integrations):
    return


def test_battery_config_from_subentry_basic():
    data = {
        "name": "Main Battery",
        "battery_sensor": "sensor.battery_power",
        "battery_max_charge_w": 3000.0,
        "battery_max_discharge_w": 2500.0,
        "battery_setpoint_entity": "number.battery_setpoint",
    }
    cfg = battery_config_from_subentry("sub_abc", data)
    assert cfg.name == "Main Battery"
    assert cfg.sensor_entity == "sensor.battery_power"
    assert cfg.max_charge_w == 3000.0
    assert cfg.max_discharge_w == 2500.0
    assert cfg.setpoint_entity == "number.battery_setpoint"
    assert cfg.subentry_id == "sub_abc"


def test_battery_config_default_name():
    data = {
        "battery_sensor": "sensor.battery_power",
        "battery_max_charge_w": 5000.0,
        "battery_max_discharge_w": 5000.0,
        "battery_setpoint_entity": "number.battery_setpoint",
    }
    cfg = battery_config_from_subentry("abcdef12", data)
    assert "abcdef12" in cfg.name
