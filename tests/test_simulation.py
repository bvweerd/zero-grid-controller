"""End-to-end simulation tests for the control loop."""

from __future__ import annotations

import pytest
from pytest_homeassistant_custom_component.common import MockConfigEntry

from custom_components.zero_grid_controller.const import (
    DOMAIN,
    STATUS_ACTIVE,
    STATUS_DEADBAND,
    STATUS_DISABLED,
)
from custom_components.zero_grid_controller.coordinator import ZeroGridCoordinator


@pytest.fixture(autouse=True)
def auto_enable_custom_integrations(enable_custom_integrations):
    return


def _entry(kp=1.0, ki=0.0, kd=0.0, deadband_w=20.0, ewm_alpha=1.0):
    return MockConfigEntry(
        domain=DOMAIN,
        title="Test",
        data={
            "name": "Test ZGC",
            "grid_import_sensors": ["sensor.grid_import"],
            "grid_export_sensors": ["sensor.grid_export"],
            "kp": kp,
            "ki": ki,
            "kd": kd,
            "deadband_w": deadband_w,
            "ewm_alpha": ewm_alpha,
        },
        options={},
    )


async def test_pid_output_positive_on_import(hass):
    """Importing from grid → positive PID output (curtail PV)."""
    entry = _entry(kp=1.0, deadband_w=10.0, ewm_alpha=1.0)
    entry.add_to_hass(hass)
    coordinator = ZeroGridCoordinator(hass, entry)
    hass.states.async_set("sensor.grid_import", "100")
    hass.states.async_set("sensor.grid_export", "0")
    result = await coordinator._async_update_data()
    assert result.pid_output_w > 0


async def test_pid_output_negative_on_export(hass):
    """Exporting to grid → negative PID output (open PV limit)."""
    entry = _entry(kp=1.0, deadband_w=10.0, ewm_alpha=1.0)
    entry.add_to_hass(hass)
    coordinator = ZeroGridCoordinator(hass, entry)
    hass.states.async_set("sensor.grid_import", "0")
    hass.states.async_set("sensor.grid_export", "200")
    result = await coordinator._async_update_data()
    assert result.pid_output_w < 0


async def test_ewm_filter_smooths_readings(hass):
    """EWM filter gradually approaches target, not instant."""
    entry = _entry(kp=1.0, deadband_w=5.0, ewm_alpha=0.3)
    entry.add_to_hass(hass)
    coordinator = ZeroGridCoordinator(hass, entry)
    hass.states.async_set("sensor.grid_import", "200")
    hass.states.async_set("sensor.grid_export", "0")

    result1 = await coordinator._async_update_data()
    # First reading: filtered_w should be 200 (initialized to raw value)
    assert result1.grid_filtered_w == pytest.approx(200.0)

    hass.states.async_set("sensor.grid_import", "0")
    result2 = await coordinator._async_update_data()
    # With alpha=0.3: 0.3*0 + 0.7*200 = 140
    assert result2.grid_filtered_w == pytest.approx(140.0)


async def test_safe_state_on_grid_unavailable(hass):
    """When grid sensors become unavailable, controller disables."""
    entry = _entry()
    entry.add_to_hass(hass)
    coordinator = ZeroGridCoordinator(hass, entry)
    # No states set → unavailable
    result = await coordinator._async_update_data()
    assert result.status == STATUS_DISABLED
    assert result.pid_output_w == 0.0
