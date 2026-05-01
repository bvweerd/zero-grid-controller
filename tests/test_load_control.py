"""Tests for controllable load control logic in ZeroGridCoordinator."""

from __future__ import annotations

from unittest.mock import AsyncMock, patch

import pytest
from pytest_homeassistant_custom_component.common import MockConfigEntry

from custom_components.zero_grid_controller.const import (
    DOMAIN,
    LOAD_TYPE_NUMERIC,
    LOAD_TYPE_SWITCH,
    STATUS_ACTIVE,
)
from custom_components.zero_grid_controller.coordinator import ZeroGridCoordinator
from custom_components.zero_grid_controller.load import LoadConfig


@pytest.fixture(autouse=True)
def auto_enable_custom_integrations(enable_custom_integrations):
    return


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _make_entry(deadband_w=0.0, ewm_alpha=1.0, kp=1.0, ki=0.0, kd=0.0):
    data = {
        "name": "Test ZGC",
        "grid_import_sensors": ["sensor.grid_import"],
        "grid_export_sensors": ["sensor.grid_export"],
        "deadband_w": deadband_w,
        "ewm_alpha": ewm_alpha,
        "kp": kp,
        "ki": ki,
        "kd": kd,
        "controller_enabled": True,
    }
    return MockConfigEntry(domain=DOMAIN, title="Test", data=data, options={})


def _set_state(hass, entity_id, value):
    hass.states.async_set(entity_id, str(value))


def _numeric_load(
    name="EV",
    setpoint_entity="number.ev",
    setpoint_min=0.0,
    setpoint_max=16.0,
    w_per_unit=230.0,
    absolute_min_w=None,
    priority=1,
    settling_time_s=0,
) -> LoadConfig:
    return LoadConfig(
        name=name,
        load_type=LOAD_TYPE_NUMERIC,
        setpoint_entity=setpoint_entity,
        priority=priority,
        setpoint_min=setpoint_min,
        setpoint_max=setpoint_max,
        w_per_unit=w_per_unit,
        absolute_min_w=absolute_min_w,
        settling_time_s=settling_time_s,
    )


def _switch_load(
    name="Boiler",
    setpoint_entity="switch.boiler",
    power_w=2000.0,
    priority=1,
    switch_debounce_s=30,
) -> LoadConfig:
    return LoadConfig(
        name=name,
        load_type=LOAD_TYPE_SWITCH,
        setpoint_entity=setpoint_entity,
        priority=priority,
        power_w=power_w,
        switch_debounce_s=switch_debounce_s,
    )


# ---------------------------------------------------------------------------
# LoadConfig unit tests
# ---------------------------------------------------------------------------


def test_load_config_headroom_increase():
    load = _numeric_load(setpoint_min=0.0, setpoint_max=16.0, w_per_unit=230.0)
    assert load.headroom_increase_w(6.0) == pytest.approx(2300.0)
    assert load.headroom_increase_w(16.0) == 0.0


def test_load_config_headroom_decrease():
    load = _numeric_load(setpoint_min=0.0, setpoint_max=16.0, w_per_unit=230.0)
    assert load.headroom_decrease_w(6.0) == pytest.approx(1380.0)
    assert load.headroom_decrease_w(0.0) == 0.0


def test_snap_setpoint_snap_down():
    """When decreasing below absolute_min_w, snap to setpoint_min (off)."""
    load = _numeric_load(w_per_unit=230.0, absolute_min_w=1380.0)
    assert load.snap_setpoint(4.0, increasing=False) == 0.0
    assert load.snap_setpoint(0.0, increasing=False) == 0.0
    assert load.snap_setpoint(7.0, increasing=False) == 7.0


def test_snap_setpoint_snap_up():
    """When increasing from 0, snap up to minimum active setpoint."""
    load = _numeric_load(w_per_unit=230.0, absolute_min_w=1380.0)
    # ceil(1380/230) = 6
    assert load.snap_setpoint(3.0, increasing=True) == 6.0
    assert load.snap_setpoint(7.0, increasing=True) == 7.0


# ---------------------------------------------------------------------------
# _distribute_to_numeric_loads unit tests
# ---------------------------------------------------------------------------


async def test_numeric_load_increases_on_export(hass):
    """Numeric load setpoint increases when exporting (surplus)."""
    entry = _make_entry(kp=1.0)
    entry.add_to_hass(hass)

    coordinator = ZeroGridCoordinator(hass, entry)
    coordinator.loads = [
        _numeric_load(setpoint_min=0.0, setpoint_max=16.0, w_per_unit=230.0)
    ]
    hass.states.async_set("number.ev", "0")

    with patch.object(
        coordinator._actuators, "write_numeric_entity", new=AsyncMock()
    ) as mock_write:
        # -1150 W: surplus → increase EV load
        remaining = await coordinator._distribute_to_numeric_loads(-1150.0, now=0.0)

    mock_write.assert_awaited()
    written_value = mock_write.call_args[0][1]
    assert written_value > 0
    assert remaining == pytest.approx(0.0, abs=1.0)  # fully absorbed


async def test_numeric_load_decreases_on_import(hass):
    """Numeric load setpoint decreases when importing (deficit)."""
    entry = _make_entry(kp=1.0)
    entry.add_to_hass(hass)

    coordinator = ZeroGridCoordinator(hass, entry)
    coordinator.loads = [
        _numeric_load(setpoint_min=0.0, setpoint_max=16.0, w_per_unit=230.0)
    ]
    coordinator._current_load_setpoints["EV"] = 10.0
    hass.states.async_set("number.ev", "10")

    with patch.object(
        coordinator._actuators, "write_numeric_entity", new=AsyncMock()
    ) as mock_write:
        # +1150 W: deficit → reduce EV load
        await coordinator._distribute_to_numeric_loads(1150.0, now=0.0)

    mock_write.assert_awaited()
    written_value = mock_write.call_args[0][1]
    assert written_value < 10.0


async def test_numeric_load_snap_to_min_on_decrease(hass):
    """EV charger snaps to 0 when below absolute_min_w during decrease."""
    entry = _make_entry(kp=1.0)
    entry.add_to_hass(hass)

    coordinator = ZeroGridCoordinator(hass, entry)
    coordinator.loads = [
        _numeric_load(
            w_per_unit=230.0, absolute_min_w=1380.0, setpoint_min=0.0, setpoint_max=16.0
        )
    ]
    coordinator._current_load_setpoints["EV"] = 6.0
    hass.states.async_set("number.ev", "6")

    with patch.object(
        coordinator._actuators, "write_numeric_entity", new=AsyncMock()
    ) as mock_write:
        # 920 W deficit → reduce from 6A; would go to 2A (< 6A min) → snap to 0
        await coordinator._distribute_to_numeric_loads(920.0, now=0.0)

    mock_write.assert_awaited()
    written_value = mock_write.call_args[0][1]
    assert written_value == 0.0


async def test_load_priority_fills_highest_first(hass):
    """With two loads, highest priority (lowest number) is filled first on export."""
    entry = _make_entry(kp=1.0)
    entry.add_to_hass(hass)

    coordinator = ZeroGridCoordinator(hass, entry)
    coordinator.loads = [
        _numeric_load(
            "EV",
            "number.ev",
            setpoint_min=0,
            setpoint_max=16,
            w_per_unit=230.0,
            priority=1,
        ),
        _numeric_load(
            "Pool",
            "number.pool",
            setpoint_min=0,
            setpoint_max=10,
            w_per_unit=500.0,
            priority=2,
        ),
    ]
    hass.states.async_set("number.ev", "0")
    hass.states.async_set("number.pool", "0")

    write_calls: list[tuple[str, float]] = []

    async def mock_write(entity_id, value):
        write_calls.append((entity_id, value))

    with patch.object(
        coordinator._actuators, "write_numeric_entity", side_effect=mock_write
    ):
        # 2000 W surplus: EV can absorb all 2000 W (max 3680 W)
        await coordinator._distribute_to_numeric_loads(-2000.0, now=0.0)

    ev_writes = [v for eid, v in write_calls if "ev" in eid]
    pool_writes = [v for eid, v in write_calls if "pool" in eid]

    assert ev_writes, "EV (priority 1) should have received a setpoint"
    ev_absorbed = ev_writes[0] * 230.0
    if ev_absorbed >= 2000:
        # EV absorbed the full surplus; pool should not have been increased
        assert not pool_writes


# ---------------------------------------------------------------------------
# _apply_load_switch_hysteresis unit tests
# ---------------------------------------------------------------------------


async def test_switch_load_turns_on_when_surplus(hass):
    """Switch load turns on when export surplus >= power_w."""
    entry = _make_entry()
    entry.add_to_hass(hass)

    coordinator = ZeroGridCoordinator(hass, entry)
    coordinator.loads = [_switch_load(power_w=2000.0, switch_debounce_s=30)]
    hass.states.async_set("switch.boiler", "off")

    with patch.object(
        coordinator._actuators, "write_switch_entity", new=AsyncMock()
    ) as mock_write:
        # -2500 W residual: surplus of 2500 >= power_w 2000
        new_residual = await coordinator._apply_load_switch_hysteresis(-2500.0, now=0.0)

    mock_write.assert_awaited_once_with("switch.boiler", True)
    assert coordinator._current_load_setpoints.get("Boiler") == 1.0
    assert new_residual == pytest.approx(-500.0)  # feedforward: -2500 + 2000


async def test_switch_load_stays_off_when_insufficient_surplus(hass):
    """Switch load does not turn on when surplus < power_w."""
    entry = _make_entry()
    entry.add_to_hass(hass)

    coordinator = ZeroGridCoordinator(hass, entry)
    coordinator.loads = [_switch_load(power_w=2000.0)]
    hass.states.async_set("switch.boiler", "off")

    with patch.object(
        coordinator._actuators, "write_switch_entity", new=AsyncMock()
    ) as mock_write:
        # -1500 W residual: surplus 1500 < power_w 2000 → stay off
        await coordinator._apply_load_switch_hysteresis(-1500.0, now=0.0)

    mock_write.assert_not_awaited()


async def test_switch_load_turns_off_when_importing(hass):
    """Switch load turns off when grid imports (residual >= 0)."""
    entry = _make_entry()
    entry.add_to_hass(hass)

    coordinator = ZeroGridCoordinator(hass, entry)
    coordinator.loads = [_switch_load(power_w=2000.0, switch_debounce_s=0)]
    coordinator._current_load_setpoints["Boiler"] = 1.0
    hass.states.async_set("switch.boiler", "on")

    with patch.object(
        coordinator._actuators, "write_switch_entity", new=AsyncMock()
    ) as mock_write:
        new_residual = await coordinator._apply_load_switch_hysteresis(500.0, now=0.0)

    mock_write.assert_awaited_once_with("switch.boiler", False)
    assert coordinator._current_load_setpoints.get("Boiler") == 0.0
    assert new_residual == pytest.approx(-1500.0)  # feedforward: 500 - 2000


async def test_switch_load_debounce(hass):
    """Switch load respects debounce after turning on."""
    entry = _make_entry()
    entry.add_to_hass(hass)

    coordinator = ZeroGridCoordinator(hass, entry)
    coordinator.loads = [_switch_load(power_w=2000.0, switch_debounce_s=30)]
    hass.states.async_set("switch.boiler", "off")

    with patch.object(
        coordinator._actuators, "write_switch_entity", new=AsyncMock()
    ) as mock_write:
        await coordinator._apply_load_switch_hysteresis(-2500.0, now=0.0)
        assert mock_write.await_count == 1
        # Second call within debounce window (now=10 < settling_until=30)
        await coordinator._apply_load_switch_hysteresis(-2500.0, now=10.0)
        assert mock_write.await_count == 1


# ---------------------------------------------------------------------------
# _distribute_to_numeric_loads edge cases
# ---------------------------------------------------------------------------


async def test_numeric_load_at_max_no_write_on_surplus(hass):
    """Load already at setpoint_max: no write, full surplus returned."""
    entry = _make_entry(kp=1.0)
    entry.add_to_hass(hass)

    coordinator = ZeroGridCoordinator(hass, entry)
    coordinator.loads = [
        _numeric_load(setpoint_min=0.0, setpoint_max=16.0, w_per_unit=230.0)
    ]
    coordinator._current_load_setpoints["EV"] = 16.0  # already at max
    hass.states.async_set("number.ev", "16")

    with patch.object(
        coordinator._actuators, "write_numeric_entity", new=AsyncMock()
    ) as mock_write:
        remaining = await coordinator._distribute_to_numeric_loads(-1000.0, now=0.0)

    mock_write.assert_not_awaited()
    assert remaining == pytest.approx(-1000.0)


async def test_numeric_load_at_min_no_write_on_import(hass):
    """Load already at setpoint_min: no write, full deficit returned."""
    entry = _make_entry(kp=1.0)
    entry.add_to_hass(hass)

    coordinator = ZeroGridCoordinator(hass, entry)
    coordinator.loads = [
        _numeric_load(setpoint_min=0.0, setpoint_max=16.0, w_per_unit=230.0)
    ]
    coordinator._current_load_setpoints["EV"] = 0.0  # already at min
    hass.states.async_set("number.ev", "0")

    with patch.object(
        coordinator._actuators, "write_numeric_entity", new=AsyncMock()
    ) as mock_write:
        remaining = await coordinator._distribute_to_numeric_loads(500.0, now=0.0)

    mock_write.assert_not_awaited()
    assert remaining == pytest.approx(500.0)


async def test_numeric_load_settling_time_skipped(hass):
    """Load within settling window is excluded from active set: no write."""
    entry = _make_entry(kp=1.0)
    entry.add_to_hass(hass)

    coordinator = ZeroGridCoordinator(hass, entry)
    coordinator.loads = [_numeric_load(settling_time_s=30)]
    coordinator._current_load_setpoints["EV"] = 0.0
    coordinator._load_settling_until["EV"] = 100.0  # settling until t=100
    hass.states.async_set("number.ev", "0")

    with patch.object(
        coordinator._actuators, "write_numeric_entity", new=AsyncMock()
    ) as mock_write:
        remaining = await coordinator._distribute_to_numeric_loads(
            -1000.0, now=50.0
        )  # now < 100

    mock_write.assert_not_awaited()
    assert remaining == pytest.approx(-1000.0)


async def test_numeric_load_tiny_surplus_rounds_to_zero_no_write(hass):
    """Surplus smaller than one unit (rounds to 0 delta_units): no write."""
    entry = _make_entry(kp=1.0)
    entry.add_to_hass(hass)

    coordinator = ZeroGridCoordinator(hass, entry)
    coordinator.loads = [
        _numeric_load(setpoint_min=0.0, setpoint_max=16.0, w_per_unit=230.0)
    ]
    coordinator._current_load_setpoints["EV"] = 0.0
    hass.states.async_set("number.ev", "0")

    with patch.object(
        coordinator._actuators, "write_numeric_entity", new=AsyncMock()
    ) as mock_write:
        # 100 W surplus, w_per_unit=230: round(100/230)=0 → skip
        remaining = await coordinator._distribute_to_numeric_loads(-100.0, now=0.0)

    mock_write.assert_not_awaited()
    assert remaining == pytest.approx(-100.0)


async def test_numeric_load_priority_cascade_first_saturated(hass):
    """When priority-1 load is full, surplus spills to priority-2."""
    entry = _make_entry(kp=1.0)
    entry.add_to_hass(hass)

    coordinator = ZeroGridCoordinator(hass, entry)
    coordinator.loads = [
        _numeric_load(
            "EV",
            "number.ev",
            setpoint_min=0,
            setpoint_max=10,
            w_per_unit=230.0,
            priority=1,
        ),
        _numeric_load(
            "Pool",
            "number.pool",
            setpoint_min=0,
            setpoint_max=10,
            w_per_unit=500.0,
            priority=2,
        ),
    ]
    # EV already at max → headroom = 0
    coordinator._current_load_setpoints["EV"] = 10.0
    coordinator._current_load_setpoints["Pool"] = 0.0
    hass.states.async_set("number.ev", "10")
    hass.states.async_set("number.pool", "0")

    write_calls: list[tuple[str, float]] = []

    async def mock_write(entity_id, value):
        write_calls.append((entity_id, value))

    with patch.object(
        coordinator._actuators, "write_numeric_entity", side_effect=mock_write
    ):
        await coordinator._distribute_to_numeric_loads(-2000.0, now=0.0)

    ev_writes = [v for eid, v in write_calls if "ev" in eid]
    pool_writes = [v for eid, v in write_calls if "pool" in eid]
    assert not ev_writes, "EV is at max, should not be written"
    assert pool_writes, "Pool (priority 2) should absorb surplus when EV is full"
    assert pool_writes[0] > 0


async def test_numeric_load_snap_up_over_absorbs(hass):
    """Snap up to absolute_min_w can over-absorb surplus (remaining goes positive)."""
    entry = _make_entry(kp=1.0)
    entry.add_to_hass(hass)

    coordinator = ZeroGridCoordinator(hass, entry)
    # absolute_min_w=1380 → ceil(1380/230)=6A minimum when on
    coordinator.loads = [
        _numeric_load(
            w_per_unit=230.0, absolute_min_w=1380.0, setpoint_min=0.0, setpoint_max=16.0
        )
    ]
    coordinator._current_load_setpoints["EV"] = 0.0
    hass.states.async_set("number.ev", "0")

    with patch.object(
        coordinator._actuators, "write_numeric_entity", new=AsyncMock()
    ) as mock_write:
        # -300 W surplus: would request 1-2A, snap up to 6A (1380W) → over-absorbs
        remaining = await coordinator._distribute_to_numeric_loads(-300.0, now=0.0)

    mock_write.assert_awaited()
    written = mock_write.call_args[0][1]
    assert written == 6.0  # snapped to minimum active setpoint
    # remaining flips positive: EV absorbed 1380W but only 300W was requested
    assert remaining > 0


async def test_numeric_load_snap_down_over_releases(hass):
    """Snap down to 0 can over-release deficit (remaining goes negative)."""
    entry = _make_entry(kp=1.0)
    entry.add_to_hass(hass)

    coordinator = ZeroGridCoordinator(hass, entry)
    # EV at 4A (920W), import 300W → would go to 3A, but 3A < 6A min → snap to 0
    coordinator.loads = [
        _numeric_load(
            w_per_unit=230.0, absolute_min_w=1380.0, setpoint_min=0.0, setpoint_max=16.0
        )
    ]
    coordinator._current_load_setpoints["EV"] = 4.0
    hass.states.async_set("number.ev", "4")

    with patch.object(
        coordinator._actuators, "write_numeric_entity", new=AsyncMock()
    ) as mock_write:
        remaining = await coordinator._distribute_to_numeric_loads(300.0, now=0.0)

    mock_write.assert_awaited()
    written = mock_write.call_args[0][1]
    assert written == 0.0  # snapped to off
    # remaining goes negative: released 920W but only 300W was needed
    assert remaining < 0


async def test_numeric_load_write_failure_setpoint_not_updated(hass):
    """Write exception: setpoint in memory stays at previous value."""
    entry = _make_entry(kp=1.0)
    entry.add_to_hass(hass)

    coordinator = ZeroGridCoordinator(hass, entry)
    coordinator.loads = [
        _numeric_load(setpoint_min=0.0, setpoint_max=16.0, w_per_unit=230.0)
    ]
    coordinator._current_load_setpoints["EV"] = 0.0
    hass.states.async_set("number.ev", "0")

    with patch.object(
        coordinator._actuators,
        "write_numeric_entity",
        new=AsyncMock(side_effect=Exception("device offline")),
    ):
        # should not raise; setpoint must remain 0
        remaining = await coordinator._distribute_to_numeric_loads(-1150.0, now=0.0)

    assert coordinator._current_load_setpoints["EV"] == 0.0
    # Remaining is unchanged: failed write means load absorbed nothing
    assert remaining == pytest.approx(-1150.0)


# ---------------------------------------------------------------------------
# _apply_load_switch_hysteresis edge cases
# ---------------------------------------------------------------------------


async def test_switch_load_already_on_no_redundant_write_on_surplus(hass):
    """Switch already on with surplus: no redundant write."""
    entry = _make_entry()
    entry.add_to_hass(hass)

    coordinator = ZeroGridCoordinator(hass, entry)
    coordinator.loads = [_switch_load(power_w=2000.0, switch_debounce_s=0)]
    coordinator._current_load_setpoints["Boiler"] = 1.0
    hass.states.async_set("switch.boiler", "on")

    with patch.object(
        coordinator._actuators, "write_switch_entity", new=AsyncMock()
    ) as mock_write:
        await coordinator._apply_load_switch_hysteresis(-3000.0, now=0.0)

    mock_write.assert_not_awaited()


async def test_switch_load_already_off_no_write_on_import(hass):
    """Switch already off while importing: no redundant write."""
    entry = _make_entry()
    entry.add_to_hass(hass)

    coordinator = ZeroGridCoordinator(hass, entry)
    coordinator.loads = [_switch_load(power_w=2000.0, switch_debounce_s=0)]
    coordinator._current_load_setpoints["Boiler"] = 0.0
    hass.states.async_set("switch.boiler", "off")

    with patch.object(
        coordinator._actuators, "write_switch_entity", new=AsyncMock()
    ) as mock_write:
        await coordinator._apply_load_switch_hysteresis(500.0, now=0.0)

    mock_write.assert_not_awaited()


async def test_switch_load_lower_priority_turns_on_when_higher_cant(hass):
    """Lower-priority load turns on when surplus is insufficient for higher-priority load."""
    entry = _make_entry()
    entry.add_to_hass(hass)

    coordinator = ZeroGridCoordinator(hass, entry)
    coordinator.loads = [
        _switch_load(
            "Boiler", "switch.boiler", power_w=2000.0, priority=1, switch_debounce_s=0
        ),
        _switch_load(
            "Pump", "switch.pump", power_w=1000.0, priority=2, switch_debounce_s=0
        ),
    ]
    hass.states.async_set("switch.boiler", "off")
    hass.states.async_set("switch.pump", "off")

    boiler_calls: list[bool] = []
    pump_calls: list[bool] = []

    async def mock_write(entity_id, state):
        if "boiler" in entity_id:
            boiler_calls.append(state)
        else:
            pump_calls.append(state)

    with patch.object(
        coordinator._actuators, "write_switch_entity", side_effect=mock_write
    ):
        # -1500 W: enough for pump (1000W) but not boiler (2000W)
        await coordinator._apply_load_switch_hysteresis(-1500.0, now=0.0)

    assert not boiler_calls, "Boiler needs 2000W, only 1500W available"
    assert pump_calls == [True], "Pump (1000W) should turn on with 1500W surplus"


async def test_switch_load_debounce_after_turn_off(hass):
    """Switch load respects debounce after turning off."""
    entry = _make_entry()
    entry.add_to_hass(hass)

    coordinator = ZeroGridCoordinator(hass, entry)
    coordinator.loads = [_switch_load(power_w=2000.0, switch_debounce_s=30)]
    coordinator._current_load_setpoints["Boiler"] = 1.0
    hass.states.async_set("switch.boiler", "on")

    with patch.object(
        coordinator._actuators, "write_switch_entity", new=AsyncMock()
    ) as mock_write:
        # Turn off at t=0
        await coordinator._apply_load_switch_hysteresis(500.0, now=0.0)
        assert mock_write.await_count == 1
        # Surplus returns within debounce window: should NOT turn on again
        await coordinator._apply_load_switch_hysteresis(-3000.0, now=10.0)
        assert mock_write.await_count == 1, (
            "Should be blocked by debounce after turn-off"
        )


async def test_switch_load_write_failure_setpoint_not_updated(hass):
    """Write exception on switch turn-on: setpoint stays at 0, no crash."""
    entry = _make_entry()
    entry.add_to_hass(hass)

    coordinator = ZeroGridCoordinator(hass, entry)
    coordinator.loads = [_switch_load(power_w=2000.0, switch_debounce_s=0)]
    hass.states.async_set("switch.boiler", "off")

    with patch.object(
        coordinator._actuators,
        "write_switch_entity",
        new=AsyncMock(side_effect=Exception("device offline")),
    ):
        residual = await coordinator._apply_load_switch_hysteresis(-3000.0, now=0.0)

    # Setpoint must remain 0 (off): write failed, feedforward must not be applied
    assert coordinator._current_load_setpoints.get("Boiler", 0.0) == 0.0
    # Residual unchanged: load was not turned on
    assert residual == pytest.approx(-3000.0)


# ---------------------------------------------------------------------------
# Full control cycle integration
# ---------------------------------------------------------------------------


async def test_full_cycle_switch_load_activates_on_export(hass):
    """Full cycle: switch load turns on when grid exports surplus >= power_w."""
    entry = _make_entry(kp=1.0, deadband_w=0.0)
    entry.add_to_hass(hass)

    _set_state(hass, "sensor.grid_import", 0)
    _set_state(hass, "sensor.grid_export", 2500)
    hass.states.async_set("switch.boiler", "off")

    coordinator = ZeroGridCoordinator(hass, entry)
    coordinator.loads = [_switch_load(power_w=2000.0)]

    with (
        patch.object(
            coordinator._actuators, "write_switch_entity", new=AsyncMock()
        ) as mock_sw,
        patch.object(coordinator._actuators, "write_numeric_entity", new=AsyncMock()),
    ):
        result = await coordinator._async_update_data()

    assert result.status == STATUS_ACTIVE
    mock_sw.assert_awaited_once_with("switch.boiler", True)


async def test_full_cycle_numeric_load_reduces_on_import(hass):
    """Full cycle: numeric load is reduced when grid imports."""
    entry = _make_entry(kp=1.0, deadband_w=0.0)
    entry.add_to_hass(hass)

    _set_state(hass, "sensor.grid_import", 1150)
    _set_state(hass, "sensor.grid_export", 0)
    hass.states.async_set("number.ev", "10")

    coordinator = ZeroGridCoordinator(hass, entry)
    coordinator.loads = [_numeric_load(setpoint_max=16.0, w_per_unit=230.0)]
    coordinator._current_load_setpoints["EV"] = 10.0

    with patch.object(
        coordinator._actuators, "write_numeric_entity", new=AsyncMock()
    ) as mock_num:
        result = await coordinator._async_update_data()

    assert result.status == STATUS_ACTIVE
    mock_num.assert_awaited()
    written = mock_num.call_args[0][1]
    assert written < 10.0
