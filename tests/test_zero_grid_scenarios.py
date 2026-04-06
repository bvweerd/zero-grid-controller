"""Realistic zero-grid control scenario tests.

System context (from live battery_controller diagnostics, 2026-04-05 20:00 local):
  House Amsterdam — 4 PV arrays total 6.3 kWp
    PV West  2.4 kWp  23 W/%   (w=2304 W at 100 %)
    PV Zuid  1.0 kWp  10 W/%   (w=960 W  at 100 %)
  Battery: Marstek 2.12 kWh / 1.21 kW
  Sensors captured:
    grid             = +161 W  (slight import, evening)
    battery power    =    0 W  (idle, SOC 98 %)
    consumption      =  ~500 W (midday) / ~715 W (evening)

Grid convention throughout: positive = importing, negative = exporting.
PID setpoint = 0 W.  delta_w > 0 means "curtail PV"; < 0 means "open PV".
"""
from __future__ import annotations

import time
from unittest.mock import AsyncMock, patch

import pytest
from homeassistant.core import HomeAssistant
from pytest_homeassistant_custom_component.common import MockConfigEntry

from custom_components.zero_grid_controller.array import ArrayConfig
import pytest

from custom_components.zero_grid_controller.const import (
    ARRAY_SUBENTRY_TYPE,
    CONF_ARRAY_NAME,
    CONF_BATTERY_MAX_CHARGE_W,
    CONF_BATTERY_SENSOR,
    CONF_DEADBAND_W,
    CONF_GRID_MEASUREMENT_TYPE,
    CONF_GRID_SENSOR,
    CONF_INVERT_SIGN,
    CONF_MODE_GUARD_ENABLED,
    CONF_MODE_GUARD_ENTITY,
    CONF_MODE_GUARD_MAPPING,
    CONF_OUTPUT_TYPE,
    CONF_SETPOINT_ENTITY,
    DOMAIN,
    OUTPUT_TYPE_PERCENT,
)
from custom_components.zero_grid_controller.coordinator import ZeroGridCoordinator
from custom_components.zero_grid_controller.estimator import RLSEstimator


@pytest.fixture(autouse=True)
def auto_enable_custom_integrations(enable_custom_integrations):
    return


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _make_entry(hass: HomeAssistant, data: dict | None = None, options: dict | None = None) -> MockConfigEntry:
    entry = MockConfigEntry(
        domain=DOMAIN,
        unique_id=DOMAIN,
        data=data or {
            "name": "Test ZGC",
            CONF_GRID_MEASUREMENT_TYPE: "net",
            CONF_GRID_SENSOR: "sensor.grid_power",
            CONF_INVERT_SIGN: False,
        },
        options=options or {},
    )
    entry.add_to_hass(hass)
    return entry


def _pv_west(setpoint: float = 80.0, *, pv_sensor: bool = False) -> ArrayConfig:
    """PV West 2.4 kWp — west-facing, 23 W/%, initial setpoint 80 %."""
    return ArrayConfig(
        name="PV West",
        enabled=True,
        output_type=OUTPUT_TYPE_PERCENT,
        setpoint_entity="number.pv_west_limit",
        pv_power_entity="sensor.pv_west_power" if pv_sensor else None,
        w_per_unit=23.0,
        calibration_confidence="estimated",
        setpoint_min=0.0,
        setpoint_max=100.0,
        settling_time_s=15,
        priority=1,
    )


def _pv_zuidarray(setpoint: float = 80.0) -> ArrayConfig:
    """PV Zuid 1.0 kWp — south-facing, 10 W/%, initial setpoint 80 %."""
    return ArrayConfig(
        name="PV Zuid",
        enabled=True,
        output_type=OUTPUT_TYPE_PERCENT,
        setpoint_entity="number.pv_zuidlimit",
        pv_power_entity=None,
        w_per_unit=10.0,
        calibration_confidence="estimated",
        setpoint_min=0.0,
        setpoint_max=100.0,
        settling_time_s=15,
        priority=2,
    )


def _inject(
    coordinator: ZeroGridCoordinator,
    arrays: list[ArrayConfig],
    setpoints: dict[str, float] | None = None,
) -> None:
    """Inject arrays and initial setpoints directly into coordinator state."""
    coordinator._arrays = arrays
    coordinator._grid_unavailable_reported = False
    for array in arrays:
        sp = (setpoints or {}).get(array.name, array.setpoint_max)
        coordinator._current_setpoints[array.name] = sp
        coordinator._estimators.setdefault(
            array.name, RLSEstimator(settling_time_s=array.settling_time_s)
        )


async def _run_loop(coordinator: ZeroGridCoordinator, dt: float = 5.0):
    """Run one control-loop cycle with _write_setpoint mocked out."""
    with patch.object(coordinator, "_write_setpoint", new=AsyncMock()), \
         patch.object(coordinator, "_persist_estimators"):
        return await coordinator._run_control_loop(dt, time.monotonic())


# ---------------------------------------------------------------------------
# Scenario 1: Sunny midday — heavy export, controller curtails PV
#
# PV West + Zuid producing ~3200 W combined, consumption 500 W →
# net grid = −2700 W (exporting heavily).
# Expected: both arrays' setpoints DECREASE (curtail).
# ---------------------------------------------------------------------------

async def test_scenario_heavy_export_curtails_setpoints(hass: HomeAssistant) -> None:
    entry = _make_entry(hass, options={CONF_DEADBAND_W: 20.0})
    hass.states.async_set("sensor.grid_power", "-2700.0")

    coordinator = ZeroGridCoordinator(hass, entry)
    _inject(coordinator, [_pv_west(), _pv_zuidarray()], {"PV West": 80.0, "PV Zuid": 80.0})

    await _run_loop(coordinator)

    # Setpoints must have decreased (curtailment)
    assert coordinator._current_setpoints["PV West"] < 80.0
    assert coordinator._current_setpoints["PV Zuid"] < 80.0


# ---------------------------------------------------------------------------
# Scenario 2: Evening — slight import, controller opens setpoints
#
# Production ≈ 0 W (captured in diagnostics: 20:00 local), consumption 715 W,
# but let's say grid = +161 W and arrays are at 50 % limit.
# Expected: setpoints INCREASE (open up to produce more).
# ---------------------------------------------------------------------------

async def test_scenario_import_opens_setpoints(hass: HomeAssistant) -> None:
    # Morning scenario: house drawing 500 W, PV not yet producing, arrays at 50 %.
    # The controller should open the limits to allow more production.
    # We pre-warm the EWM filter to the actual grid reading so the first cycle
    # uses the full error rather than an attenuated start value.
    entry = _make_entry(hass, options={CONF_DEADBAND_W: 20.0})
    hass.states.async_set("sensor.grid_power", "500.0")

    coordinator = ZeroGridCoordinator(hass, entry)
    _inject(coordinator, [_pv_west(), _pv_zuidarray()], {"PV West": 50.0, "PV Zuid": 50.0})
    coordinator._filtered_w = 500.0   # pre-warm EWM filter

    await _run_loop(coordinator)

    assert coordinator._current_setpoints["PV West"] > 50.0
    assert coordinator._current_setpoints["PV Zuid"] > 50.0


# ---------------------------------------------------------------------------
# Scenario 3: Deadband — tiny grid imbalance, no setpoint changes
#
# Battery controller's zero-grid deadband is 50 W (from diagnostics).
# Here we use the coordinator's DEFAULT_DEADBAND_W (20 W).
# Grid = +10 W is within deadband → integrator frozen, no writes.
# ---------------------------------------------------------------------------

async def test_scenario_within_deadband_no_writes(hass: HomeAssistant) -> None:
    entry = _make_entry(hass)
    hass.states.async_set("sensor.grid_power", "10.0")

    coordinator = ZeroGridCoordinator(hass, entry)
    _inject(coordinator, [_pv_west()], {"PV West": 80.0})

    with patch.object(coordinator, "_write_setpoint", new=AsyncMock()) as mock_write, \
         patch.object(coordinator, "_persist_estimators"):
        result = await coordinator._run_control_loop(5.0, time.monotonic())

    mock_write.assert_not_called()
    from custom_components.zero_grid_controller.const import STATUS_DEADBAND
    assert result.mode == STATUS_DEADBAND


# ---------------------------------------------------------------------------
# Scenario 4: Passive mode — importing, controller does NOT open setpoints
#
# Mode guard entity "input_select.energy_mode" is in "self_consumption" state
# which maps to MODE_PASSIVE.  In passive mode delta_w < 0 (open) is blocked.
# ---------------------------------------------------------------------------

async def test_scenario_passive_mode_blocks_opening(hass: HomeAssistant) -> None:
    entry = _make_entry(hass, data={
        "name": "Test ZGC",
        CONF_GRID_MEASUREMENT_TYPE: "net",
        CONF_GRID_SENSOR: "sensor.grid_power",
        CONF_INVERT_SIGN: False,
        CONF_MODE_GUARD_ENABLED: True,
        CONF_MODE_GUARD_ENTITY: "input_select.energy_mode",
        CONF_MODE_GUARD_MAPPING: {"self_consumption": "passive", "export": "active"},
    })
    hass.states.async_set("sensor.grid_power", "300.0")          # importing
    hass.states.async_set("input_select.energy_mode", "self_consumption")

    coordinator = ZeroGridCoordinator(hass, entry)
    _inject(coordinator, [_pv_west()], {"PV West": 80.0})

    with patch.object(coordinator, "_write_setpoint", new=AsyncMock()) as mock_write, \
         patch.object(coordinator, "_persist_estimators"):
        await coordinator._run_control_loop(5.0, time.monotonic())

    # Passive mode must block opening → no setpoint written
    mock_write.assert_not_called()


# ---------------------------------------------------------------------------
# Scenario 5: Passive mode — exporting, controller IS allowed to curtail
#
# Same mode guard, but grid = −500 W (exporting).
# delta_w > 0 (curtail) is permitted even in passive mode.
# ---------------------------------------------------------------------------

async def test_scenario_passive_mode_allows_curtailing(hass: HomeAssistant) -> None:
    entry = _make_entry(hass, data={
        "name": "Test ZGC",
        CONF_GRID_MEASUREMENT_TYPE: "net",
        CONF_GRID_SENSOR: "sensor.grid_power",
        CONF_INVERT_SIGN: False,
        CONF_MODE_GUARD_ENABLED: True,
        CONF_MODE_GUARD_ENTITY: "input_select.energy_mode",
        CONF_MODE_GUARD_MAPPING: {"self_consumption": "passive"},
    })
    hass.states.async_set("sensor.grid_power", "-500.0")         # exporting
    hass.states.async_set("input_select.energy_mode", "self_consumption")

    coordinator = ZeroGridCoordinator(hass, entry)
    _inject(coordinator, [_pv_west()], {"PV West": 80.0})

    with patch.object(coordinator, "_write_setpoint", new=AsyncMock()) as mock_write, \
         patch.object(coordinator, "_persist_estimators"):
        await coordinator._run_control_loop(5.0, time.monotonic())

    # Setpoint must have been written (curtailment allowed in passive)
    mock_write.assert_called_once()
    written_value = mock_write.call_args.args[1]
    assert written_value < 80.0


# ---------------------------------------------------------------------------
# Scenario 6: Battery clipping — Marstek at full charge power
#
# Marstek max charge = 1210 W.  At 98 % SOC the inverter caps charge at
# ~1210 W × 0.95 = 1149.5 W threshold.  battery_clipping=True must appear
# in the ZGCResult.
# ---------------------------------------------------------------------------

async def test_scenario_battery_clipping_detected(hass: HomeAssistant) -> None:
    entry = _make_entry(hass, data={
        "name": "Test ZGC",
        CONF_GRID_MEASUREMENT_TYPE: "net",
        CONF_GRID_SENSOR: "sensor.grid_power",
        CONF_INVERT_SIGN: False,
        CONF_BATTERY_SENSOR: "sensor.marstek_power",
        CONF_BATTERY_MAX_CHARGE_W: 1210.0,
    })
    hass.states.async_set("sensor.grid_power", "-800.0")
    hass.states.async_set("sensor.marstek_power", "1200.0")  # 1200 ≥ 1210 × 0.95

    coordinator = ZeroGridCoordinator(hass, entry)
    _inject(coordinator, [_pv_west()], {"PV West": 80.0})

    result = await _run_loop(coordinator)

    assert result.battery_clipping is True


# ---------------------------------------------------------------------------
# Scenario 7: Settling time — array locked, no write during settling window
#
# After a step the array's settling_until is in the future.
# The coordinator must skip it even though delta_w is large.
# ---------------------------------------------------------------------------

async def test_scenario_settling_time_blocks_writes(hass: HomeAssistant) -> None:
    entry = _make_entry(hass, options={CONF_DEADBAND_W: 0.0})
    hass.states.async_set("sensor.grid_power", "-2700.0")

    coordinator = ZeroGridCoordinator(hass, entry)
    array = _pv_west()
    _inject(coordinator, [array], {"PV West": 80.0})

    # Mark the array as still settling
    coordinator._settling_until["PV West"] = time.monotonic() + 30.0

    with patch.object(coordinator, "_write_setpoint", new=AsyncMock()) as mock_write, \
         patch.object(coordinator, "_persist_estimators"):
        await coordinator._run_control_loop(5.0, time.monotonic())

    mock_write.assert_not_called()


# ---------------------------------------------------------------------------
# Scenario 8: Proportional distribution across two arrays
#
# Realistic afternoon setup: PV West at 80 % (headroom_up = 80×23 = 1840 W),
# PV Zuid at 60 % (headroom_up = 60×10 = 600 W), total = 2440 W.
# Grid = −1200 W → curtail; each array's share ∝ its headroom.
# Expected: PV West setpoint drops more than PV Zuid (more headroom).
# ---------------------------------------------------------------------------

async def test_scenario_proportional_distribution(hass: HomeAssistant) -> None:
    entry = _make_entry(hass, options={CONF_DEADBAND_W: 0.0})
    hass.states.async_set("sensor.grid_power", "-1200.0")

    coordinator = ZeroGridCoordinator(hass, entry)
    _inject(
        coordinator,
        [_pv_west(), _pv_zuidarray()],
        {"PV West": 80.0, "PV Zuid": 60.0},
    )

    await _run_loop(coordinator)

    sp_west = coordinator._current_setpoints["PV West"]
    sp_zuidarray = coordinator._current_setpoints["PV Zuid"]

    # Both setpoints must have dropped
    assert sp_west < 80.0
    assert sp_zuidarray < 60.0

    # West has 1840 W headroom vs Zuid's 600 W → West absorbs more of the cut
    drop_west_w = (80.0 - sp_west) * 23.0
    drop_zuidarray_w = (60.0 - sp_zuidarray) * 10.0
    assert drop_west_w > drop_zuidarray_w


# ---------------------------------------------------------------------------
# Scenario 9: Override setpoint — PID leaves overridden array alone
#
# service.override_setpoint pins PV West at 40 % for 5 minutes.
# Even with a large grid error the coordinator must not touch PV West.
# ---------------------------------------------------------------------------

async def test_scenario_override_prevents_writes(hass: HomeAssistant) -> None:
    entry = _make_entry(hass, options={CONF_DEADBAND_W: 0.0})
    hass.states.async_set("sensor.grid_power", "-2700.0")

    coordinator = ZeroGridCoordinator(hass, entry)
    _inject(
        coordinator,
        [_pv_west(), _pv_zuidarray()],
        {"PV West": 80.0, "PV Zuid": 80.0},
    )
    coordinator.override_setpoint("PV West", 40.0, duration_s=300.0)

    await _run_loop(coordinator)

    # PV West must stay at 80 % (override value 40 % is written externally)
    assert coordinator._current_setpoints["PV West"] == 80.0
    # PV Zuid is free and should have been curtailed
    assert coordinator._current_setpoints["PV Zuid"] < 80.0


# ---------------------------------------------------------------------------
# Scenario 10: RLS learning — after enough cycles Kp is auto-tuned
#
# Each cycle we feed the estimator a step of −230 W (PV West drops 10 %)
# and a grid response of +225 W (≈ 23 W/%, realistic for PV West).
# After the estimator becomes reliable, Kp should be updated away from
# the default.
# ---------------------------------------------------------------------------

async def test_scenario_rls_learning_auto_tunes_kp(hass: HomeAssistant) -> None:
    from custom_components.zero_grid_controller.const import DEFAULT_KP

    entry = _make_entry(hass)
    coordinator = ZeroGridCoordinator(hass, entry)
    _inject(coordinator, [_pv_west()], {"PV West": 80.0})

    estimator = coordinator._estimators["PV West"]
    initial_kp = coordinator.pid.kp

    # Feed 30 consistent observations: −230 W step → +225 W grid response
    for _ in range(30):
        estimator.update(-230.0, 225.0)

    assert estimator.is_reliable

    # Simulate one more coordinator update that triggers Kp tuning
    new_kp = estimator.suggest_kp(coordinator.pid.kp, coordinator._response_factor)
    coordinator.pid.set_gains(new_kp, coordinator.pid.ki, coordinator.pid.kd)

    # Kp should have moved away from the un-tuned default
    assert coordinator.pid.kp != pytest.approx(initial_kp, abs=0.05)
    # And must be a sane value (0.01–10)
    assert 0.01 <= coordinator.pid.kp <= 10.0


# ---------------------------------------------------------------------------
# Scenario 11: Cloud shadow — PV sensor below setpoint, integrator frozen
#
# PV West limit is at 80 % (≈ 1840 W), but the PV sensor reports only 50 W
# (cloud passing over).  The controller must detect cloud shadow, freeze the
# integrator, and return STATUS_CLOUD_SHADOW without writing a setpoint.
# ---------------------------------------------------------------------------

async def test_scenario_cloud_shadow_freezes_integrator(hass: HomeAssistant) -> None:
    entry = _make_entry(hass, options={CONF_DEADBAND_W: 0.0})
    hass.states.async_set("sensor.grid_power", "-500.0")
    hass.states.async_set("sensor.pv_west_power", "50.0")  # far below 80 % × 23 = 1840 W

    coordinator = ZeroGridCoordinator(hass, entry)
    array = _pv_west(pv_sensor=True)
    _inject(coordinator, [array], {"PV West": 80.0})

    with patch.object(coordinator, "_write_setpoint", new=AsyncMock()) as mock_write, \
         patch.object(coordinator, "_persist_estimators"):
        result = await coordinator._run_control_loop(5.0, time.monotonic())

    from custom_components.zero_grid_controller.const import STATUS_CLOUD_SHADOW
    assert result.mode == STATUS_CLOUD_SHADOW
    mock_write.assert_not_called()


# ---------------------------------------------------------------------------
# Scenario 12: Computed grid mode — consumption and production sensors
#
# This is how the battery_controller is configured: two separate sensors for
# consumption (sensor.power_consumption) and production (sensor.power_production).
# Consumption 715 W (evening value from diagnostics), production 0 W →
# computed grid = +715 W (importing).
# ---------------------------------------------------------------------------

async def test_scenario_computed_grid_mode(hass: HomeAssistant) -> None:
    entry = _make_entry(hass, data={
        "name": "Test ZGC",
        CONF_GRID_MEASUREMENT_TYPE: "computed",
        "power_consumption_sensors": ["sensor.power_consumption"],
        "power_production_sensors": ["sensor.power_production"],
        CONF_INVERT_SIGN: False,
    })
    hass.states.async_set("sensor.power_consumption", "715.0")
    hass.states.async_set("sensor.power_production", "0.0")

    coordinator = ZeroGridCoordinator(hass, entry)
    assert coordinator._read_grid() == pytest.approx(715.0)


# ---------------------------------------------------------------------------
# Scenario 13: MODE_DISABLED — mode guard maps to disabled
#
# When the mode guard entity is in a state mapped to "disabled",
# the controller must reset the PID and return STATUS_DISABLED.
# ---------------------------------------------------------------------------

async def test_scenario_mode_disabled(hass: HomeAssistant) -> None:
    entry = _make_entry(hass, data={
        "name": "Test ZGC",
        CONF_GRID_MEASUREMENT_TYPE: "net",
        CONF_GRID_SENSOR: "sensor.grid_power",
        CONF_INVERT_SIGN: False,
        CONF_MODE_GUARD_ENABLED: True,
        CONF_MODE_GUARD_ENTITY: "input_select.energy_mode",
        CONF_MODE_GUARD_MAPPING: {"off": "disabled"},
    })
    hass.states.async_set("sensor.grid_power", "-500.0")
    hass.states.async_set("input_select.energy_mode", "off")

    coordinator = ZeroGridCoordinator(hass, entry)
    _inject(coordinator, [_pv_west()], {"PV West": 80.0})

    from custom_components.zero_grid_controller.const import STATUS_DISABLED

    with patch.object(coordinator, "_write_setpoint", new=AsyncMock()) as mock_write, \
         patch.object(coordinator, "_persist_estimators"):
        result = await coordinator._run_control_loop(5.0, __import__("time").monotonic())

    assert result.mode == STATUS_DISABLED
    mock_write.assert_not_called()


# ---------------------------------------------------------------------------
# Scenario 14: PV clipping ACTIVE — pv_clipping_any = True
#
# When the PV sensor reports power close to the setpoint limit, the controller
# detects clipping (pv_clipping_any = True) and proceeds with normal control.
# ---------------------------------------------------------------------------

async def test_scenario_pv_clipping_active(hass: HomeAssistant) -> None:
    entry = _make_entry(hass, options={CONF_DEADBAND_W: 0.0})
    hass.states.async_set("sensor.grid_power", "-500.0")
    # PV at 1800 W, limit at 80% * 23 W/% = 1840 W → clipping (1800 >= 1656)
    hass.states.async_set("sensor.pv_west_power", "1800.0")

    coordinator = ZeroGridCoordinator(hass, entry)
    array = _pv_west(pv_sensor=True)
    _inject(coordinator, [array], {"PV West": 80.0})
    coordinator._filtered_w = -500.0  # pre-warm filter

    result = await _run_loop(coordinator)

    assert result.array_clipping.get("PV West") is True


# ---------------------------------------------------------------------------
# Scenario 15: Battery control enabled — writes battery target setpoint
#
# When _battery_control_enabled is True and _battery_setpoint_entity is set,
# the coordinator must call hass.services.async_call for the number.set_value.
# ---------------------------------------------------------------------------

async def test_scenario_battery_setpoint_write(hass: HomeAssistant) -> None:
    from custom_components.zero_grid_controller.const import (
        CONF_BATTERY_CONTROL_ENABLED,
        CONF_BATTERY_MAX_CHARGE_W,
        CONF_BATTERY_SETPOINT_ENTITY,
    )

    entry = _make_entry(hass, data={
        "name": "Test ZGC",
        CONF_GRID_MEASUREMENT_TYPE: "net",
        CONF_GRID_SENSOR: "sensor.grid_power",
        CONF_INVERT_SIGN: False,
        CONF_BATTERY_CONTROL_ENABLED: True,
        CONF_BATTERY_SETPOINT_ENTITY: "number.battery_target",
        CONF_BATTERY_MAX_CHARGE_W: 5000.0,
    }, options={CONF_DEADBAND_W: 0.0})
    hass.states.async_set("sensor.grid_power", "300.0")

    coordinator = ZeroGridCoordinator(hass, entry)
    _inject(coordinator, [], {})
    coordinator._filtered_w = 300.0

    battery_calls = []

    async def _spy_number_set_value(call):
        battery_calls.append(call)

    hass.services.async_register("number", "set_value", _spy_number_set_value)

    with patch.object(coordinator, "_persist_estimators"):
        await coordinator._run_control_loop(5.0, __import__("time").monotonic())

    assert len(battery_calls) >= 1
    assert battery_calls[0].data["entity_id"] == "number.battery_target"


# ---------------------------------------------------------------------------
# Scenario 16: STATUS_SATURATION — battery clipping, PV sensor present but not clipping
# ---------------------------------------------------------------------------

async def test_scenario_status_saturation(hass: HomeAssistant) -> None:
    from custom_components.zero_grid_controller.const import (
        CONF_BATTERY_MAX_CHARGE_W,
        CONF_BATTERY_SENSOR,
        STATUS_SATURATION,
    )

    entry = _make_entry(hass, data={
        "name": "Test ZGC",
        CONF_GRID_MEASUREMENT_TYPE: "net",
        CONF_GRID_SENSOR: "sensor.grid_power",
        CONF_INVERT_SIGN: False,
        CONF_BATTERY_SENSOR: "sensor.battery_power",
        CONF_BATTERY_MAX_CHARGE_W: 1000.0,
    }, options={CONF_DEADBAND_W: 0.0})
    hass.states.async_set("sensor.grid_power", "-500.0")
    # Battery at max charge (clipping)
    hass.states.async_set("sensor.battery_power", "980.0")  # >= 1000 * 0.95
    # PV NOT clipping: 50 W << 80% * 23 W/% * 0.9 = 1656 W
    hass.states.async_set("sensor.pv_west_power", "50.0")

    coordinator = ZeroGridCoordinator(hass, entry)
    array = _pv_west(pv_sensor=True)
    _inject(coordinator, [array], {"PV West": 80.0})
    coordinator._filtered_w = -500.0

    result = await _run_loop(coordinator)

    assert result.mode == STATUS_SATURATION


# ---------------------------------------------------------------------------
# Scenario 17: Grid unavailable → available transition dismisses repair issue
# ---------------------------------------------------------------------------

async def test_scenario_grid_unavailable_dismiss_issue(hass: HomeAssistant) -> None:
    entry = _make_entry(hass)
    hass.states.async_set("sensor.grid_power", "150.0")

    coordinator = ZeroGridCoordinator(hass, entry)
    _inject(coordinator, [], {})
    # Simulate: we previously reported the issue
    coordinator._grid_unavailable_reported = True

    dismissed = []

    def _mock_dismiss(hass_arg):
        dismissed.append(True)

    with patch(
        "custom_components.zero_grid_controller.coordinator.dismiss_grid_sensor_unavailable",
        side_effect=_mock_dismiss,
    ), patch.object(coordinator, "_persist_estimators"):
        await coordinator._run_control_loop(5.0, __import__("time").monotonic())

    assert dismissed, "dismiss_grid_sensor_unavailable should have been called"
    assert coordinator._grid_unavailable_reported is False


# ---------------------------------------------------------------------------
# Scenario 18: _async_update_data exception → raises UpdateFailed
# ---------------------------------------------------------------------------

async def test_scenario_update_data_exception(hass: HomeAssistant) -> None:
    from homeassistant.helpers.update_coordinator import UpdateFailed

    entry = _make_entry(hass)
    coordinator = ZeroGridCoordinator(hass, entry)

    with patch.object(
        coordinator, "_run_control_loop", side_effect=RuntimeError("simulated error")
    ):
        with pytest.raises(UpdateFailed):
            await coordinator._async_update_data()


# ---------------------------------------------------------------------------
# Scenario 19: _resolve_mode — unknown state falls back to MODE_ACTIVE
# ---------------------------------------------------------------------------

async def test_scenario_resolve_mode_unknown_state(hass: HomeAssistant) -> None:
    entry = _make_entry(hass, data={
        "name": "Test ZGC",
        CONF_GRID_MEASUREMENT_TYPE: "net",
        CONF_GRID_SENSOR: "sensor.grid_power",
        CONF_INVERT_SIGN: False,
        CONF_MODE_GUARD_ENABLED: True,
        CONF_MODE_GUARD_ENTITY: "input_select.mode",
        CONF_MODE_GUARD_MAPPING: {"active": "active"},
    })
    hass.states.async_set("input_select.mode", "unknown")

    coordinator = ZeroGridCoordinator(hass, entry)
    from custom_components.zero_grid_controller.const import MODE_ACTIVE
    assert coordinator._resolve_mode() == MODE_ACTIVE


# ---------------------------------------------------------------------------
# Scenario 20: _resolve_mode — invalid mapped value falls back to MODE_ACTIVE
# ---------------------------------------------------------------------------

async def test_scenario_resolve_mode_invalid_mapped(hass: HomeAssistant) -> None:
    entry = _make_entry(hass, data={
        "name": "Test ZGC",
        CONF_GRID_MEASUREMENT_TYPE: "net",
        CONF_GRID_SENSOR: "sensor.grid_power",
        CONF_INVERT_SIGN: False,
        CONF_MODE_GUARD_ENABLED: True,
        CONF_MODE_GUARD_ENTITY: "input_select.mode",
        CONF_MODE_GUARD_MAPPING: {"export": "invalid_mode"},
    })
    hass.states.async_set("input_select.mode", "export")

    coordinator = ZeroGridCoordinator(hass, entry)
    from custom_components.zero_grid_controller.const import MODE_ACTIVE
    assert coordinator._resolve_mode() == MODE_ACTIVE


# ---------------------------------------------------------------------------
# Scenario 21: Expired override is cleaned up during distribute_and_write
# ---------------------------------------------------------------------------

async def test_scenario_expired_override_cleanup(hass: HomeAssistant) -> None:
    import time as _time

    entry = _make_entry(hass, options={CONF_DEADBAND_W: 0.0})
    hass.states.async_set("sensor.grid_power", "-500.0")

    coordinator = ZeroGridCoordinator(hass, entry)
    _inject(coordinator, [_pv_west()], {"PV West": 80.0})
    coordinator._filtered_w = -500.0

    # Set an already-expired override
    coordinator._override_setpoints["PV West"] = (40.0, _time.monotonic() - 10.0)

    await _run_loop(coordinator)

    # Expired override should have been removed
    assert "PV West" not in coordinator._override_setpoints


# ---------------------------------------------------------------------------
# Scenario 22: RLS estimator update when reliable → auto-tunes Kp
# ---------------------------------------------------------------------------

async def test_scenario_rls_auto_tune_in_update_estimators(hass: HomeAssistant) -> None:
    import time as _time

    entry = _make_entry(hass)
    coordinator = ZeroGridCoordinator(hass, entry)
    _inject(coordinator, [_pv_west()], {"PV West": 80.0})

    # Pre-seed estimator to make it reliable (20+ updates)
    estimator = coordinator._estimators["PV West"]
    for _ in range(25):
        estimator.update(-230.0, 225.0)
    assert estimator.is_reliable

    initial_kp = coordinator.pid.kp

    # Set a pending estimate that is "old enough" to have settled
    now = _time.monotonic()
    coordinator._pending_estimates["PV West"] = (-230.0, 0.0, now - 30.0)
    coordinator._filtered_w = 225.0  # simulated grid response

    coordinator._update_estimators({}, now)

    # Kp should have been auto-tuned
    assert coordinator.pid.kp != pytest.approx(initial_kp, abs=0.001)


# ---------------------------------------------------------------------------
# Scenario 23: _persist_estimators stores state in entry options
# ---------------------------------------------------------------------------

async def test_scenario_persist_estimators(hass: HomeAssistant) -> None:
    from custom_components.zero_grid_controller.const import CONF_ESTIMATOR_STATE

    entry = _make_entry(hass)
    coordinator = ZeroGridCoordinator(hass, entry)
    _inject(coordinator, [_pv_west()], {"PV West": 80.0})

    coordinator._persist_estimators()

    # Options should now contain estimator state
    assert CONF_ESTIMATOR_STATE in entry.options


# ---------------------------------------------------------------------------
# Scenario 24: _write_setpoint with switch output type
# ---------------------------------------------------------------------------

async def test_scenario_write_setpoint_switch(hass: HomeAssistant) -> None:
    from custom_components.zero_grid_controller.const import OUTPUT_TYPE_SWITCH
    from custom_components.zero_grid_controller.array import ArrayConfig

    entry = _make_entry(hass)
    coordinator = ZeroGridCoordinator(hass, entry)

    switch_array = ArrayConfig(
        name="Load Switch",
        enabled=True,
        output_type=OUTPUT_TYPE_SWITCH,
        setpoint_entity="switch.load",
        pv_power_entity=None,
        w_per_unit=500.0,
        calibration_confidence="estimated",
        setpoint_min=0.0,
        setpoint_max=1.0,
        settling_time_s=5,
        priority=1,
    )

    switch_calls = []

    async def _spy_switch(call):
        switch_calls.append(call)

    hass.services.async_register("switch", "turn_on", _spy_switch)
    hass.services.async_register("switch", "turn_off", _spy_switch)

    # Write value > 0 → turn_on
    await coordinator._write_setpoint(switch_array, 1.0)
    # Write value == 0 → turn_off
    await coordinator._write_setpoint(switch_array, 0.0)

    assert len(switch_calls) == 2


# ---------------------------------------------------------------------------
# Scenario 25: _distribute_and_write — total headroom = 0 → return {}
# ---------------------------------------------------------------------------

async def test_scenario_distribute_headroom_zero(hass: HomeAssistant) -> None:
    """_distribute_and_write returns {} when all headroom is exhausted."""
    entry = _make_entry(hass, options={CONF_DEADBAND_W: 0.0})
    hass.states.async_set("sensor.grid_power", "500.0")
    coordinator = ZeroGridCoordinator(hass, entry)
    # Array at setpoint_min → headroom_up_w = 0 for curtail direction
    _inject(coordinator, [_pv_west()], {"PV West": 0.0})  # at setpoint_min = 0
    coordinator._filtered_w = 500.0

    with patch.object(coordinator, "_write_setpoint", new=AsyncMock()) as mock_write, \
         patch.object(coordinator, "_persist_estimators"):
        # delta_w > 0 (curtail) but array is at min → total headroom = 0 → {}
        result = await coordinator._distribute_and_write(100.0, time.monotonic())

    assert result == {}
    mock_write.assert_not_called()


# ---------------------------------------------------------------------------
# Scenario 26: _distribute_and_write — delta_unit rounds to 0 → continue
# ---------------------------------------------------------------------------

async def test_scenario_distribute_delta_unit_zero(hass: HomeAssistant) -> None:
    """_distribute_and_write skips arrays when share_w / w_per_unit rounds to 0."""
    entry = _make_entry(hass, options={CONF_DEADBAND_W: 0.0})
    hass.states.async_set("sensor.grid_power", "500.0")
    coordinator = ZeroGridCoordinator(hass, entry)
    # w_per_unit = 23; delta_w = 1 W → share = 1 W → delta_unit = floor(1/23) = 0
    _inject(coordinator, [_pv_west()], {"PV West": 50.0})
    coordinator._filtered_w = 500.0

    with patch.object(coordinator, "_write_setpoint", new=AsyncMock()) as mock_write, \
         patch.object(coordinator, "_persist_estimators"):
        result = await coordinator._distribute_and_write(1.0, time.monotonic())

    assert result == {}
    mock_write.assert_not_called()


# ---------------------------------------------------------------------------
# Scenario 27: _distribute_and_write — actual_delta = 0 after clamp → continue
# ---------------------------------------------------------------------------

async def test_scenario_distribute_actual_delta_zero(hass: HomeAssistant) -> None:
    """_distribute_and_write skips write when clamp produces zero delta."""
    import custom_components.zero_grid_controller.coordinator as coord_module

    entry = _make_entry(hass)
    coordinator = ZeroGridCoordinator(hass, entry)
    _inject(coordinator, [_pv_west()], {"PV West": 50.0})
    coordinator._filtered_w = 500.0

    original_clamp = coord_module._clamp

    def _clamp_returns_current(value, lo, hi):
        # Return current setpoint (50.0) to force actual_delta = 0
        return 50.0

    with patch.object(coordinator, "_write_setpoint", new=AsyncMock()) as mock_write, \
         patch.object(coordinator, "_persist_estimators"), \
         patch.object(coord_module, "_clamp", side_effect=_clamp_returns_current):
        result = await coordinator._distribute_and_write(200.0, time.monotonic())

    assert result == {}
    mock_write.assert_not_called()


# ---------------------------------------------------------------------------
# Scenario 28: _write_setpoint with number output type (line 516)
# ---------------------------------------------------------------------------

async def test_scenario_write_setpoint_number(hass: HomeAssistant) -> None:
    """_write_setpoint calls number.set_value service for percent-type arrays."""
    entry = _make_entry(hass)
    coordinator = ZeroGridCoordinator(hass, entry)

    number_calls = []

    async def _spy_number(call):
        number_calls.append(call)

    hass.services.async_register("number", "set_value", _spy_number)

    await coordinator._write_setpoint(_pv_west(), 70.0)

    assert len(number_calls) == 1


# ---------------------------------------------------------------------------
# Scenario 29: _update_estimators — pending estimate not settled yet (line 544)
# ---------------------------------------------------------------------------

async def test_scenario_update_estimators_not_settled(hass: HomeAssistant) -> None:
    """_update_estimators skips estimate when settling period has not elapsed."""
    entry = _make_entry(hass)
    coordinator = ZeroGridCoordinator(hass, entry)
    _inject(coordinator, [_pv_west()], {"PV West": 80.0})

    now = time.monotonic()
    # step_time = now → now - now = 0 < settling_time_s (15) → continue
    coordinator._pending_estimates["PV West"] = (-100.0, 0.0, now)

    coordinator._update_estimators({}, now)

    # Still pending because settling time has not elapsed
    assert "PV West" in coordinator._pending_estimates


# ---------------------------------------------------------------------------
# Scenario 30: _write_battery_target — no entity → early return (line 578)
# ---------------------------------------------------------------------------

async def test_scenario_write_battery_target_no_entity(hass: HomeAssistant) -> None:
    """_write_battery_target returns early when battery setpoint entity is not set."""
    entry = _make_entry(hass)
    coordinator = ZeroGridCoordinator(hass, entry)
    assert not coordinator._battery_setpoint_entity

    # Should not raise and should not call any service
    await coordinator._write_battery_target(500.0)
