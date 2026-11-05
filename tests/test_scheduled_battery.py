"""Tests for scheduled-battery (feed-forward) control logic.

Covers:
- Happy flow: schedule sensor drives the setpoint with grid correction
- Non-happy flows: unavailable/unknown/missing sensor, write failure,
  clamping to power limits, exclusion from reactive layers, mixed batteries
"""

from __future__ import annotations

from unittest.mock import AsyncMock, patch

import pytest
from pytest_homeassistant_custom_component.common import MockConfigEntry

from custom_components.zero_grid_controller.battery import (
    BatteryConfig,
    battery_config_from_subentry,
)
from custom_components.zero_grid_controller.const import DOMAIN
from custom_components.zero_grid_controller.coordinator import ZeroGridCoordinator


@pytest.fixture(autouse=True)
def auto_enable_custom_integrations(enable_custom_integrations):
    return


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _entry(deadband_w=10.0, ewm_alpha=1.0, kp=1.0, ki=0.0):
    return MockConfigEntry(
        domain=DOMAIN,
        title="Test",
        data={
            "name": "Test ZGC",
            "grid_import_sensors": ["sensor.grid_import"],
            "grid_export_sensors": ["sensor.grid_export"],
            "kp": kp,
            "ki": ki,
            "deadband_w": deadband_w,
            "ewm_alpha": ewm_alpha,
        },
        options={},
    )


def _make_scheduled_battery(
    name="SchedBat",
    max_charge=5000.0,
    max_discharge=5000.0,
    schedule_sensor="sensor.optimizer_setpoint",
):
    return BatteryConfig(
        subentry_id="sub_sched",
        name=name,
        sensor_entity="sensor.battery_power",
        max_charge_w=max_charge,
        max_discharge_w=max_discharge,
        setpoint_entity="number.battery_sp",
        schedule_sensor_entity=schedule_sensor,
    )


def _make_reactive_battery(name="ReactiveBat", max_charge=5000.0, max_discharge=5000.0):
    return BatteryConfig(
        subentry_id="sub_react",
        name=name,
        sensor_entity="sensor.battery2_power",
        max_charge_w=max_charge,
        max_discharge_w=max_discharge,
        setpoint_entity="number.battery2_sp",
    )


def _grid(hass, import_w=0, export_w=0):
    hass.states.async_set("sensor.grid_import", str(import_w))
    hass.states.async_set("sensor.grid_export", str(export_w))


# ---------------------------------------------------------------------------
# battery_config_from_subentry — schedule sensor field
# ---------------------------------------------------------------------------


def test_battery_config_schedule_sensor_parsed():
    """schedule_sensor_entity is read from subentry data."""
    data = {
        "name": "Bat",
        "battery_sensor": "sensor.bat_power",
        "battery_max_charge_w": 3000.0,
        "battery_max_discharge_w": 3000.0,
        "battery_setpoint_entity": "number.bat_sp",
        "battery_schedule_sensor": "sensor.optimizer_setpoint",
    }
    cfg = battery_config_from_subentry("sub1", data)
    assert cfg.schedule_sensor_entity == "sensor.optimizer_setpoint"


def test_battery_config_no_schedule_sensor_defaults_to_none():
    """When battery_schedule_sensor is absent, schedule_sensor_entity is None."""
    data = {
        "name": "Bat",
        "battery_sensor": "sensor.bat_power",
        "battery_max_charge_w": 3000.0,
        "battery_max_discharge_w": 3000.0,
        "battery_setpoint_entity": "number.bat_sp",
    }
    cfg = battery_config_from_subentry("sub1", data)
    assert cfg.schedule_sensor_entity is None


def test_battery_config_empty_schedule_sensor_becomes_none():
    """An empty string for battery_schedule_sensor is treated as absent."""
    data = {
        "name": "Bat",
        "battery_sensor": "sensor.bat_power",
        "battery_max_charge_w": 3000.0,
        "battery_max_discharge_w": 3000.0,
        "battery_setpoint_entity": "number.bat_sp",
        "battery_schedule_sensor": "",
    }
    cfg = battery_config_from_subentry("sub1", data)
    assert cfg.schedule_sensor_entity is None


# ---------------------------------------------------------------------------
# Happy flow: schedule sensor drives the setpoint
# ---------------------------------------------------------------------------


async def test_scheduled_battery_follows_schedule_charging(hass):
    """With no grid error, the battery setpoint equals the schedule value (charging)."""
    entry = _entry(deadband_w=0.0, ewm_alpha=1.0)
    entry.add_to_hass(hass)
    coordinator = ZeroGridCoordinator(hass, entry)
    coordinator.batteries = [_make_scheduled_battery(max_charge=5000.0)]

    _grid(hass, import_w=0, export_w=0)
    hass.states.async_set("sensor.optimizer_setpoint", "-800")  # schedule: charge 800 W

    with patch.object(
        coordinator._actuators, "write_numeric_entity", new=AsyncMock()
    ) as mock_write:
        result = await coordinator._async_update_data()

    sp = result.battery_setpoints.get("SchedBat")
    assert sp is not None
    assert sp < 0  # charging
    # With grid_w ≈ 0 the correction ≈ 0, so target ≈ schedule
    assert abs(sp - (-800.0)) < 1.0
    mock_write.assert_called()


async def test_scheduled_battery_grid_correction_reduces_charge_when_importing(hass):
    """When importing, the correction pushes the scheduled charge target toward zero."""
    entry = _entry(deadband_w=0.0, ewm_alpha=1.0)
    entry.add_to_hass(hass)
    coordinator = ZeroGridCoordinator(hass, entry)
    coordinator.batteries = [
        _make_scheduled_battery(max_charge=5000.0, max_discharge=5000.0)
    ]

    _grid(hass, import_w=300, export_w=0)
    hass.states.async_set("sensor.optimizer_setpoint", "-500")  # schedule: charge 500 W

    with patch.object(coordinator._actuators, "write_numeric_entity", new=AsyncMock()):
        result = await coordinator._async_update_data()

    sp = result.battery_setpoints.get("SchedBat")
    # correction = +300 (importing), so target ≈ -500 + 300 = -200
    assert sp is not None
    assert -500.0 < sp < 0.0  # less charging than scheduled


async def test_scheduled_battery_grid_correction_increases_charge_when_exporting(hass):
    """When exporting, the correction pushes the scheduled target toward more charging."""
    entry = _entry(deadband_w=0.0, ewm_alpha=1.0)
    entry.add_to_hass(hass)
    coordinator = ZeroGridCoordinator(hass, entry)
    coordinator.batteries = [
        _make_scheduled_battery(max_charge=5000.0, max_discharge=5000.0)
    ]

    _grid(hass, import_w=0, export_w=200)
    hass.states.async_set("sensor.optimizer_setpoint", "-500")  # schedule: charge 500 W

    with patch.object(coordinator._actuators, "write_numeric_entity", new=AsyncMock()):
        result = await coordinator._async_update_data()

    sp = result.battery_setpoints.get("SchedBat")
    # correction = -200 (exporting), so target ≈ -500 + (-200) = -700 (more charging)
    assert sp is not None
    assert sp < -500.0


# ---------------------------------------------------------------------------
# Non-happy flows: sensor unavailable / unknown / missing
# ---------------------------------------------------------------------------


async def test_scheduled_battery_sensor_unavailable_falls_back_to_reactive(hass):
    """An 'unavailable' schedule sensor drops the battery into the reactive
    layer (SoC-aware, incremental) instead of correction-only control."""
    entry = _entry(deadband_w=0.0, ewm_alpha=1.0)
    entry.add_to_hass(hass)
    coordinator = ZeroGridCoordinator(hass, entry)
    coordinator.batteries = [_make_scheduled_battery()]

    _grid(hass, import_w=400, export_w=0)
    hass.states.async_set("sensor.optimizer_setpoint", "unavailable")

    with patch.object(coordinator._actuators, "write_numeric_entity", new=AsyncMock()):
        result = await coordinator._async_update_data()

    sp = result.battery_setpoints.get("SchedBat")
    # Reactive layer: import 400 W, no PV/loads to correct → discharge 400 W
    assert sp is not None
    assert sp > 0


async def test_scheduled_battery_sensor_unknown_falls_back_to_reactive(hass):
    """An 'unknown' schedule sensor drops the battery into the reactive layer."""
    entry = _entry(deadband_w=0.0, ewm_alpha=1.0)
    entry.add_to_hass(hass)
    coordinator = ZeroGridCoordinator(hass, entry)
    coordinator.batteries = [_make_scheduled_battery()]

    _grid(hass, import_w=300, export_w=0)
    hass.states.async_set("sensor.optimizer_setpoint", "unknown")

    with patch.object(coordinator._actuators, "write_numeric_entity", new=AsyncMock()):
        result = await coordinator._async_update_data()

    sp = result.battery_setpoints.get("SchedBat")
    assert sp is not None
    assert sp > 0  # reactive discharge — grid importing, nothing else to correct


async def test_scheduled_battery_sensor_entity_missing_falls_back_to_reactive(
    hass,
):
    """A missing schedule sensor entity drops the battery into the reactive layer."""
    entry = _entry(deadband_w=0.0, ewm_alpha=1.0)
    entry.add_to_hass(hass)
    coordinator = ZeroGridCoordinator(hass, entry)
    # Schedule sensor entity is registered but never set in hass.states
    coordinator.batteries = [
        _make_scheduled_battery(schedule_sensor="sensor.nonexistent")
    ]

    _grid(hass, import_w=200, export_w=0)

    with patch.object(coordinator._actuators, "write_numeric_entity", new=AsyncMock()):
        result = await coordinator._async_update_data()

    sp = result.battery_setpoints.get("SchedBat")
    assert sp is not None
    assert sp > 0  # reactive discharge


async def test_scheduled_battery_sensor_non_numeric_falls_back_to_reactive(hass):
    """A non-numeric schedule sensor state drops the battery into the reactive layer."""
    entry = _entry(deadband_w=0.0, ewm_alpha=1.0)
    entry.add_to_hass(hass)
    coordinator = ZeroGridCoordinator(hass, entry)
    coordinator.batteries = [_make_scheduled_battery()]

    _grid(hass, import_w=100, export_w=0)
    hass.states.async_set("sensor.optimizer_setpoint", "error")

    with patch.object(coordinator._actuators, "write_numeric_entity", new=AsyncMock()):
        result = await coordinator._async_update_data()

    sp = result.battery_setpoints.get("SchedBat")
    assert sp is not None
    assert sp > 0


# ---------------------------------------------------------------------------
# Non-happy flow: write failure does not abort the cycle
# ---------------------------------------------------------------------------


async def test_scheduled_battery_write_failure_does_not_abort_cycle(hass):
    """A write exception for a scheduled battery is logged but the cycle completes."""
    entry = _entry(deadband_w=0.0, ewm_alpha=1.0)
    entry.add_to_hass(hass)
    coordinator = ZeroGridCoordinator(hass, entry)
    coordinator.batteries = [_make_scheduled_battery()]

    _grid(hass, import_w=0, export_w=0)
    hass.states.async_set("sensor.optimizer_setpoint", "-500")

    write_mock = AsyncMock(side_effect=Exception("write error"))
    with patch.object(coordinator._actuators, "write_numeric_entity", new=write_mock):
        result = await coordinator._async_update_data()

    # Cycle must complete — status is returned even though write failed
    assert result is not None
    # Setpoint not updated in internal state after write failure
    assert result.battery_setpoints.get("SchedBat") is None


# ---------------------------------------------------------------------------
# Non-happy flow: clamping to power limits
# ---------------------------------------------------------------------------


async def test_scheduled_battery_clamped_to_max_charge(hass):
    """schedule + large negative correction must not exceed max_charge_w."""
    entry = _entry(deadband_w=0.0, ewm_alpha=1.0)
    entry.add_to_hass(hass)
    coordinator = ZeroGridCoordinator(hass, entry)
    coordinator.batteries = [
        _make_scheduled_battery(max_charge=1000.0, max_discharge=1000.0)
    ]

    # schedule = -800, export correction = -600 → raw = -1400, clamped to -1000
    _grid(hass, import_w=0, export_w=600)
    hass.states.async_set("sensor.optimizer_setpoint", "-800")

    with patch.object(coordinator._actuators, "write_numeric_entity", new=AsyncMock()):
        result = await coordinator._async_update_data()

    sp = result.battery_setpoints.get("SchedBat")
    assert sp is not None
    assert sp >= -1000.0


async def test_scheduled_battery_clamped_to_max_discharge(hass):
    """schedule + large positive correction must not exceed max_discharge_w."""
    entry = _entry(deadband_w=0.0, ewm_alpha=1.0)
    entry.add_to_hass(hass)
    coordinator = ZeroGridCoordinator(hass, entry)
    coordinator.batteries = [
        _make_scheduled_battery(max_charge=1000.0, max_discharge=1000.0)
    ]

    # schedule = +700, import correction = +600 → raw = +1300, clamped to +1000
    _grid(hass, import_w=600, export_w=0)
    hass.states.async_set("sensor.optimizer_setpoint", "700")

    with patch.object(coordinator._actuators, "write_numeric_entity", new=AsyncMock()):
        result = await coordinator._async_update_data()

    sp = result.battery_setpoints.get("SchedBat")
    assert sp is not None
    assert sp <= 1000.0


# ---------------------------------------------------------------------------
# Scheduled battery excluded from reactive layers
# ---------------------------------------------------------------------------


async def test_scheduled_battery_not_charged_by_reactive_layer(hass):
    """A scheduled battery must not also be commanded by the reactive charge layer."""
    entry = _entry(deadband_w=0.0, ewm_alpha=1.0)
    entry.add_to_hass(hass)
    coordinator = ZeroGridCoordinator(hass, entry)
    coordinator.batteries = [_make_scheduled_battery()]

    # schedule says discharge 200 W, grid is exporting 500 W
    _grid(hass, import_w=0, export_w=500)
    hass.states.async_set("sensor.optimizer_setpoint", "200")

    written_values: list[tuple] = []

    async def capture_write(entity_id, value):
        written_values.append((entity_id, value))

    with patch.object(
        coordinator._actuators, "write_numeric_entity", new=capture_write
    ):
        await coordinator._async_update_data()

    # Only one write to the battery setpoint entity (from the schedule layer, not twice)
    battery_writes = [v for e, v in written_values if e == "number.battery_sp"]
    assert len(battery_writes) == 1


async def test_scheduled_battery_not_discharged_by_reactive_layer(hass):
    """A scheduled battery must not also be commanded by the reactive discharge layer."""
    entry = _entry(deadband_w=0.0, ewm_alpha=1.0)
    entry.add_to_hass(hass)
    coordinator = ZeroGridCoordinator(hass, entry)
    coordinator.batteries = [_make_scheduled_battery()]

    # Schedule says charge 300 W, grid is importing 400 W, no PV arrays configured
    _grid(hass, import_w=400, export_w=0)
    hass.states.async_set("sensor.optimizer_setpoint", "-300")

    written_values: list[tuple] = []

    async def capture_write(entity_id, value):
        written_values.append((entity_id, value))

    with patch.object(
        coordinator._actuators, "write_numeric_entity", new=capture_write
    ):
        await coordinator._async_update_data()

    battery_writes = [v for e, v in written_values if e == "number.battery_sp"]
    assert len(battery_writes) == 1


# ---------------------------------------------------------------------------
# Mixed: scheduled + reactive battery coexist independently
# ---------------------------------------------------------------------------


async def test_mixed_scheduled_and_reactive_batteries(hass):
    """Scheduled battery follows optimizer; reactive battery charges from export surplus."""
    entry = _entry(deadband_w=0.0, ewm_alpha=1.0)
    entry.add_to_hass(hass)
    coordinator = ZeroGridCoordinator(hass, entry)
    coordinator.batteries = [
        _make_scheduled_battery(name="Sched", max_charge=3000.0, max_discharge=3000.0),
        _make_reactive_battery(
            name="Reactive", max_charge=2000.0, max_discharge=2000.0
        ),
    ]

    # Grid exporting 400 W; schedule says charge 500 W
    _grid(hass, import_w=0, export_w=400)
    hass.states.async_set("sensor.optimizer_setpoint", "-500")
    hass.states.async_set("sensor.battery_power", "0")
    hass.states.async_set("sensor.battery2_power", "0")

    written: dict[str, float] = {}

    async def capture_write(entity_id, value):
        written[entity_id] = value

    with patch.object(
        coordinator._actuators, "write_numeric_entity", new=capture_write
    ):
        result = await coordinator._async_update_data()

    sched_sp = result.battery_setpoints.get("Sched")
    reactive_sp = result.battery_setpoints.get("Reactive")

    # Scheduled battery: schedule −500 + correction −400 = −900 (clamped to −3000 → −900)
    assert sched_sp is not None
    assert sched_sp < -500.0  # correction pushes it to charge more

    # Reactive battery: charges from the export surplus
    assert reactive_sp is not None
    assert reactive_sp < 0.0  # charging


# ---------------------------------------------------------------------------
# Multiple scheduled batteries: correction split proportionally
# ---------------------------------------------------------------------------


async def test_multiple_scheduled_batteries_correction_split_proportionally(hass):
    """With two scheduled batteries of different capacities, the grid correction
    is distributed in proportion to their max_charge_w."""
    entry = _entry(deadband_w=0.0, ewm_alpha=1.0)
    entry.add_to_hass(hass)
    coordinator = ZeroGridCoordinator(hass, entry)
    bat_a = BatteryConfig(
        subentry_id="a",
        name="BatA",
        sensor_entity="sensor.bat_a",
        max_charge_w=2000.0,
        max_discharge_w=2000.0,
        setpoint_entity="number.bat_a_sp",
        schedule_sensor_entity="sensor.schedule_a",
    )
    bat_b = BatteryConfig(
        subentry_id="b",
        name="BatB",
        sensor_entity="sensor.bat_b",
        max_charge_w=6000.0,
        max_discharge_w=6000.0,
        setpoint_entity="number.bat_b_sp",
        schedule_sensor_entity="sensor.schedule_b",
    )
    coordinator.batteries = [bat_a, bat_b]

    _grid(hass, import_w=800, export_w=0)  # total correction = +800
    hass.states.async_set("sensor.schedule_a", "0")
    hass.states.async_set("sensor.schedule_b", "0")

    written: dict[str, float] = {}

    async def capture_write(entity_id, value):
        written[entity_id] = value

    with patch.object(
        coordinator._actuators, "write_numeric_entity", new=capture_write
    ):
        await coordinator._async_update_data()

    sp_a = written.get("number.bat_a_sp")
    sp_b = written.get("number.bat_b_sp")

    assert sp_a is not None
    assert sp_b is not None
    # BatA has 2000/(2000+6000)=25% share → correction ≈ 200
    # BatB has 6000/(2000+6000)=75% share → correction ≈ 600
    assert abs(sp_a - 200.0) < 5.0
    assert abs(sp_b - 600.0) < 5.0
    # BatB correction is 3× BatA correction
    assert abs(sp_b / sp_a - 3.0) < 0.1


# ---------------------------------------------------------------------------
# Closed loop: scheduled battery + PID must not double-correct
# ---------------------------------------------------------------------------


async def test_scheduled_battery_and_pid_do_not_oscillate(hass):
    """The scheduled correction is fed forward into the PID residual.

    Without the feedforward, the scheduled battery and the PID each corrected
    the full grid error every cycle (combined loop gain ≈ 2) and the closed
    loop diverged into a full-power charge/discharge oscillation.
    """
    from custom_components.zero_grid_controller.array import ArrayConfig

    entry = _entry(deadband_w=10.0, ewm_alpha=1.0, kp=1.0, ki=0.0)
    entry.add_to_hass(hass)
    coordinator = ZeroGridCoordinator(hass, entry)
    coordinator.arrays = [
        ArrayConfig(
            name="Solar",
            output_type="percent",
            setpoint_entity="number.solar",
            w_per_unit=50.0,
            calibration_confidence="estimated",
            setpoint_min=0.0,
            setpoint_max=100.0,
            settling_time_s=0,
        )
    ]
    coordinator.batteries = [_make_scheduled_battery()]
    coordinator._engine._current_setpoints["Solar"] = 40.0  # limited to 2000 W

    plant = {"sp": 40.0, "bat": 0.0, "bat_target": 0.0}
    clock = {"t": 1000.0}

    class _FakeTime:
        @staticmethod
        def monotonic() -> float:
            return clock["t"]

    def publish() -> float:
        pv = min(5000.0, plant["sp"] * 50.0)
        grid = 3000.0 - pv - plant["bat"]
        _grid(hass, import_w=max(0.0, grid), export_w=max(0.0, -grid))
        hass.states.async_set("sensor.battery_power", str(plant["bat"]))
        hass.states.async_set("sensor.optimizer_setpoint", "0")
        return grid

    async def on_sp(array, value):
        plant["sp"] = value

    async def on_num(entity_id, value):
        if entity_id == "number.battery_sp":
            plant["bat_target"] = value

    publish()
    grid_trace: list[float] = []
    with (
        patch("custom_components.zero_grid_controller.control_engine.time", _FakeTime),
        patch.object(
            coordinator._actuators,
            "write_setpoint",
            new=AsyncMock(side_effect=on_sp),
        ),
        patch.object(
            coordinator._actuators,
            "write_numeric_entity",
            new=AsyncMock(side_effect=on_num),
        ),
    ):
        for _ in range(15):
            clock["t"] += 5.0
            await coordinator._async_update_data()
            plant["bat"] = plant["bat_target"]  # battery follows instantly
            grid_trace.append(publish())

    # Converged, not oscillating: the last cycles stay within the deadband
    assert all(abs(g) <= 10.0 for g in grid_trace[-5:]), grid_trace


# ---------------------------------------------------------------------------
# SoC limits bound the corrected schedule target
# ---------------------------------------------------------------------------


async def test_scheduled_battery_soc_max_blocks_charging(hass):
    """At/above max SoC the corrected target is clamped to ≥ 0 (no charge)."""
    entry = _entry(deadband_w=0.0, ewm_alpha=1.0)
    entry.add_to_hass(hass)
    coordinator = ZeroGridCoordinator(hass, entry)
    battery = _make_scheduled_battery()
    battery.soc_sensor_entity = "sensor.battery_soc"
    battery.max_soc = 95.0
    coordinator.batteries = [battery]

    hass.states.async_set("sensor.battery_soc", "96")
    hass.states.async_set("sensor.optimizer_setpoint", "-2000")  # optimizer: charge
    _grid(hass, import_w=0, export_w=500)

    with patch.object(coordinator._actuators, "write_numeric_entity", new=AsyncMock()):
        result = await coordinator._async_update_data()

    sp = result.battery_setpoints.get("SchedBat")
    assert sp is not None
    assert sp >= 0, "Full battery must not be commanded to charge"


async def test_scheduled_battery_soc_min_blocks_discharge(hass):
    """At/below min SoC the corrected target is clamped to ≤ 0 (no discharge)."""
    entry = _entry(deadband_w=0.0, ewm_alpha=1.0)
    entry.add_to_hass(hass)
    coordinator = ZeroGridCoordinator(hass, entry)
    battery = _make_scheduled_battery()
    battery.soc_sensor_entity = "sensor.battery_soc"
    battery.min_soc = 10.0
    coordinator.batteries = [battery]

    hass.states.async_set("sensor.battery_soc", "8")
    hass.states.async_set("sensor.optimizer_setpoint", "2000")  # optimizer: discharge
    _grid(hass, import_w=500, export_w=0)

    with patch.object(coordinator._actuators, "write_numeric_entity", new=AsyncMock()):
        result = await coordinator._async_update_data()

    sp = result.battery_setpoints.get("SchedBat")
    assert sp is not None
    assert sp <= 0, "Empty battery must not be commanded to discharge"


async def test_scheduled_battery_write_skipped_when_unchanged(hass):
    """An unchanged scheduled target (< 1 W delta) is not rewritten every cycle."""
    entry = _entry(deadband_w=0.0, ewm_alpha=1.0)
    entry.add_to_hass(hass)
    coordinator = ZeroGridCoordinator(hass, entry)
    coordinator.batteries = [_make_scheduled_battery()]

    hass.states.async_set("sensor.optimizer_setpoint", "-1500")
    hass.states.async_set("sensor.battery_power", "-1500")
    _grid(hass, import_w=0, export_w=0)
    hass.states.async_set("sensor.grid_import", "0.4")  # tiny error < 1 W target delta

    with patch.object(
        coordinator._actuators, "write_numeric_entity", new=AsyncMock()
    ) as mock_write:
        await coordinator._async_update_data()
        first_calls = mock_write.await_count
        await coordinator._async_update_data()

    assert mock_write.await_count == first_calls, (
        "Unchanged scheduled target must not be rewritten"
    )
