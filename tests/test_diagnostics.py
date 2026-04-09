"""Tests for the diagnostics module."""

from __future__ import annotations

import time
from unittest.mock import MagicMock

import pytest

from custom_components.zero_grid_controller.coordinator import ZGCResult
from custom_components.zero_grid_controller.diagnostics import (
    async_get_config_entry_diagnostics,
)
from custom_components.zero_grid_controller.estimator import RLSEstimator

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _make_zgc_result() -> ZGCResult:
    return ZGCResult(
        grid_raw_w=150.0,
        grid_filtered_w=148.0,
        pid_output_w=20.0,
        pid_p_w=10.0,
        pid_i_w=8.0,
        pid_d_w=2.0,
        mode="active",
        status="active",
        battery_clipping=False,
        learning_status="Learning...",
        setpoints={"PV West": 80.0},
        battery_setpoints={"Battery": 0.0},
        battery_unresponsive={"Battery": False},
        array_clipping={"PV West": False},
        array_gain_k={"PV West": None},
        array_calibration={"PV West": "estimated"},
    )


def _make_coordinator_mock(with_data: bool = True) -> MagicMock:
    coordinator = MagicMock()
    coordinator.data = _make_zgc_result() if with_data else None
    coordinator.last_update_success = True
    coordinator.last_update_success_time = None
    coordinator.update_interval = MagicMock()
    coordinator.update_interval.total_seconds.return_value = 5.0
    coordinator.controller_enabled = True
    coordinator.safe_state_applied = False

    # PID
    pid = MagicMock()
    pid.kp = 0.5
    pid.ki = 0.05
    pid.kd = 0.0
    pid.integral = 12.3
    pid._output_min = -10000.0
    pid._output_max = 10000.0
    coordinator.pid = pid

    # Arrays
    array = MagicMock()
    array.name = "PV West"
    array.output_type = "percent"
    array.setpoint_min = 0.0
    array.setpoint_max = 100.0
    array.settling_time_s = 15
    array.priority = 1
    array.enabled = True
    coordinator.arrays = [array]

    # Override setpoints
    coordinator.override_setpoints = {"PV West": (40.0, time.monotonic() + 300.0)}

    # Settling until
    coordinator.settling_until = {"PV West": time.monotonic() + 10.0}

    # Response factor
    coordinator._response_factor = 1.0

    # Estimators
    est = RLSEstimator(settling_time_s=15)
    coordinator.get_estimator.return_value = est
    coordinator._estimators = {"PV West": est}

    return coordinator


def _make_entry_mock(coordinator) -> MagicMock:
    entry = MagicMock()
    entry.entry_id = "test_entry_id"
    entry.title = "Test ZGC"
    entry.data = {
        "name": "Test ZGC",
        "grid_sensor": "sensor.grid_power",
        "grid_measurement_type": "net",
    }
    entry.options = {}
    entry.subentries = {}

    runtime = MagicMock()
    runtime.coordinator = coordinator
    entry.runtime_data = runtime

    return entry


# ---------------------------------------------------------------------------
# Test 1: Full diagnostics with coordinator data
# ---------------------------------------------------------------------------


async def test_diagnostics_with_data() -> None:
    """Diagnostics returns structured data when coordinator has data."""
    coordinator = _make_coordinator_mock(with_data=True)
    entry = _make_entry_mock(coordinator)

    hass = MagicMock()
    hass.states.get.return_value = None

    # Issue registry
    issue_registry = MagicMock()
    issue_registry.issues.values.return_value = []

    # Entity registry
    ent_reg = MagicMock()
    ent_reg_module = MagicMock()
    ent_reg_module.async_get.return_value = ent_reg
    ent_reg_module.async_entries_for_config_entry.return_value = []

    with (
        pytest.MonkeyPatch().context() as mp,
    ):
        mp.setattr(
            "custom_components.zero_grid_controller.diagnostics.ir_async_get",
            lambda _: issue_registry,
        )
        mp.setattr(
            "custom_components.zero_grid_controller.diagnostics.er",
            ent_reg_module,
        )
        result = await async_get_config_entry_diagnostics(hass, entry)

    assert "config_entry" in result
    assert "coordinator" in result
    assert "pid" in result
    assert "arrays" in result
    assert "override_setpoints" in result
    assert "settling_state" in result
    assert "estimators" in result
    assert "repair_issues" in result
    assert "entities" in result

    # Check coordinator data is present
    assert result["coordinator"]["grid_raw_w"] == 150.0
    assert result["coordinator"]["mode"] == "active"
    assert result["coordinator"]["status"] == "active"
    assert result["coordinator"]["battery_setpoints"]["Battery"] == 0.0
    assert result["coordinator"]["battery_unresponsive"]["Battery"] is False

    # Check PID data
    assert result["pid"]["kp"] == 0.5

    # Check arrays
    assert "PV West" in result["arrays"]

    # Check estimators
    assert "PV West" in result["estimators"]


# ---------------------------------------------------------------------------
# Test 2: Diagnostics with coordinator that has no data yet
# ---------------------------------------------------------------------------


async def test_diagnostics_coordinator_no_data() -> None:
    """Diagnostics handles coordinator with data=None gracefully."""
    coordinator = _make_coordinator_mock(with_data=False)
    entry = _make_entry_mock(coordinator)

    hass = MagicMock()

    issue_registry = MagicMock()
    issue_registry.issues.values.return_value = []

    ent_reg = MagicMock()
    ent_reg_module = MagicMock()
    ent_reg_module.async_get.return_value = ent_reg
    ent_reg_module.async_entries_for_config_entry.return_value = []

    with pytest.MonkeyPatch().context() as mp:
        mp.setattr(
            "custom_components.zero_grid_controller.diagnostics.ir_async_get",
            lambda _: issue_registry,
        )
        mp.setattr(
            "custom_components.zero_grid_controller.diagnostics.er",
            ent_reg_module,
        )
        result = await async_get_config_entry_diagnostics(hass, entry)

    assert result["coordinator"] == {}


# ---------------------------------------------------------------------------
# Test 3: Diagnostics without runtime_data
# ---------------------------------------------------------------------------


async def test_diagnostics_no_runtime_data() -> None:
    """Diagnostics handles None runtime_data gracefully."""
    entry = MagicMock()
    entry.entry_id = "test_entry_id"
    entry.title = "Test ZGC"
    entry.data = {}
    entry.options = {}
    entry.subentries = {}
    entry.runtime_data = None

    hass = MagicMock()

    issue_registry = MagicMock()
    issue_registry.issues.values.return_value = []

    ent_reg = MagicMock()
    ent_reg_module = MagicMock()
    ent_reg_module.async_get.return_value = ent_reg
    ent_reg_module.async_entries_for_config_entry.return_value = []

    with pytest.MonkeyPatch().context() as mp:
        mp.setattr(
            "custom_components.zero_grid_controller.diagnostics.ir_async_get",
            lambda _: issue_registry,
        )
        mp.setattr(
            "custom_components.zero_grid_controller.diagnostics.er",
            ent_reg_module,
        )
        result = await async_get_config_entry_diagnostics(hass, entry)

    assert result["coordinator"] == {}
    assert result["pid"] == {}


# ---------------------------------------------------------------------------
# Test 4: Diagnostics with repair issues
# ---------------------------------------------------------------------------


async def test_diagnostics_with_repair_issues() -> None:
    """Diagnostics includes repair issues from the issue registry."""
    from custom_components.zero_grid_controller.const import DOMAIN

    coordinator = _make_coordinator_mock(with_data=False)
    entry = _make_entry_mock(coordinator)

    hass = MagicMock()

    issue = MagicMock()
    issue.domain = DOMAIN
    issue.issue_id = "grid_sensor_unavailable"
    issue.severity = MagicMock()
    issue.severity.value = "error"
    issue.is_fixable = False
    issue.translation_key = "grid_sensor_unavailable"

    issue_registry = MagicMock()
    issue_registry.issues.values.return_value = [issue]

    ent_reg = MagicMock()
    ent_reg_module = MagicMock()
    ent_reg_module.async_get.return_value = ent_reg
    ent_reg_module.async_entries_for_config_entry.return_value = []

    with pytest.MonkeyPatch().context() as mp:
        mp.setattr(
            "custom_components.zero_grid_controller.diagnostics.ir_async_get",
            lambda _: issue_registry,
        )
        mp.setattr(
            "custom_components.zero_grid_controller.diagnostics.er",
            ent_reg_module,
        )
        result = await async_get_config_entry_diagnostics(hass, entry)

    assert len(result["repair_issues"]) == 1
    assert result["repair_issues"][0]["issue_id"] == "grid_sensor_unavailable"


# ---------------------------------------------------------------------------
# Test 5: Diagnostics with update_interval set on coordinator
# ---------------------------------------------------------------------------


async def test_diagnostics_coordinator_timing() -> None:
    """Diagnostics captures coordinator timing info."""
    coordinator = _make_coordinator_mock(with_data=False)
    entry = _make_entry_mock(coordinator)
    coordinator.last_update_success_time = MagicMock()
    coordinator.last_update_success_time.isoformat.return_value = "2026-04-06T12:00:00"

    hass = MagicMock()

    issue_registry = MagicMock()
    issue_registry.issues.values.return_value = []

    ent_reg = MagicMock()
    ent_reg_module = MagicMock()
    ent_reg_module.async_get.return_value = ent_reg
    ent_reg_module.async_entries_for_config_entry.return_value = []

    with pytest.MonkeyPatch().context() as mp:
        mp.setattr(
            "custom_components.zero_grid_controller.diagnostics.ir_async_get",
            lambda _: issue_registry,
        )
        mp.setattr(
            "custom_components.zero_grid_controller.diagnostics.er",
            ent_reg_module,
        )
        result = await async_get_config_entry_diagnostics(hass, entry)

    assert (
        result["coordinator_timing"]["last_update_success_time"]
        == "2026-04-06T12:00:00"
    )
    assert result["coordinator_timing"]["controller_enabled"] is True
    assert result["coordinator_timing"]["safe_state_applied"] is False


# ---------------------------------------------------------------------------
# Test 6: Diagnostics with entity registry entries
# ---------------------------------------------------------------------------


async def test_diagnostics_with_entities() -> None:
    """Diagnostics includes entities from the entity registry."""
    coordinator = _make_coordinator_mock(with_data=False)
    entry = _make_entry_mock(coordinator)

    hass = MagicMock()
    mock_state = MagicMock()
    mock_state.state = "150.0"
    hass.states.get.return_value = mock_state

    issue_registry = MagicMock()
    issue_registry.issues.values.return_value = []

    entity_entry = MagicMock()
    entity_entry.entity_id = "sensor.zgc_grid_power"
    entity_entry.unique_id = "test_entry_grid_filtered_w"

    ent_reg = MagicMock()
    ent_reg_module = MagicMock()
    ent_reg_module.async_get.return_value = ent_reg
    ent_reg_module.async_entries_for_config_entry.return_value = [entity_entry]

    with pytest.MonkeyPatch().context() as mp:
        mp.setattr(
            "custom_components.zero_grid_controller.diagnostics.ir_async_get",
            lambda _: issue_registry,
        )
        mp.setattr(
            "custom_components.zero_grid_controller.diagnostics.er",
            ent_reg_module,
        )
        result = await async_get_config_entry_diagnostics(hass, entry)

    assert len(result["entities"]) == 1
    assert result["entities"][0]["entity_id"] == "sensor.zgc_grid_power"


async def test_diagnostics_redacts_subentry_data() -> None:
    """Diagnostics redacts sensitive fields inside subentries."""
    coordinator = _make_coordinator_mock(with_data=False)
    entry = _make_entry_mock(coordinator)
    subentry = MagicMock()
    subentry.title = "Battery"
    subentry.subentry_type = "battery"
    subentry.data = {
        "battery_sensor": "sensor.secret_battery",
        "battery_setpoint_entity": "number.secret_target",
    }
    entry.subentries = {"sub1": subentry}

    hass = MagicMock()
    issue_registry = MagicMock()
    issue_registry.issues.values.return_value = []
    ent_reg = MagicMock()
    ent_reg_module = MagicMock()
    ent_reg_module.async_get.return_value = ent_reg
    ent_reg_module.async_entries_for_config_entry.return_value = []

    with pytest.MonkeyPatch().context() as mp:
        mp.setattr(
            "custom_components.zero_grid_controller.diagnostics.ir_async_get",
            lambda _: issue_registry,
        )
        mp.setattr(
            "custom_components.zero_grid_controller.diagnostics.er",
            ent_reg_module,
        )
        result = await async_get_config_entry_diagnostics(hass, entry)

    subentry_data = result["config_entry"]["subentries"]["Battery"]["data"]
    assert subentry_data["battery_sensor"] == "**REDACTED**"
    assert subentry_data["battery_setpoint_entity"] == "**REDACTED**"


# ---------------------------------------------------------------------------
# Test 7: Diagnostics with reliable estimator
# ---------------------------------------------------------------------------


async def test_diagnostics_reliable_estimator() -> None:
    """Diagnostics shows suggested_kp when estimator is reliable."""
    coordinator = _make_coordinator_mock(with_data=False)
    entry = _make_entry_mock(coordinator)

    # Make estimator reliable by feeding 25 updates
    est = RLSEstimator(settling_time_s=15)
    for _ in range(25):
        est.update(-100.0, 100.0)

    coordinator.get_estimator.return_value = est
    coordinator._estimators = {"PV West": est}

    hass = MagicMock()

    issue_registry = MagicMock()
    issue_registry.issues.values.return_value = []

    ent_reg = MagicMock()
    ent_reg_module = MagicMock()
    ent_reg_module.async_get.return_value = ent_reg
    ent_reg_module.async_entries_for_config_entry.return_value = []

    with pytest.MonkeyPatch().context() as mp:
        mp.setattr(
            "custom_components.zero_grid_controller.diagnostics.ir_async_get",
            lambda _: issue_registry,
        )
        mp.setattr(
            "custom_components.zero_grid_controller.diagnostics.er",
            ent_reg_module,
        )
        result = await async_get_config_entry_diagnostics(hass, entry)

    assert "PV West" in result["estimators"]
    assert "suggested_kp" in result["estimators"]["PV West"]


# ---------------------------------------------------------------------------
# Test (repairs): dismiss_grid_sensor_unavailable deletes the issue (line 27)
# ---------------------------------------------------------------------------


async def test_dismiss_grid_sensor_unavailable(hass) -> None:
    """dismiss_grid_sensor_unavailable calls async_delete_issue (covers repairs.py:27)."""
    from homeassistant.helpers.issue_registry import async_get as ir_async_get

    from custom_components.zero_grid_controller.const import DOMAIN
    from custom_components.zero_grid_controller.repairs import (
        ISSUE_GRID_SENSOR_UNAVAILABLE,
        dismiss_grid_sensor_unavailable,
        raise_grid_sensor_unavailable,
    )

    # First create the issue so there's something to dismiss
    raise_grid_sensor_unavailable(hass)
    ir = ir_async_get(hass)
    assert ir.async_get_issue(DOMAIN, ISSUE_GRID_SENSOR_UNAVAILABLE) is not None

    # Now dismiss it — this must call async_delete_issue (line 27)
    dismiss_grid_sensor_unavailable(hass)
    assert ir.async_get_issue(DOMAIN, ISSUE_GRID_SENSOR_UNAVAILABLE) is None
