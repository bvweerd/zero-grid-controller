"""Closed-loop simulation tests for the battery control layer.

These simulate a small plant (PV + battery + house consumption) over many
control cycles and assert that the controller actually converges to zero
grid power — the scenarios where the pre-rewrite battery layer oscillated
or settled at a permanent export.
"""

from __future__ import annotations

from unittest.mock import AsyncMock, patch

import pytest
from pytest_homeassistant_custom_component.common import MockConfigEntry

from custom_components.zero_grid_controller.array import ArrayConfig
from custom_components.zero_grid_controller.battery import BatteryConfig
from custom_components.zero_grid_controller.const import DOMAIN
from custom_components.zero_grid_controller.coordinator import ZeroGridCoordinator

DEADBAND_W = 10.0
CYCLE_S = 5.0


@pytest.fixture(autouse=True)
def auto_enable_custom_integrations(enable_custom_integrations):
    return


def _make_entry(kp=1.0, ki=0.0) -> MockConfigEntry:
    return MockConfigEntry(
        domain=DOMAIN,
        title="Test",
        data={
            "name": "Test ZGC",
            "grid_import_sensors": ["sensor.grid_import"],
            "grid_export_sensors": ["sensor.grid_export"],
            "kp": kp,
            "ki": ki,
            "deadband_w": DEADBAND_W,
            "ewm_alpha": 1.0,
        },
        options={},
    )


def _make_array(w_per_unit=50.0) -> ArrayConfig:
    return ArrayConfig(
        name="Solar",
        output_type="percent",
        setpoint_entity="number.solar_limit",
        w_per_unit=w_per_unit,
        calibration_confidence="estimated",
        setpoint_min=0.0,
        setpoint_max=100.0,
        settling_time_s=0,
    )


def _make_battery(max_charge_w=5000.0, max_discharge_w=5000.0, **kwargs):
    return BatteryConfig(
        subentry_id="bat-1",
        name="Battery",
        sensor_entity="sensor.battery_power",
        max_charge_w=max_charge_w,
        max_discharge_w=max_discharge_w,
        setpoint_entity="number.battery_sp",
        **kwargs,
    )


class Plant:
    """Tiny physical model: grid = consumption − PV delivered − battery power.

    Battery sign convention: negative = charging, positive = discharging.
    """

    def __init__(
        self,
        hass,
        consumption_w: float,
        pv_available_w: float,
        array_w_per_unit: float = 50.0,
        array_sp: float = 100.0,
        battery_actual_w: float = 0.0,
        battery_ramp_w: float = 1500.0,
    ) -> None:
        self.hass = hass
        self.consumption_w = consumption_w
        self.pv_available_w = pv_available_w
        self.array_w_per_unit = array_w_per_unit
        self.array_sp = array_sp
        self.battery_actual_w = battery_actual_w
        self.battery_target_w = battery_actual_w
        self.battery_ramp_w = battery_ramp_w
        self.clock = {"t": 1000.0}
        self.publish()

    async def on_write_setpoint(self, array, value):
        self.array_sp = value

    async def on_write_numeric(self, entity_id, value):
        if entity_id == "number.battery_sp":
            self.battery_target_w = value

    @property
    def pv_delivered_w(self) -> float:
        return min(self.pv_available_w, self.array_sp * self.array_w_per_unit)

    @property
    def grid_w(self) -> float:
        return self.consumption_w - self.pv_delivered_w - self.battery_actual_w

    def step(self) -> None:
        """Advance battery ramp one cycle and publish new sensor states."""
        delta = self.battery_target_w - self.battery_actual_w
        if abs(delta) <= self.battery_ramp_w:
            self.battery_actual_w = self.battery_target_w
        else:
            self.battery_actual_w += self.battery_ramp_w * (1 if delta > 0 else -1)
        self.publish()

    def publish(self) -> None:
        grid = self.grid_w
        self.hass.states.async_set("sensor.grid_import", str(max(0.0, grid)))
        self.hass.states.async_set("sensor.grid_export", str(max(0.0, -grid)))
        self.hass.states.async_set("sensor.battery_power", str(self.battery_actual_w))
        self.hass.states.async_set("number.battery_sp", str(self.battery_target_w))


async def _run_cycles(coordinator, plant, n: int) -> None:
    """Run n control cycles with a fake 5 s clock and the plant in the loop."""
    clock = plant.clock

    class _FakeTime:
        @staticmethod
        def monotonic() -> float:
            return clock["t"]

    with (
        patch(
            "custom_components.zero_grid_controller.coordinator.time",
            _FakeTime,
        ),
        patch.object(
            coordinator._actuators,
            "write_setpoint",
            new=AsyncMock(side_effect=plant.on_write_setpoint),
        ),
        patch.object(
            coordinator._actuators,
            "write_numeric_entity",
            new=AsyncMock(side_effect=plant.on_write_numeric),
        ),
    ):
        for _ in range(n):
            clock["t"] += CYCLE_S
            await coordinator._async_update_data()
            plant.step()


async def test_battery_absorbs_export_without_curtailment(hass):
    """Surplus within battery capacity: grid → 0, PV stays fully open."""
    entry = _make_entry()
    entry.add_to_hass(hass)
    coordinator = ZeroGridCoordinator(hass, entry)
    coordinator.arrays = [_make_array()]
    coordinator.batteries = [_make_battery(max_charge_w=5000.0)]
    coordinator._current_setpoints["Solar"] = 100.0

    # 3000 W PV, 500 W consumption → 2500 W surplus, well within battery cap
    plant = Plant(hass, consumption_w=500.0, pv_available_w=3000.0)

    await _run_cycles(coordinator, plant, 10)

    assert abs(plant.grid_w) <= DEADBAND_W
    assert plant.array_sp == 100.0, "PV must not be curtailed within battery capacity"
    assert plant.battery_actual_w == pytest.approx(-2500.0, abs=DEADBAND_W)


async def test_battery_cap_exceeded_curtails_remainder(hass):
    """Surplus beyond battery capacity: battery at cap, PV curtails the rest."""
    entry = _make_entry()
    entry.add_to_hass(hass)
    coordinator = ZeroGridCoordinator(hass, entry)
    coordinator.arrays = [_make_array(w_per_unit=60.0)]
    coordinator.batteries = [_make_battery(max_charge_w=2000.0)]
    coordinator._current_setpoints["Solar"] = 100.0

    # 6000 W PV, no consumption → 4000 W beyond the 2000 W battery cap
    plant = Plant(
        hass,
        consumption_w=0.0,
        pv_available_w=6000.0,
        array_w_per_unit=60.0,
        battery_ramp_w=2000.0,
    )

    await _run_cycles(coordinator, plant, 15)

    assert abs(plant.grid_w) <= DEADBAND_W
    assert plant.array_sp < 100.0, "PV must be curtailed beyond battery capacity"
    assert plant.battery_actual_w == pytest.approx(-2000.0, abs=50.0)


async def test_import_while_charging_backs_off_charge_first(hass):
    """Import while charging: the charge command backs off, PV stays at max.

    The pre-rewrite code reset the charge to 0 on any import (oscillation)
    and double-counted the battery in the residual (PID fighting the
    battery); this asserts smooth convergence to zero grid instead.
    """
    entry = _make_entry()
    entry.add_to_hass(hass)
    coordinator = ZeroGridCoordinator(hass, entry)
    coordinator.arrays = [_make_array()]
    coordinator.batteries = [_make_battery(max_charge_w=5000.0)]
    coordinator._current_setpoints["Solar"] = 100.0
    coordinator._current_battery_setpoints["Battery"] = -2000.0

    # Battery charging 2000 W; PV 3000 W, consumption 1500 W → 500 W import,
    # but the true surplus is 1500 W: correct response is charging at 1500 W.
    plant = Plant(
        hass,
        consumption_w=1500.0,
        pv_available_w=3000.0,
        battery_actual_w=-2000.0,
    )

    await _run_cycles(coordinator, plant, 10)

    assert abs(plant.grid_w) <= DEADBAND_W
    assert plant.array_sp == 100.0, "PV must stay open while the battery backs off"
    assert plant.battery_actual_w == pytest.approx(-1500.0, abs=DEADBAND_W)


async def test_unresponsive_battery_curtails_after_trust_window(hass):
    """A battery that never follows commands cannot block curtailment forever.

    Within the trust window the commanded response is fed forward (no
    curtailment); once it expires the measured power is the truth and PV
    curtails the export.
    """
    entry = _make_entry()
    entry.add_to_hass(hass)
    coordinator = ZeroGridCoordinator(hass, entry)
    coordinator.arrays = [_make_array()]
    coordinator.batteries = [_make_battery(max_charge_w=5000.0)]
    coordinator._current_setpoints["Solar"] = 100.0

    # 2000 W surplus; the battery accepts commands but never moves
    plant = Plant(
        hass,
        consumption_w=500.0,
        pv_available_w=2500.0,
        battery_ramp_w=0.0,
    )

    await _run_cycles(coordinator, plant, 5)
    assert plant.array_sp == 100.0, (
        "PV must not be curtailed while the battery command is still pending"
    )

    await _run_cycles(coordinator, plant, 20)
    assert abs(plant.grid_w) <= DEADBAND_W
    assert plant.array_sp < 100.0, (
        "After the trust window expires, PV must curtail the export"
    )


async def test_soc_at_max_blocks_charging(hass):
    """A full battery is not commanded to charge; PV curtails instead."""
    entry = _make_entry()
    entry.add_to_hass(hass)
    coordinator = ZeroGridCoordinator(hass, entry)
    coordinator.arrays = [_make_array()]
    coordinator.batteries = [
        _make_battery(
            max_charge_w=5000.0,
            soc_sensor_entity="sensor.battery_soc",
            max_soc=95.0,
        )
    ]
    coordinator._current_setpoints["Solar"] = 100.0
    hass.states.async_set("sensor.battery_soc", "96")

    plant = Plant(hass, consumption_w=500.0, pv_available_w=2500.0)

    await _run_cycles(coordinator, plant, 10)

    assert abs(plant.grid_w) <= DEADBAND_W
    assert plant.battery_target_w == 0.0, "Full battery must not be commanded to charge"
    assert plant.array_sp < 100.0, "PV must curtail when the battery is full"


async def test_soc_at_min_blocks_discharge(hass):
    """An empty battery is not commanded to discharge on import."""
    entry = _make_entry()
    entry.add_to_hass(hass)
    coordinator = ZeroGridCoordinator(hass, entry)
    coordinator.arrays = [_make_array()]
    coordinator.batteries = [
        _make_battery(
            max_discharge_w=5000.0,
            soc_sensor_entity="sensor.battery_soc",
            min_soc=10.0,
        )
    ]
    # PV already at max → discharge would normally be allowed
    coordinator._current_setpoints["Solar"] = 100.0
    hass.states.async_set("sensor.battery_soc", "8")

    # 1000 W import that PV cannot cover
    plant = Plant(hass, consumption_w=3000.0, pv_available_w=2000.0)

    await _run_cycles(coordinator, plant, 10)

    assert plant.battery_target_w == 0.0, (
        "Empty battery must not be commanded to discharge"
    )


async def test_discharge_only_when_pv_maxed(hass):
    """Battery discharge is the last resort: only when PV is at max."""
    entry = _make_entry()
    entry.add_to_hass(hass)
    coordinator = ZeroGridCoordinator(hass, entry)
    coordinator.arrays = [_make_array()]
    coordinator.batteries = [_make_battery(max_discharge_w=5000.0)]
    # PV curtailed at 40% with available production → opening covers the import
    coordinator._current_setpoints["Solar"] = 40.0

    plant = Plant(
        hass,
        consumption_w=3000.0,
        pv_available_w=3000.0,
        array_sp=40.0,
    )

    await _run_cycles(coordinator, plant, 10)

    assert abs(plant.grid_w) <= DEADBAND_W
    assert plant.battery_target_w == 0.0, (
        "Battery must not discharge while PV still has headroom"
    )
    assert plant.array_sp > 40.0, "PV must open to cover the import"


async def test_import_with_pv_maxed_discharges_battery(hass):
    """When PV is at max and import persists, the battery discharges to zero grid."""
    entry = _make_entry()
    entry.add_to_hass(hass)
    coordinator = ZeroGridCoordinator(hass, entry)
    coordinator.arrays = [_make_array()]
    coordinator.batteries = [_make_battery(max_discharge_w=5000.0)]
    coordinator._current_setpoints["Solar"] = 100.0

    # PV produces all it can (2000 W), consumption 3000 W → 1000 W import
    plant = Plant(hass, consumption_w=3000.0, pv_available_w=2000.0)

    await _run_cycles(coordinator, plant, 10)

    assert abs(plant.grid_w) <= DEADBAND_W
    assert plant.battery_actual_w == pytest.approx(1000.0, abs=DEADBAND_W)


async def test_pv_recovery_reopens_curtailed_arrays(hass):
    """Curtailed PV gradually reopens while the battery has spare capacity.

    With the grid balanced at zero there is no error signal, so without the
    recovery bias the curtailment would persist and the spare battery
    capacity would be wasted.
    """
    entry = _make_entry()
    entry.add_to_hass(hass)
    coordinator = ZeroGridCoordinator(hass, entry)
    coordinator.arrays = [_make_array()]
    coordinator.batteries = [_make_battery(max_charge_w=5000.0)]
    coordinator._current_setpoints["Solar"] = 40.0
    coordinator._current_battery_setpoints["Battery"] = -1000.0

    # Balanced: 1000 W consumption, PV limited to 2000 W, battery charging
    # 1000 W → grid = 0.  PV could produce 5000 W; battery has 4000 W spare.
    plant = Plant(
        hass,
        consumption_w=1000.0,
        pv_available_w=5000.0,
        array_sp=40.0,
        battery_actual_w=-1000.0,
        battery_ramp_w=2000.0,
    )

    await _run_cycles(coordinator, plant, 60)

    assert plant.array_sp > 40.0, "Curtailed PV must reopen into spare battery capacity"
    assert plant.battery_actual_w < -1000.0, (
        "The recovered production must go into the battery"
    )
    assert abs(plant.grid_w) <= DEADBAND_W
