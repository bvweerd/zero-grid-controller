"""Tests for ControllerMode: zero_import, zero_export, maximize_export, maximize_import."""

from __future__ import annotations

from unittest.mock import AsyncMock, patch

import pytest
from pytest_homeassistant_custom_component.common import MockConfigEntry

from custom_components.zero_grid_controller.const import (
    ARRAY_SUBENTRY_TYPE,
    BATTERY_SUBENTRY_TYPE,
    DOMAIN,
    LOAD_SUBENTRY_TYPE,
    STATUS_ACTIVE,
    STATUS_DEADBAND,
    STATUS_DISABLED,
    ControllerMode,
    ControllerStatus,
)
from custom_components.zero_grid_controller.coordinator import ZeroGridCoordinator


@pytest.fixture(autouse=True)
def auto_enable_custom_integrations(enable_custom_integrations):
    return


_BASE_DATA = {
    "name": "Test ZGC",
    "grid_import_sensors": ["sensor.grid_import"],
    "grid_export_sensors": ["sensor.grid_export"],
    "deadband_w": 20.0,
    "ewm_alpha": 1.0,
    "kp": 1.0,
    "ki": 0.0,
    "kd": 0.0,
    "controller_enabled": True,
}

_ARRAY_DATA = {
    "array_name": "Roof",
    "output_type": "percent",
    "setpoint_entity": "number.inverter_limit",
    "setpoint_min": 10.0,
    "setpoint_max": 100.0,
    "w_per_unit": 50.0,
    "settling_time_s": 15,
}

_BATTERY_DATA = {
    "name": "Battery",
    "battery_sensor": "sensor.battery_power",
    "battery_max_charge_w": 2000.0,
    "battery_max_discharge_w": 2000.0,
    "battery_setpoint_entity": "number.battery_setpoint",
}

_SWITCH_LOAD_DATA = {
    "load_name": "Boiler",
    "load_type": "switch",
    "setpoint_entity": "switch.boiler",
    "load_power_w": 2000.0,
    "load_priority": 50,
}


def _make_entry(
    control_mode: str = "zero_grid",
    deadband_w: float = 20.0,
    ewm_alpha: float = 1.0,
    kp: float = 1.0,
    ki: float = 0.0,
    subentries_data=None,
):
    data = {
        **_BASE_DATA,
        "deadband_w": deadband_w,
        "ewm_alpha": ewm_alpha,
        "kp": kp,
        "ki": ki,
        "control_mode": control_mode,
    }
    return MockConfigEntry(
        domain=DOMAIN,
        title="Test",
        data=data,
        options={},
        subentries_data=subentries_data or (),
    )


def _set_state(hass, entity_id, value):
    hass.states.async_set(entity_id, str(value))


def _set_grid(hass, import_w: float, export_w: float) -> None:
    _set_state(hass, "sensor.grid_import", import_w)
    _set_state(hass, "sensor.grid_export", export_w)


# ---------------------------------------------------------------------------
# zero_grid regression
# ---------------------------------------------------------------------------


async def test_zero_grid_importing_is_active(hass):
    entry = _make_entry(control_mode="zero_grid")
    entry.add_to_hass(hass)
    coordinator = ZeroGridCoordinator(hass, entry)
    _set_grid(hass, 200, 0)
    result = await coordinator._async_update_data()
    assert result.status == STATUS_ACTIVE


async def test_zero_grid_exporting_is_active(hass):
    entry = _make_entry(control_mode="zero_grid")
    entry.add_to_hass(hass)
    coordinator = ZeroGridCoordinator(hass, entry)
    _set_grid(hass, 0, 200)
    result = await coordinator._async_update_data()
    assert result.status == STATUS_ACTIVE


# ---------------------------------------------------------------------------
# zero_import mode
# ---------------------------------------------------------------------------


async def test_zero_import_importing_is_active(hass):
    """When importing (grid > 0), zero_import mode should control normally."""
    entry = _make_entry(control_mode="zero_import")
    entry.add_to_hass(hass)
    coordinator = ZeroGridCoordinator(hass, entry)
    _set_grid(hass, 200, 0)
    result = await coordinator._async_update_data()
    assert result.status == STATUS_ACTIVE
    assert result.pid_output_w > 0


async def test_zero_import_exporting_is_idle(hass):
    """When exporting (grid < 0), zero_import reports the allowed-side status.

    The PID still runs so surplus can be routed into controllable loads,
    but with no loads configured nothing is actuated.
    """
    entry = _make_entry(control_mode="zero_import")
    entry.add_to_hass(hass)
    coordinator = ZeroGridCoordinator(hass, entry)
    _set_grid(hass, 0, 200)
    result = await coordinator._async_update_data()
    assert result.status == ControllerStatus.IDLE_EXPORT_OK
    assert result.setpoints == {}


async def test_zero_import_within_deadband_is_deadband(hass):
    entry = _make_entry(control_mode="zero_import", deadband_w=20.0)
    entry.add_to_hass(hass)
    coordinator = ZeroGridCoordinator(hass, entry)
    _set_grid(hass, 10, 0)
    result = await coordinator._async_update_data()
    assert result.status == STATUS_DEADBAND


async def test_zero_import_idle_freezes_integrator(hass):
    """Integrator must not accumulate when exporting in zero_import mode."""
    entry = _make_entry(control_mode="zero_import", ki=0.1)
    entry.add_to_hass(hass)
    coordinator = ZeroGridCoordinator(hass, entry)
    _set_grid(hass, 0, 200)
    await coordinator._async_update_data()
    await coordinator._async_update_data()
    assert coordinator._engine.pid.integral == 0.0


async def test_zero_import_exporting_charges_battery(hass):
    """Battery charge layer must still run when idle in zero_import mode."""
    entry = _make_entry(
        control_mode="zero_import",
        subentries_data=(
            {
                "subentry_id": "bat1",
                "subentry_type": BATTERY_SUBENTRY_TYPE,
                "title": "Battery",
                "data": _BATTERY_DATA,
            },
        ),
    )
    entry.add_to_hass(hass)
    _set_grid(hass, 0, 500)
    _set_state(hass, "sensor.battery_power", 0)
    _set_state(hass, "number.battery_setpoint", 0)

    written: list[tuple[str, float]] = []

    async def _mock_write_numeric(entity_id: str, value: float) -> None:
        written.append((entity_id, value))

    coordinator = ZeroGridCoordinator(hass, entry)
    coordinator._actuators.write_numeric_entity = _mock_write_numeric

    await coordinator._async_update_data()

    battery_writes = [v for eid, v in written if eid == "number.battery_setpoint"]
    assert battery_writes, (
        "Battery charge setpoint should be written in zero_import idle"
    )
    assert battery_writes[-1] < 0, "Charge setpoint must be negative"


# ---------------------------------------------------------------------------
# zero_export mode
# ---------------------------------------------------------------------------


async def test_zero_export_exporting_is_active(hass):
    """When exporting (grid < 0), zero_export mode should control normally."""
    entry = _make_entry(control_mode="zero_export")
    entry.add_to_hass(hass)
    coordinator = ZeroGridCoordinator(hass, entry)
    _set_grid(hass, 0, 200)
    result = await coordinator._async_update_data()
    assert result.status == STATUS_ACTIVE
    assert result.pid_output_w < 0


async def test_zero_export_importing_is_idle(hass):
    """When importing (grid > 0), zero_export reports the allowed-side status.

    The PID still runs so curtailed PV can be reopened, but with no arrays
    configured nothing is actuated.
    """
    entry = _make_entry(control_mode="zero_export")
    entry.add_to_hass(hass)
    coordinator = ZeroGridCoordinator(hass, entry)
    _set_grid(hass, 200, 0)
    result = await coordinator._async_update_data()
    assert result.status == ControllerStatus.IDLE_IMPORT_OK
    assert result.load_setpoints == {}


async def test_zero_export_within_deadband_is_deadband(hass):
    entry = _make_entry(control_mode="zero_export", deadband_w=20.0)
    entry.add_to_hass(hass)
    coordinator = ZeroGridCoordinator(hass, entry)
    _set_grid(hass, 0, 10)
    result = await coordinator._async_update_data()
    assert result.status == STATUS_DEADBAND


async def test_zero_export_idle_freezes_integrator(hass):
    """Integrator must not accumulate when importing in zero_export mode."""
    entry = _make_entry(control_mode="zero_export", ki=0.1)
    entry.add_to_hass(hass)
    coordinator = ZeroGridCoordinator(hass, entry)
    _set_grid(hass, 200, 0)
    await coordinator._async_update_data()
    await coordinator._async_update_data()
    assert coordinator._engine.pid.integral == 0.0


# ---------------------------------------------------------------------------
# maximize_export mode
# ---------------------------------------------------------------------------


async def test_maximize_export_status(hass):
    entry = _make_entry(control_mode="maximize_export")
    entry.add_to_hass(hass)
    coordinator = ZeroGridCoordinator(hass, entry)
    _set_grid(hass, 0, 0)
    result = await coordinator._async_update_data()
    assert result.status == ControllerStatus.MAXIMIZING_EXPORT
    assert result.pid_output_w == 0.0


async def test_maximize_export_sets_array_to_max(hass):
    """All numeric arrays should be written to setpoint_max."""
    entry = _make_entry(
        control_mode="maximize_export",
        subentries_data=(
            {
                "subentry_id": "arr1",
                "subentry_type": ARRAY_SUBENTRY_TYPE,
                "title": "Roof",
                "data": _ARRAY_DATA,
            },
        ),
    )
    entry.add_to_hass(hass)
    _set_grid(hass, 0, 0)
    _set_state(hass, "number.inverter_limit", 50)

    written: list[tuple[str, float]] = []

    async def _mock_write_setpoint(array_cfg, value: float) -> None:
        written.append((array_cfg.setpoint_entity, value))

    coordinator = ZeroGridCoordinator(hass, entry)
    coordinator._actuators.write_setpoint = _mock_write_setpoint

    await coordinator._async_update_data()

    assert any(eid == "number.inverter_limit" and v == 100.0 for eid, v in written), (
        f"Array should be written to setpoint_max=100. Got: {written}"
    )


async def test_maximize_export_turns_load_off(hass):
    """Switch loads should be turned off in maximize_export mode."""
    entry = _make_entry(
        control_mode="maximize_export",
        subentries_data=(
            {
                "subentry_id": "ld1",
                "subentry_type": LOAD_SUBENTRY_TYPE,
                "title": "Boiler",
                "data": _SWITCH_LOAD_DATA,
            },
        ),
    )
    entry.add_to_hass(hass)
    _set_grid(hass, 0, 0)
    _set_state(hass, "switch.boiler", "on")

    switch_writes: list[tuple[str, bool]] = []

    async def _mock_write_switch(entity_id: str, state: bool) -> None:
        switch_writes.append((entity_id, state))

    coordinator = ZeroGridCoordinator(hass, entry)
    coordinator._actuators.write_switch_entity = _mock_write_switch

    await coordinator._async_update_data()

    assert any(
        eid == "switch.boiler" and state is False for eid, state in switch_writes
    ), f"Switch load should be turned off in maximize_export. Got: {switch_writes}"


async def test_maximize_export_resets_pid(hass):
    """PID integral must be zero after running in maximize_export mode."""
    entry = _make_entry(control_mode="maximize_export", ki=0.5)
    entry.add_to_hass(hass)
    coordinator = ZeroGridCoordinator(hass, entry)
    coordinator._engine.pid.set_integral(999.0)
    _set_grid(hass, 100, 0)
    await coordinator._async_update_data()
    assert coordinator._engine.pid.integral == 0.0


async def test_maximize_export_discharges_battery(hass):
    """Battery should be discharged at max_discharge_w in maximize_export mode."""
    entry = _make_entry(
        control_mode="maximize_export",
        subentries_data=(
            {
                "subentry_id": "bat1",
                "subentry_type": BATTERY_SUBENTRY_TYPE,
                "title": "Battery",
                "data": _BATTERY_DATA,
            },
        ),
    )
    entry.add_to_hass(hass)
    _set_grid(hass, 0, 0)
    _set_state(hass, "sensor.battery_power", 0)
    _set_state(hass, "number.battery_setpoint", 0)

    written: list[tuple[str, float]] = []

    async def _mock_write_numeric(entity_id: str, value: float) -> None:
        written.append((entity_id, value))

    coordinator = ZeroGridCoordinator(hass, entry)
    coordinator._actuators.write_numeric_entity = _mock_write_numeric

    await coordinator._async_update_data()

    battery_writes = [v for eid, v in written if eid == "number.battery_setpoint"]
    assert battery_writes, "Battery setpoint should be written in maximize_export"
    assert battery_writes[-1] == 2000.0, (
        f"Battery should discharge at max_discharge_w=2000. Got: {battery_writes}"
    )


# ---------------------------------------------------------------------------
# maximize_import mode
# ---------------------------------------------------------------------------


async def test_maximize_import_status(hass):
    entry = _make_entry(control_mode="maximize_import")
    entry.add_to_hass(hass)
    coordinator = ZeroGridCoordinator(hass, entry)
    _set_grid(hass, 0, 0)
    result = await coordinator._async_update_data()
    assert result.status == ControllerStatus.MAXIMIZING_IMPORT
    assert result.pid_output_w == 0.0


async def test_maximize_import_sets_array_to_min(hass):
    """All numeric arrays should be written to setpoint_min."""
    entry = _make_entry(
        control_mode="maximize_import",
        subentries_data=(
            {
                "subentry_id": "arr1",
                "subentry_type": ARRAY_SUBENTRY_TYPE,
                "title": "Roof",
                "data": _ARRAY_DATA,
            },
        ),
    )
    entry.add_to_hass(hass)
    _set_grid(hass, 0, 0)
    _set_state(hass, "number.inverter_limit", 100)

    written: list[tuple[str, float]] = []

    async def _mock_write_setpoint(array_cfg, value: float) -> None:
        written.append((array_cfg.setpoint_entity, value))

    coordinator = ZeroGridCoordinator(hass, entry)
    coordinator._actuators.write_setpoint = _mock_write_setpoint

    await coordinator._async_update_data()

    assert any(eid == "number.inverter_limit" and v == 10.0 for eid, v in written), (
        f"Array should be written to setpoint_min=10. Got: {written}"
    )


async def test_maximize_import_turns_load_on(hass):
    """Switch loads should be turned on in maximize_import mode."""
    entry = _make_entry(
        control_mode="maximize_import",
        subentries_data=(
            {
                "subentry_id": "ld1",
                "subentry_type": LOAD_SUBENTRY_TYPE,
                "title": "Boiler",
                "data": _SWITCH_LOAD_DATA,
            },
        ),
    )
    entry.add_to_hass(hass)
    _set_grid(hass, 0, 0)
    _set_state(hass, "switch.boiler", "off")

    switch_writes: list[tuple[str, bool]] = []

    async def _mock_write_switch(entity_id: str, state: bool) -> None:
        switch_writes.append((entity_id, state))

    coordinator = ZeroGridCoordinator(hass, entry)
    coordinator._actuators.write_switch_entity = _mock_write_switch

    await coordinator._async_update_data()

    assert any(
        eid == "switch.boiler" and state is True for eid, state in switch_writes
    ), f"Switch load should be turned on in maximize_import. Got: {switch_writes}"


async def test_maximize_import_charges_battery(hass):
    """Battery should be charged at max_charge_w (negative) in maximize_import mode."""
    entry = _make_entry(
        control_mode="maximize_import",
        subentries_data=(
            {
                "subentry_id": "bat1",
                "subentry_type": BATTERY_SUBENTRY_TYPE,
                "title": "Battery",
                "data": _BATTERY_DATA,
            },
        ),
    )
    entry.add_to_hass(hass)
    _set_grid(hass, 0, 0)
    _set_state(hass, "sensor.battery_power", 0)
    _set_state(hass, "number.battery_setpoint", 0)

    written: list[tuple[str, float]] = []

    async def _mock_write_numeric(entity_id: str, value: float) -> None:
        written.append((entity_id, value))

    coordinator = ZeroGridCoordinator(hass, entry)
    coordinator._actuators.write_numeric_entity = _mock_write_numeric

    await coordinator._async_update_data()

    battery_writes = [v for eid, v in written if eid == "number.battery_setpoint"]
    assert battery_writes, "Battery setpoint should be written in maximize_import"
    assert battery_writes[-1] == -2000.0, (
        f"Battery should charge at -max_charge_w=-2000. Got: {battery_writes}"
    )


# ---------------------------------------------------------------------------
# Mode persistence through reload
# ---------------------------------------------------------------------------


async def test_mode_persists_after_reload(hass):
    """Mode should survive a coordinator.reload_config() call."""
    entry = _make_entry(control_mode="zero_import")
    entry.add_to_hass(hass)
    coordinator = ZeroGridCoordinator(hass, entry)
    assert coordinator._mode == ControllerMode.ZERO_IMPORT
    assert coordinator._engine._mode == ControllerMode.ZERO_IMPORT

    coordinator.reload_config()
    assert coordinator._mode == ControllerMode.ZERO_IMPORT
    assert coordinator._engine._mode == ControllerMode.ZERO_IMPORT


async def test_mode_default_is_zero_grid(hass):
    """Missing control_mode key should default to zero_grid."""
    data = {**_BASE_DATA}  # no "control_mode" key
    entry = MockConfigEntry(domain=DOMAIN, title="Test", data=data, options={})
    entry.add_to_hass(hass)
    coordinator = ZeroGridCoordinator(hass, entry)
    assert coordinator._mode == ControllerMode.ZERO_GRID


# ---------------------------------------------------------------------------
# Non-happy flow tests
# ---------------------------------------------------------------------------


async def test_invalid_control_mode_falls_back_to_zero_grid(hass):
    """An unrecognised control_mode value should silently fall back to zero_grid."""
    data = {**_BASE_DATA, "control_mode": "totally_invalid_mode"}
    entry = MockConfigEntry(domain=DOMAIN, title="Test", data=data, options={})
    entry.add_to_hass(hass)
    coordinator = ZeroGridCoordinator(hass, entry)
    assert coordinator._mode == ControllerMode.ZERO_GRID


async def test_maximize_export_grid_unavailable_enters_safe_state(hass):
    """Grid unavailable must override maximize_export — safe state returned."""
    entry = _make_entry(control_mode="maximize_export")
    entry.add_to_hass(hass)
    coordinator = ZeroGridCoordinator(hass, entry)
    # Do NOT set grid sensor states → unavailable
    result = await coordinator._async_update_data()
    assert result.status == STATUS_DISABLED


async def test_maximize_import_grid_unavailable_enters_safe_state(hass):
    """Grid unavailable must override maximize_import — safe state returned."""
    entry = _make_entry(control_mode="maximize_import")
    entry.add_to_hass(hass)
    coordinator = ZeroGridCoordinator(hass, entry)
    result = await coordinator._async_update_data()
    assert result.status == STATUS_DISABLED


async def test_maximize_export_controller_disabled_enters_safe_state(hass):
    """When controller_enabled=False, maximize_export must not run actuators."""
    data = {
        **_BASE_DATA,
        "control_mode": "maximize_export",
        "controller_enabled": False,
    }
    entry = MockConfigEntry(domain=DOMAIN, title="Test", data=data, options={})
    entry.add_to_hass(hass)
    coordinator = ZeroGridCoordinator(hass, entry)
    _set_grid(hass, 0, 0)
    result = await coordinator._async_update_data()
    assert result.status == STATUS_DISABLED


async def test_maximize_import_controller_disabled_enters_safe_state(hass):
    """When controller_enabled=False, maximize_import must not run actuators."""
    data = {
        **_BASE_DATA,
        "control_mode": "maximize_import",
        "controller_enabled": False,
    }
    entry = MockConfigEntry(domain=DOMAIN, title="Test", data=data, options={})
    entry.add_to_hass(hass)
    coordinator = ZeroGridCoordinator(hass, entry)
    _set_grid(hass, 0, 0)
    result = await coordinator._async_update_data()
    assert result.status == STATUS_DISABLED


async def test_maximize_export_calibrating_suspends_control(hass):
    """Calibration guard must suspend maximize_export (calibrator owns actuators)."""
    entry = _make_entry(control_mode="maximize_export")
    entry.add_to_hass(hass)
    coordinator = ZeroGridCoordinator(hass, entry)
    coordinator._calibrator = object()  # non-None → calibrating=True
    _set_grid(hass, 0, 0)
    result = await coordinator._async_update_data()
    assert result.status == STATUS_DISABLED


async def test_zero_import_grid_unavailable_enters_safe_state(hass):
    """Grid unavailable must enter safe state even in zero_import mode."""
    entry = _make_entry(control_mode="zero_import")
    entry.add_to_hass(hass)
    coordinator = ZeroGridCoordinator(hass, entry)
    result = await coordinator._async_update_data()
    assert result.status == STATUS_DISABLED


async def test_zero_export_controller_disabled_enters_safe_state(hass):
    """Disabled controller in zero_export mode must return DISABLED."""
    data = {**_BASE_DATA, "control_mode": "zero_export", "controller_enabled": False}
    entry = MockConfigEntry(domain=DOMAIN, title="Test", data=data, options={})
    entry.add_to_hass(hass)
    coordinator = ZeroGridCoordinator(hass, entry)
    _set_grid(hass, 200, 0)
    result = await coordinator._async_update_data()
    assert result.status == STATUS_DISABLED


async def test_zero_import_idle_does_not_write_array_setpoints(hass):
    """No array setpoint writes should happen when idle in zero_import mode."""
    entry = _make_entry(
        control_mode="zero_import",
        subentries_data=(
            {
                "subentry_id": "arr1",
                "subentry_type": ARRAY_SUBENTRY_TYPE,
                "title": "Roof",
                "data": _ARRAY_DATA,
            },
        ),
    )
    entry.add_to_hass(hass)
    _set_grid(hass, 0, 200)  # exporting → idle
    _set_state(hass, "number.inverter_limit", 80)

    written: list[tuple[str, float]] = []

    async def _mock_write_setpoint(array_cfg, value: float) -> None:
        written.append((array_cfg.setpoint_entity, value))

    coordinator = ZeroGridCoordinator(hass, entry)
    coordinator._actuators.write_setpoint = _mock_write_setpoint

    result = await coordinator._async_update_data()
    assert result.status == ControllerStatus.IDLE_EXPORT_OK
    assert written == [], f"No array writes expected in idle, got: {written}"


async def test_zero_export_importing_reopens_curtailed_arrays(hass):
    """Importing in zero_export mode reopens curtailed PV (free energy).

    Previously the whole PID was skipped on the allowed side, so a single
    export event left the arrays curtailed forever while importing.
    """
    entry = _make_entry(
        control_mode="zero_export",
        subentries_data=(
            {
                "subentry_id": "arr1",
                "subentry_type": ARRAY_SUBENTRY_TYPE,
                "title": "Roof",
                "data": _ARRAY_DATA,
            },
        ),
    )
    entry.add_to_hass(hass)
    _set_grid(hass, 200, 0)  # importing → allowed side
    _set_state(hass, "number.inverter_limit", 80)

    written: list[tuple[str, float]] = []

    async def _mock_write_setpoint(array_cfg, value: float) -> None:
        written.append((array_cfg.setpoint_entity, value))

    coordinator = ZeroGridCoordinator(hass, entry)
    coordinator._actuators.write_setpoint = _mock_write_setpoint

    result = await coordinator._async_update_data()
    assert result.status == ControllerStatus.IDLE_IMPORT_OK
    assert written, "Curtailed PV must be reopened while importing"
    assert written[0][1] > 80


async def test_zero_export_importing_does_not_reduce_loads(hass):
    """Importing in zero_export mode must not reduce controllable loads."""
    entry = _make_entry(
        control_mode="zero_export",
        subentries_data=(
            {
                "subentry_id": "load1",
                "subentry_type": LOAD_SUBENTRY_TYPE,
                "title": "EV",
                "data": {
                    "load_name": "EV",
                    "load_type": "numeric",
                    "setpoint_entity": "number.ev",
                    "setpoint_min": 0.0,
                    "setpoint_max": 16.0,
                    "w_per_unit": 230.0,
                    "settling_time_s": 0,
                    "load_priority": 10,
                },
            },
        ),
    )
    entry.add_to_hass(hass)
    coordinator = ZeroGridCoordinator(hass, entry)
    coordinator._engine._current_load_setpoints["EV"] = 16.0  # charging full
    _set_grid(hass, 2000, 0)  # importing → allowed in zero_export

    with patch.object(
        coordinator._actuators, "write_numeric_entity", new=AsyncMock()
    ) as mock_write:
        result = await coordinator._async_update_data()

    assert result.status == ControllerStatus.IDLE_IMPORT_OK
    mock_write.assert_not_awaited()
    assert coordinator._engine._current_load_setpoints["EV"] == 16.0


async def test_zero_import_exporting_routes_surplus_into_loads(hass):
    """Exporting in zero_import mode increases loads instead of doing nothing."""
    entry = _make_entry(
        control_mode="zero_import",
        subentries_data=(
            {
                "subentry_id": "load1",
                "subentry_type": LOAD_SUBENTRY_TYPE,
                "title": "EV",
                "data": {
                    "load_name": "EV",
                    "load_type": "numeric",
                    "setpoint_entity": "number.ev",
                    "setpoint_min": 0.0,
                    "setpoint_max": 16.0,
                    "w_per_unit": 230.0,
                    "settling_time_s": 0,
                    "load_priority": 10,
                },
            },
        ),
    )
    entry.add_to_hass(hass)
    coordinator = ZeroGridCoordinator(hass, entry)
    coordinator._engine._current_load_setpoints["EV"] = 0.0
    _set_state(hass, "number.ev", 0)
    _set_grid(hass, 0, 2300)  # exporting → allowed in zero_import

    with patch.object(
        coordinator._actuators, "write_numeric_entity", new=AsyncMock()
    ) as mock_write:
        result = await coordinator._async_update_data()

    assert result.status == ControllerStatus.IDLE_EXPORT_OK
    mock_write.assert_awaited()
    assert coordinator._engine._current_load_setpoints["EV"] > 0.0


async def test_zero_import_exporting_still_never_curtails_arrays(hass):
    """Even with loads absorbing, arrays are never curtailed in zero_import."""
    entry = _make_entry(
        control_mode="zero_import",
        subentries_data=(
            {
                "subentry_id": "arr1",
                "subentry_type": ARRAY_SUBENTRY_TYPE,
                "title": "Roof",
                "data": _ARRAY_DATA,
            },
        ),
    )
    entry.add_to_hass(hass)
    _set_grid(hass, 0, 3000)  # big export, no loads to absorb it
    _set_state(hass, "number.inverter_limit", 100)

    written: list[tuple[str, float]] = []

    async def _mock_write_setpoint(array_cfg, value: float) -> None:
        written.append((array_cfg.setpoint_entity, value))

    coordinator = ZeroGridCoordinator(hass, entry)
    coordinator._actuators.write_setpoint = _mock_write_setpoint

    result = await coordinator._async_update_data()
    assert result.status == ControllerStatus.IDLE_EXPORT_OK
    assert written == [], f"Arrays must never be curtailed in zero_import: {written}"


# ---------------------------------------------------------------------------
# Maximize modes: SoC limits and write-if-changed
# ---------------------------------------------------------------------------


def _battery_subentry(**extra):
    return {
        "data": {**_BATTERY_DATA, **extra},
        "subentry_type": BATTERY_SUBENTRY_TYPE,
        "subentry_id": "sub_bat",
        "title": "Battery",
        "unique_id": None,
    }


def _array_subentry():
    return {
        "data": _ARRAY_DATA,
        "subentry_type": ARRAY_SUBENTRY_TYPE,
        "subentry_id": "sub_arr",
        "title": "Roof",
        "unique_id": None,
    }


async def test_maximize_export_respects_min_soc(hass):
    """An empty battery is not discharged in maximize_export mode."""
    entry = _make_entry(
        control_mode="maximize_export",
        subentries_data=(_battery_subentry(battery_soc_sensor="sensor.battery_soc"),),
    )
    entry.add_to_hass(hass)
    coordinator = ZeroGridCoordinator(hass, entry)
    _set_state(hass, "sensor.battery_soc", 8)  # below default min_soc 10
    _set_grid(hass, 0, 0)

    with patch.object(coordinator._actuators, "write_numeric_entity", new=AsyncMock()):
        result = await coordinator._async_update_data()

    assert result.battery_setpoints.get("Battery") == 0.0, (
        "maximize_export must not discharge a battery at/below min SoC"
    )


async def test_maximize_import_respects_max_soc(hass):
    """A full battery is not charged in maximize_import mode."""
    entry = _make_entry(
        control_mode="maximize_import",
        subentries_data=(_battery_subentry(battery_soc_sensor="sensor.battery_soc"),),
    )
    entry.add_to_hass(hass)
    coordinator = ZeroGridCoordinator(hass, entry)
    _set_state(hass, "sensor.battery_soc", 97)  # above default max_soc 95
    _set_grid(hass, 0, 0)

    with patch.object(coordinator._actuators, "write_numeric_entity", new=AsyncMock()):
        result = await coordinator._async_update_data()

    assert result.battery_setpoints.get("Battery") == 0.0, (
        "maximize_import must not charge a battery at/above max SoC"
    )


async def test_maximize_export_skips_writes_when_entities_match(hass):
    """maximize_export does not rewrite actuators that already match the target."""
    entry = _make_entry(
        control_mode="maximize_export",
        subentries_data=(_array_subentry(), _battery_subentry()),
    )
    entry.add_to_hass(hass)
    coordinator = ZeroGridCoordinator(hass, entry)

    # Entities already at the maximize targets
    _set_state(hass, "number.inverter_limit", 100)
    _set_state(hass, "number.battery_setpoint", 2000)
    _set_grid(hass, 0, 0)

    with (
        patch.object(
            coordinator._actuators, "write_setpoint", new=AsyncMock()
        ) as mock_sp,
        patch.object(
            coordinator._actuators, "write_numeric_entity", new=AsyncMock()
        ) as mock_num,
    ):
        await coordinator._async_update_data()

    mock_sp.assert_not_awaited()
    mock_num.assert_not_awaited()


async def test_maximize_export_reasserts_external_changes(hass):
    """maximize_export re-writes an actuator that was changed externally."""
    entry = _make_entry(
        control_mode="maximize_export",
        subentries_data=(_array_subentry(),),
    )
    entry.add_to_hass(hass)
    coordinator = ZeroGridCoordinator(hass, entry)

    # Someone moved the limit down outside the controller
    _set_state(hass, "number.inverter_limit", 40)
    _set_grid(hass, 0, 0)

    with patch.object(
        coordinator._actuators, "write_setpoint", new=AsyncMock()
    ) as mock_sp:
        await coordinator._async_update_data()

    mock_sp.assert_awaited()
    assert mock_sp.call_args[0][1] == 100.0
