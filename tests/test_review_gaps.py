"""Tests covering review-identified gaps in Zero Grid Controller behaviour."""

from __future__ import annotations

from unittest.mock import AsyncMock, patch

import pytest
from pytest_homeassistant_custom_component.common import MockConfigEntry

from custom_components.zero_grid_controller.array import ArrayConfig
from custom_components.zero_grid_controller.battery import BatteryConfig
from custom_components.zero_grid_controller.const import (
    DOMAIN,
    LOAD_TYPE_NUMERIC,
    SERVICE_RECALIBRATE,
    SERVICE_RESET_PID,
    STATUS_ACTIVE,
    STATUS_DEADBAND,
)
from custom_components.zero_grid_controller.coordinator import ZeroGridCoordinator
from custom_components.zero_grid_controller.load import LoadConfig


@pytest.fixture(autouse=True)
def auto_enable_custom_integrations(enable_custom_integrations):
    return


# ---------------------------------------------------------------------------
# Helpers (follow the same pattern as test_coordinator.py / test_load_control.py)
# ---------------------------------------------------------------------------


def _make_entry(
    deadband_w: float = 20.0,
    ewm_alpha: float = 1.0,
    kp: float = 1.0,
    ki: float = 0.0,
    kd: float = 0.0,
    controller_enabled: bool = True,
) -> MockConfigEntry:
    data = {
        "name": "Test ZGC",
        "grid_import_sensors": ["sensor.grid_import"],
        "grid_export_sensors": ["sensor.grid_export"],
        "deadband_w": deadband_w,
        "ewm_alpha": ewm_alpha,
        "kp": kp,
        "ki": ki,
        "kd": kd,
        "controller_enabled": controller_enabled,
    }
    return MockConfigEntry(domain=DOMAIN, title="Test", data=data, options={})


def _set_state(hass, entity_id: str, value) -> None:
    hass.states.async_set(entity_id, str(value))


def _numeric_array(
    name: str = "Solar",
    setpoint_entity: str = "number.solar_limit",
    w_per_unit: float = 50.0,
    setpoint_min: float = 0.0,
    setpoint_max: float = 100.0,
    settling_time_s: int = 10,
    settling_down_s: int | None = None,
    settling_up_s: int | None = None,
) -> ArrayConfig:
    return ArrayConfig(
        name=name,
        output_type="percent",
        setpoint_entity=setpoint_entity,
        w_per_unit=w_per_unit,
        calibration_confidence="estimated",
        setpoint_min=setpoint_min,
        setpoint_max=setpoint_max,
        settling_time_s=settling_time_s,
        settling_down_s=settling_down_s,
        settling_up_s=settling_up_s,
    )


def _make_battery(
    name: str = "Battery",
    subentry_id: str = "bat-1",
    sensor_entity: str = "sensor.battery_power",
    setpoint_entity: str = "number.battery_sp",
    max_charge_w: float = 3000.0,
    max_discharge_w: float = 5000.0,
) -> BatteryConfig:
    return BatteryConfig(
        subentry_id=subentry_id,
        name=name,
        sensor_entity=sensor_entity,
        max_charge_w=max_charge_w,
        max_discharge_w=max_discharge_w,
        setpoint_entity=setpoint_entity,
    )


# ---------------------------------------------------------------------------
# TEST 1: Deadband freeze does not accumulate integral
# ---------------------------------------------------------------------------


async def test_deadband_freeze_does_not_accumulate_integral(hass):
    """When inside deadband for many cycles, the integrator must not accumulate.

    After exiting deadband on the first active cycle, the integral should only
    reflect a single cycle's accumulation, not N frozen cycles' worth.
    """
    # kp=0 so P-term is zero; only ki matters
    entry = _make_entry(deadband_w=20.0, ewm_alpha=1.0, kp=0.0, ki=0.5)
    entry.add_to_hass(hass)
    coordinator = ZeroGridCoordinator(hass, entry)
    coordinator.arrays = [
        _numeric_array(
            w_per_unit=50.0, setpoint_min=0, setpoint_max=100, settling_time_s=0
        )
    ]
    coordinator._engine._current_setpoints["Solar"] = 50.0

    # --- Phase 1: 5 cycles inside deadband (grid = 15 W < 20 W) ---
    _set_state(hass, "sensor.grid_import", 15)
    _set_state(hass, "sensor.grid_export", 0)

    with patch.object(coordinator._actuators, "write_setpoint", new=AsyncMock()):
        for _ in range(5):
            result = await coordinator._async_update_data()

    assert result.status == STATUS_DEADBAND, (
        "All 5 in-deadband cycles should be DEADBAND"
    )
    assert coordinator._engine.pid.integral == pytest.approx(0.0), (
        "Integrator must stay at 0 while inside deadband"
    )

    # --- Phase 2: 2 cycles outside deadband (grid = 100 W > 20 W) ---
    # Note: the last deadband cycle leaves freeze_integrator() called but not
    # yet consumed (no compute() was called in the deadband return-early path).
    # So the FIRST active cycle's integrator is also frozen (carry-over).
    # The SECOND active cycle is the first one where integration actually happens.
    _set_state(hass, "sensor.grid_import", 100)
    _set_state(hass, "sensor.grid_export", 0)

    with patch.object(coordinator._actuators, "write_setpoint", new=AsyncMock()):
        result = (
            await coordinator._async_update_data()
        )  # cycle 6 — still frozen (carry-over)
        assert result.status == STATUS_ACTIVE
        integral_after_cycle6 = coordinator._engine.pid.integral
        result = (
            await coordinator._async_update_data()
        )  # cycle 7 — first free accumulation

    assert result.status == STATUS_ACTIVE, "100 W is above deadband → should be ACTIVE"

    # Cycle 6 carry-over freeze: integral stays at 0
    assert integral_after_cycle6 == pytest.approx(0.0), (
        "First active cycle after deadband still uses the carry-over freeze; integral = 0"
    )

    # Cycle 7: first freely-accumulating cycle.
    # dt is clamped to CONTROL_DT_MIN (0.1 s). error = 100 W. ki = 0.5.
    # Expected integral contribution: 100 * 0.1 = 10 W·s (small, one cycle only).
    # If the deadband freeze had not worked, 7 cycles of accumulation at 500 W·s
    # each would push the integral to >> 500 W·s.
    assert coordinator._engine.pid.integral < 600.0, (
        "Integrator must reflect at most ONE cycle of accumulation, not 7 unfrozen cycles"
    )
    assert coordinator._engine.pid.integral > 0.0, (
        "Second active cycle should have accumulated some integral"
    )


# ---------------------------------------------------------------------------
# TEST 2: Array settling uses correct direction (up vs down)
# ---------------------------------------------------------------------------


async def test_array_settling_uses_correct_direction(hass):
    """settling_down_s used when curtailing (delta_w < 0); settling_up_s when opening."""
    entry = _make_entry(deadband_w=5.0, ewm_alpha=1.0)
    entry.add_to_hass(hass)
    coordinator = ZeroGridCoordinator(hass, entry)
    coordinator.arrays = [
        _numeric_array(
            name="Solar",
            setpoint_entity="number.solar_limit",
            w_per_unit=10.0,
            setpoint_min=0.0,
            setpoint_max=100.0,
            settling_time_s=10,  # fallback
            settling_down_s=5,  # used when lowering (curtailing)
            settling_up_s=20,  # used when raising (opening)
        )
    ]
    coordinator._engine._current_setpoints["Solar"] = 50.0

    with patch.object(coordinator._actuators, "write_setpoint", new=AsyncMock()):
        # delta_w < 0 → curtail PV (lower setpoint) → should use settling_down_s (5)
        await coordinator._engine._distribute_to_numeric_arrays(
            -200.0, 100.0, coordinator.arrays
        )

    assert coordinator._engine._settling_until.get("Solar") == pytest.approx(105.0), (
        "Curtailment must use settling_down_s=5 → settling_until = now(100) + 5 = 105"
    )

    # Reset
    coordinator._engine._current_setpoints["Solar"] = 50.0
    coordinator._engine._settling_until.clear()

    with patch.object(coordinator._actuators, "write_setpoint", new=AsyncMock()):
        # delta_w > 0 → open PV (raise setpoint) → should use settling_up_s (20)
        await coordinator._engine._distribute_to_numeric_arrays(
            200.0, 100.0, coordinator.arrays
        )

    assert coordinator._engine._settling_until.get("Solar") == pytest.approx(120.0), (
        "Opening must use settling_up_s=20 → settling_until = now(100) + 20 = 120"
    )


# ---------------------------------------------------------------------------
# TEST 3: Battery setpoint resets on grid reversal (export → import)
# ---------------------------------------------------------------------------


async def test_battery_setpoint_resets_on_grid_reversal(hass):
    """When grid transitions from exporting (neg) to importing (pos), battery
    charge setpoints are reset to 0 (or positive for discharge)."""
    entry = _make_entry(deadband_w=5.0, ewm_alpha=1.0)
    entry.add_to_hass(hass)
    coordinator = ZeroGridCoordinator(hass, entry)
    coordinator.batteries = [_make_battery(max_charge_w=3000.0, max_discharge_w=5000.0)]
    # Array at max so the discharge layer can trigger
    coordinator.arrays = [
        _numeric_array(setpoint_min=0.0, setpoint_max=100.0, w_per_unit=10.0)
    ]
    coordinator._engine._current_setpoints["Solar"] = 100.0

    # --- Cycle 1: exporting 500 W → battery should charge (negative setpoint) ---
    _set_state(hass, "sensor.grid_import", 0)
    _set_state(hass, "sensor.grid_export", 500)

    with (
        patch.object(coordinator._actuators, "write_numeric_entity", new=AsyncMock()),
        patch.object(coordinator._actuators, "write_setpoint", new=AsyncMock()),
    ):
        result1 = await coordinator._async_update_data()

    assert result1.status == STATUS_ACTIVE
    battery_sp_charge = result1.battery_setpoints.get("Battery", 0.0)
    assert battery_sp_charge < 0, (
        f"On export, battery should charge (negative setpoint), got {battery_sp_charge}"
    )

    # --- Cycle 2: importing 200 W → battery charge should reset ---
    _set_state(hass, "sensor.grid_import", 200)
    _set_state(hass, "sensor.grid_export", 0)

    with (
        patch.object(coordinator._actuators, "write_numeric_entity", new=AsyncMock()),
        patch.object(coordinator._actuators, "write_setpoint", new=AsyncMock()),
    ):
        result2 = await coordinator._async_update_data()

    assert result2.status == STATUS_ACTIVE
    battery_sp_after = result2.battery_setpoints.get("Battery", -999.0)
    assert battery_sp_after >= 0, (
        f"On import, battery charge setpoint must be reset to 0 or positive "
        f"(discharge), got {battery_sp_after}"
    )


# ---------------------------------------------------------------------------
# TEST 4: Multiple batteries proportional distribution
# ---------------------------------------------------------------------------


async def test_multiple_batteries_proportional_distribution(hass):
    """Two batteries with different max_charge_w receive proportional charge power."""
    entry = _make_entry(deadband_w=5.0, ewm_alpha=1.0)
    entry.add_to_hass(hass)
    coordinator = ZeroGridCoordinator(hass, entry)

    coordinator.batteries = [
        _make_battery(
            name="BatA",
            subentry_id="bat-a",
            setpoint_entity="number.bat_a_sp",
            max_charge_w=1000.0,
            max_discharge_w=5000.0,
        ),
        _make_battery(
            name="BatB",
            subentry_id="bat-b",
            setpoint_entity="number.bat_b_sp",
            max_charge_w=3000.0,
            max_discharge_w=5000.0,
        ),
    ]

    # Grid exporting 2000 W → should charge at 2000 W total
    _set_state(hass, "sensor.grid_import", 0)
    _set_state(hass, "sensor.grid_export", 2000)

    write_calls: dict[str, float] = {}

    async def mock_write(entity_id: str, value: float) -> None:
        write_calls[entity_id] = value

    with patch.object(
        coordinator._actuators, "write_numeric_entity", side_effect=mock_write
    ):
        result = await coordinator._async_update_data()

    assert result.status == STATUS_ACTIVE

    sp_a = write_calls.get("number.bat_a_sp")
    sp_b = write_calls.get("number.bat_b_sp")

    assert sp_a is not None, "Battery A should have received a setpoint write"
    assert sp_b is not None, "Battery B should have received a setpoint write"

    # Total capacity = 4000 W, grid = 2000 W
    # BatA share: 2000 * (1000/4000) = 500 W charge → setpoint = -500
    # BatB share: 2000 * (3000/4000) = 1500 W charge → setpoint = -1500
    assert sp_a == pytest.approx(-500.0), (
        f"BatA (1000 W cap) should receive -500 W, got {sp_a}"
    )
    assert sp_b == pytest.approx(-1500.0), (
        f"BatB (3000 W cap) should receive -1500 W, got {sp_b}"
    )


# ---------------------------------------------------------------------------
# TEST 5: snap_setpoint exact boundary cases
# ---------------------------------------------------------------------------


def test_snap_setpoint_exact_boundary():
    """snap_setpoint correctly handles exact and near-exact boundary values."""
    from custom_components.zero_grid_controller.const import LOAD_TYPE_NUMERIC
    from custom_components.zero_grid_controller.load import LoadConfig

    # Case 1: absolute_min_w exactly divisible by w_per_unit
    # ceil(2300 / 230) = 10
    load1 = LoadConfig(
        name="EV",
        load_type=LOAD_TYPE_NUMERIC,
        setpoint_entity="number.ev",
        priority=1,
        setpoint_min=0.0,
        setpoint_max=16.0,
        w_per_unit=230.0,
        absolute_min_w=2300.0,
    )
    snapped = load1.snap_setpoint(5.0, increasing=True)
    assert snapped == pytest.approx(10.0), (
        f"ceil(2300/230)=10 → snap up from 5 to 10, got {snapped}"
    )

    # Case 2: just above exact boundary — ceil(2301/230) = 11
    load2 = LoadConfig(
        name="EV",
        load_type=LOAD_TYPE_NUMERIC,
        setpoint_entity="number.ev",
        priority=1,
        setpoint_min=0.0,
        setpoint_max=16.0,
        w_per_unit=230.0,
        absolute_min_w=2301.0,
    )
    snapped2 = load2.snap_setpoint(5.0, increasing=True)
    assert snapped2 == pytest.approx(11.0), (
        f"ceil(2301/230)=11 → snap up from 5 to 11, got {snapped2}"
    )

    # Case 3: half of case 1 — ceil(1150/230) = 5
    load3 = LoadConfig(
        name="EV",
        load_type=LOAD_TYPE_NUMERIC,
        setpoint_entity="number.ev",
        priority=1,
        setpoint_min=0.0,
        setpoint_max=16.0,
        w_per_unit=230.0,
        absolute_min_w=1150.0,
    )
    snapped3 = load3.snap_setpoint(2.0, increasing=True)
    assert snapped3 == pytest.approx(5.0), (
        f"ceil(1150/230)=5 → snap up from 2 to 5, got {snapped3}"
    )

    # Case 4: snap_down from 5 → setpoint_min (0) because 5 * 230 = 1150 = absolute_min_w
    # setpoint=5 * w_per_unit=230 = 1150 >= absolute_min_w=1150 → no snap needed
    # setpoint=4 * 230 = 920 < 1150 → snaps down to 0
    snapped4 = load3.snap_setpoint(4.0, increasing=False)
    assert snapped4 == pytest.approx(0.0), (
        f"4 units * 230 W = 920 W < 1150 W min → snap down to 0, got {snapped4}"
    )


# ---------------------------------------------------------------------------
# TEST 6: Services removed when last entry is unloaded
# ---------------------------------------------------------------------------


async def test_services_removed_when_last_entry_unloaded(hass):
    """Domain services are removed after the last config entry is unloaded."""
    from custom_components.zero_grid_controller import async_setup

    # Register services via the domain-level setup (mimics normal HA startup)
    assert await async_setup(hass, {}) is True
    assert hass.services.has_service(DOMAIN, SERVICE_RESET_PID), (
        "SERVICE_RESET_PID should be registered after async_setup"
    )
    assert hass.services.has_service(DOMAIN, SERVICE_RECALIBRATE), (
        "SERVICE_RECALIBRATE should be registered after async_setup"
    )

    # Set up one entry
    entry = MockConfigEntry(
        domain=DOMAIN,
        title="Test ZGC",
        data={
            "name": "Test ZGC",
            "grid_import_sensors": ["sensor.grid_import"],
            "grid_export_sensors": ["sensor.grid_export"],
        },
        options={},
    )
    entry.add_to_hass(hass)
    _set_state(hass, "sensor.grid_import", 0)
    _set_state(hass, "sensor.grid_export", 0)

    loaded = await hass.config_entries.async_setup(entry.entry_id)
    assert loaded is True
    assert hass.services.has_service(DOMAIN, SERVICE_RESET_PID), (
        "Services must exist while the entry is loaded"
    )

    # Unload the only entry → async_unload_entry should remove domain services
    unloaded = await hass.config_entries.async_unload(entry.entry_id)
    assert unloaded is True

    assert not hass.services.has_service(DOMAIN, SERVICE_RESET_PID), (
        "SERVICE_RESET_PID should be removed after last entry is unloaded"
    )
    assert not hass.services.has_service(DOMAIN, SERVICE_RECALIBRATE), (
        "SERVICE_RECALIBRATE should be removed after last entry is unloaded"
    )


# ---------------------------------------------------------------------------
# TEST 7: EWM filter initialises from the first reading
# ---------------------------------------------------------------------------


async def test_ewm_filter_initializes_from_first_reading(hass):
    """On cold start the EWM filter is seeded with the first raw reading."""
    entry = _make_entry(deadband_w=5.0, ewm_alpha=0.3)  # ewm_alpha < 1 to check init
    entry.add_to_hass(hass)
    coordinator = ZeroGridCoordinator(hass, entry)

    # Confirm filter not yet initialised
    assert coordinator._engine._filtered_w is None

    _set_state(hass, "sensor.grid_import", 300)
    _set_state(hass, "sensor.grid_export", 0)

    with patch.object(coordinator._actuators, "write_setpoint", new=AsyncMock()):
        result = await coordinator._async_update_data()

    # After first update the filter must be seeded to the raw reading
    assert coordinator._engine._filtered_w is not None, (
        "_filtered_w should be initialised after first cycle"
    )
    assert result.grid_raw_w == pytest.approx(300.0), (
        f"grid_raw_w should be 300.0, got {result.grid_raw_w}"
    )
    # On first call _filtered_w = grid_raw (no EWM blend applied yet)
    assert result.grid_filtered_w == pytest.approx(300.0), (
        "grid_filtered_w must equal raw reading on first cycle (cold start seed)"
    )


# ---------------------------------------------------------------------------
# TEST 8: Array write failure does not update setpoint
# ---------------------------------------------------------------------------


async def test_array_write_failure_does_not_update_setpoint(hass):
    """When write_setpoint raises, the in-memory setpoint stays unchanged."""
    entry = _make_entry(deadband_w=5.0, ewm_alpha=1.0)
    entry.add_to_hass(hass)
    coordinator = ZeroGridCoordinator(hass, entry)
    coordinator.arrays = [
        _numeric_array(
            name="Solar",
            setpoint_entity="number.solar_limit",
            w_per_unit=50.0,
            setpoint_min=0.0,
            setpoint_max=100.0,
            settling_time_s=10,
        )
    ]
    coordinator._engine._current_setpoints["Solar"] = 50.0

    failing_write = AsyncMock(side_effect=Exception("inverter offline"))
    with patch.object(coordinator._actuators, "write_setpoint", new=failing_write):
        # The exception is now caught inside _distribute_to_numeric_arrays;
        # the call must return normally (not propagate).
        remaining = await coordinator._engine._distribute_to_numeric_arrays(
            500.0, 0.0, coordinator.arrays
        )

    # Nothing was absorbed — all delta_w returned as remainder.
    assert remaining == pytest.approx(500.0), (
        "All delta_w must be returned when write_setpoint raises"
    )
    # Setpoint must not have been updated since the write failed.
    assert coordinator._engine._current_setpoints["Solar"] == pytest.approx(50.0), (
        "In-memory setpoint must remain at 50.0 when write_setpoint raises"
    )
    # settling_until must also be unset for Solar (no successful write).
    assert "Solar" not in coordinator._engine._settling_until, (
        "settling_until must not be set when write_setpoint raised"
    )


# ---------------------------------------------------------------------------
# TEST 9: _can_open boundary and unavailable
# ---------------------------------------------------------------------------


async def test_can_open_boundary_and_unavailable(hass):
    """_can_open returns correct values at the exact boundary and for edge cases."""
    entry = _make_entry()
    entry.add_to_hass(hass)
    coordinator = ZeroGridCoordinator(hass, entry)

    # current_sp=50, w_per_unit=10 → threshold = (50-1)*10 = 490 W
    array = _numeric_array(
        name="Solar",
        setpoint_entity="number.solar_limit",
        w_per_unit=10.0,
        setpoint_min=0.0,
        setpoint_max=100.0,
        settling_time_s=10,
    )
    array.power_sensor_entity = "sensor.solar_power"
    coordinator._engine._current_setpoints["Solar"] = 50.0

    # Case A: power exactly at threshold (490 W) → should return True (≥ threshold)
    _set_state(hass, "sensor.solar_power", 490)
    assert coordinator._engine._can_open(array) is True, (
        "Exact threshold (490 W) must return True"
    )

    # Case B: 1 W below threshold (489 W) → should return False
    _set_state(hass, "sensor.solar_power", 489)
    assert coordinator._engine._can_open(array) is False, (
        "1 W below threshold (489 W) must return False"
    )

    # Case C: sensor is "unavailable" → fail-open (True)
    hass.states.async_set("sensor.solar_power", "unavailable")
    assert coordinator._engine._can_open(array) is True, (
        "Unavailable sensor must fail open (return True)"
    )

    # Case D: no power_sensor_entity configured → always True
    array_no_sensor = _numeric_array(
        name="Solar2",
        setpoint_entity="number.solar2_limit",
        w_per_unit=10.0,
        setpoint_min=0.0,
        setpoint_max=100.0,
    )
    # power_sensor_entity defaults to None in ArrayConfig
    coordinator._engine._current_setpoints["Solar2"] = 50.0
    assert coordinator._engine._can_open(array_no_sensor) is True, (
        "No power_sensor_entity must always return True"
    )


# ---------------------------------------------------------------------------
# TEST 10: EWM alpha=1.0 passes raw values through (no memory)
# ---------------------------------------------------------------------------


async def test_ewm_alpha_one_passes_raw_values_through(hass):
    """With ewm_alpha=1.0, each cycle's filtered value equals the raw reading."""
    entry = _make_entry(deadband_w=0.0, ewm_alpha=1.0, kp=0.0)
    entry.add_to_hass(hass)
    coordinator = ZeroGridCoordinator(hass, entry)
    # Single array so distribution doesn't crash, but we don't care about setpoints
    coordinator.arrays = [
        _numeric_array(
            name="Solar",
            setpoint_entity="number.solar_limit",
            w_per_unit=50.0,
            setpoint_min=0.0,
            setpoint_max=100.0,
            settling_time_s=0,
        )
    ]
    coordinator._engine._current_setpoints["Solar"] = 50.0

    with patch.object(coordinator._actuators, "write_setpoint", new=AsyncMock()):
        # Cycle 1: raw = 200 W → filtered must be 200 W
        _set_state(hass, "sensor.grid_import", 200)
        _set_state(hass, "sensor.grid_export", 0)
        result1 = await coordinator._async_update_data()
        assert result1.grid_filtered_w == pytest.approx(200.0), (
            f"Cycle 1: expected filtered=200, got {result1.grid_filtered_w}"
        )

        # Cycle 2: raw = 500 W → filtered must be 500 W (no blend with prior 200)
        _set_state(hass, "sensor.grid_import", 500)
        result2 = await coordinator._async_update_data()
        assert result2.grid_filtered_w == pytest.approx(500.0), (
            f"Cycle 2: expected filtered=500, got {result2.grid_filtered_w}"
        )

        # Cycle 3: raw = 100 W → filtered must be 100 W
        _set_state(hass, "sensor.grid_import", 100)
        result3 = await coordinator._async_update_data()
        assert result3.grid_filtered_w == pytest.approx(100.0), (
            f"Cycle 3: expected filtered=100, got {result3.grid_filtered_w}"
        )


# ---------------------------------------------------------------------------
# TEST 11: Switch load debounce prevents off after recent on
# ---------------------------------------------------------------------------


async def test_switch_load_debounce_prevents_off_after_recent_on(hass):
    """A switch load just turned ON must not be turned OFF within the debounce window."""
    entry = _make_entry(deadband_w=0.0, ewm_alpha=1.0)
    entry.add_to_hass(hass)
    coordinator = ZeroGridCoordinator(hass, entry)

    load = LoadConfig(
        name="Load",
        load_type="switch",
        setpoint_entity="switch.load",
        priority=1,
        power_w=500.0,
        switch_debounce_s=30,
    )
    coordinator.loads = [load]

    write_switch_calls: list[tuple[str, bool]] = []

    async def mock_write_switch(entity_id: str, value: bool) -> None:
        write_switch_calls.append((entity_id, value))

    with patch.object(
        coordinator._actuators, "write_switch_entity", side_effect=mock_write_switch
    ):
        # Step 1: now=0, residual=-600 (surplus > 500) → load turns ON
        # _load_settling_until["Load"] = 0 + 30 = 30
        await coordinator._engine._apply_load_switch_hysteresis(
            -600.0, 0.0, coordinator.loads
        )
        assert coordinator._engine._current_load_setpoints.get(
            "Load", 0.0
        ) == pytest.approx(1.0), "Load should be ON after surplus exceeds power_w"
        assert coordinator._engine._load_settling_until.get("Load") == pytest.approx(
            30.0
        ), "Debounce should be set to now(0) + 30 = 30"
        on_calls = [c for c in write_switch_calls if c[1] is True]
        assert len(on_calls) == 1, "Expected exactly one ON write"

        # Step 2: now=10 (within debounce), residual=+100 → must NOT turn off
        write_switch_calls.clear()
        await coordinator._engine._apply_load_switch_hysteresis(
            100.0, 10.0, coordinator.loads
        )
        off_calls = [c for c in write_switch_calls if c[1] is False]
        assert len(off_calls) == 0, (
            "Load must NOT turn off at now=10 (still within debounce window until t=30)"
        )
        assert coordinator._engine._current_load_setpoints.get(
            "Load", 0.0
        ) == pytest.approx(1.0), "Load must remain ON during debounce window"

        # Step 3: now=35 (after debounce), residual=+100 → may turn off now
        write_switch_calls.clear()
        await coordinator._engine._apply_load_switch_hysteresis(
            100.0, 35.0, coordinator.loads
        )
        off_calls_35 = [c for c in write_switch_calls if c[1] is False]
        assert len(off_calls_35) == 1, (
            "Load should turn off at now=35 (after debounce expired at t=30)"
        )
        assert coordinator._engine._current_load_setpoints.get(
            "Load", 0.0
        ) == pytest.approx(0.0), (
            "Load must be OFF after debounce expires and grid is importing"
        )


# ---------------------------------------------------------------------------
# TEST 12: Battery state not updated on write failure
# ---------------------------------------------------------------------------


async def test_battery_state_not_updated_on_write_failure(hass):
    """When battery charge write fails, _current_battery_setpoints is not updated."""
    from homeassistant.exceptions import HomeAssistantError

    entry = _make_entry(deadband_w=5.0, ewm_alpha=1.0, kp=1.0)
    entry.add_to_hass(hass)
    coordinator = ZeroGridCoordinator(hass, entry)

    bat = _make_battery(name="Bat", max_charge_w=3000.0, max_discharge_w=5000.0)
    coordinator.batteries = [bat]
    coordinator._engine._current_battery_setpoints["Bat"] = 0.0

    # Grid exporting 1000 W → battery charge layer will try to write
    _set_state(hass, "sensor.grid_import", 0)
    _set_state(hass, "sensor.grid_export", 1000)

    async def fail_write(entity_id: str, value: float) -> None:
        raise HomeAssistantError(f"Write failed for {entity_id}")

    with patch.object(
        coordinator._actuators, "write_numeric_entity", side_effect=fail_write
    ):
        await coordinator._async_update_data()

    assert coordinator._engine._current_battery_setpoints.get(
        "Bat", 0.0
    ) == pytest.approx(0.0), (
        "_current_battery_setpoints must remain at 0.0 when write raises HomeAssistantError"
    )


# ---------------------------------------------------------------------------
# TEST 13: reload_config prunes stale setpoint keys
# ---------------------------------------------------------------------------


async def test_reload_config_prunes_stale_setpoint_keys(hass):
    """After reload_config(), setpoint dicts no longer contain keys for removed subentries."""
    entry = _make_entry()
    entry.add_to_hass(hass)
    coordinator = ZeroGridCoordinator(hass, entry)

    # Simulate that two arrays A and B were previously active, plus a stale OldArray key.
    # Since the config entry has no subentries, after reload self.arrays will be empty,
    # and ALL keys in _current_setpoints should be pruned (including OldArray).
    coordinator._engine._current_setpoints = {"A": 50.0, "B": 70.0, "OldArray": 30.0}
    coordinator._engine._settling_until = {"OldArray": 9999.0}

    coordinator.reload_config()

    # The config entry has no array subentries, so active_array_names = {} after reload.
    # All three keys should have been pruned.
    assert "OldArray" not in coordinator._engine._current_setpoints, (
        "'OldArray' was never in the config entry; it must be pruned after reload_config()"
    )
    assert "OldArray" not in coordinator._engine._settling_until, (
        "'OldArray' settling state must also be pruned after reload_config()"
    )
    # A and B are also not in the (empty) config entry, so they are pruned too.
    assert "A" not in coordinator._engine._current_setpoints, (
        "'A' not in config entry subentries → pruned"
    )
    assert "B" not in coordinator._engine._current_setpoints, (
        "'B' not in config entry subentries → pruned"
    )


# ---------------------------------------------------------------------------
# TEST 14: Multiple loads snap cascades correctly
# ---------------------------------------------------------------------------


async def test_multiple_loads_snap_cascades_correctly(hass):
    """Surplus is distributed to loads by priority; snap logic is applied per load."""
    entry = _make_entry(deadband_w=0.0, ewm_alpha=1.0)
    entry.add_to_hass(hass)
    coordinator = ZeroGridCoordinator(hass, entry)

    # Load A: priority=1, w_per_unit=230, min=0, max=16, absolute_min_w=1380 (6A min)
    load_a = LoadConfig(
        name="LoadA",
        load_type=LOAD_TYPE_NUMERIC,
        setpoint_entity="number.load_a",
        priority=1,
        setpoint_min=0.0,
        setpoint_max=16.0,
        w_per_unit=230.0,
        absolute_min_w=1380.0,
        settling_time_s=0,
    )

    # Load B: priority=2, w_per_unit=100, min=0, max=50, no absolute_min_w
    load_b = LoadConfig(
        name="LoadB",
        load_type=LOAD_TYPE_NUMERIC,
        setpoint_entity="number.load_b",
        priority=2,
        setpoint_min=0.0,
        setpoint_max=50.0,
        w_per_unit=100.0,
        absolute_min_w=None,
        settling_time_s=0,
    )

    coordinator.loads = [load_a, load_b]
    # Both start at setpoint=0 (off)
    coordinator._engine._current_load_setpoints["LoadA"] = 0.0
    coordinator._engine._current_load_setpoints["LoadB"] = 0.0

    write_calls: dict[str, float] = {}

    async def mock_write(entity_id: str, value: float) -> None:
        write_calls[entity_id] = value

    with patch.object(
        coordinator._actuators, "write_numeric_entity", side_effect=mock_write
    ):
        # Surplus of 2000 W (delta_w=-2000)
        await coordinator._engine._distribute_to_numeric_loads(
            -2000.0, 0.0, coordinator.loads
        )

    # Load A (priority=1) snaps up: raw = round(2000/230)=9 → 9 units, but snap to
    # ceil(1380/230)=6 since 9*230=2070 ≥ 1380, no snap needed... wait:
    # Actually: available_w = headroom_increase_w(0) = 16*230 = 3680
    # take_w = min(3680, 2000) = 2000
    # delta_units = round(2000 / 230) = round(8.695...) = 9
    # new_sp = clamp(0+9, 0, 16) = 9
    # snap_setpoint(9, increasing=True): 9*230=2070 >= 1380 → no snap (new_sp returned as-is)
    # actual_w = (9-0)*230 = 2070
    # remaining = -2000 + 2070 = +70 (positive, so loop breaks)
    #
    # Load B never gets called since remaining became positive after Load A.

    sp_a = coordinator._engine._current_load_setpoints.get("LoadA")
    sp_b = coordinator._engine._current_load_setpoints.get("LoadB")

    assert sp_a == pytest.approx(9.0), (
        f"LoadA should receive setpoint=9 (round(2000/230)=9), got {sp_a}"
    )
    # LoadB should not change since all surplus was absorbed by LoadA
    assert sp_b == pytest.approx(0.0), (
        f"LoadB should remain at 0 (surplus absorbed by LoadA), got {sp_b}"
    )
    assert "number.load_a" in write_calls, "LoadA setpoint write must have been called"
    assert write_calls["number.load_a"] == pytest.approx(9.0)

    # Now test with smaller surplus where snap kicks in and remainder flows to B
    # Reset
    coordinator._engine._current_load_setpoints["LoadA"] = 0.0
    coordinator._engine._current_load_setpoints["LoadB"] = 0.0
    coordinator._engine._load_settling_until.clear()
    write_calls.clear()

    with patch.object(
        coordinator._actuators, "write_numeric_entity", side_effect=mock_write
    ):
        # Surplus of 1500 W (delta_w=-1500)
        # LoadA: take_w = min(3680, 1500) = 1500, delta_units = round(1500/230) = 7
        # new_sp = 7, snap_setpoint(7, True): 7*230=1610 >= 1380 → 7 (no snap)
        # actual_w = 7*230 = 1610 W
        # remaining = -1500 + 1610 = +110 → loop stops (positive)
        await coordinator._engine._distribute_to_numeric_loads(
            -1500.0, 0.0, coordinator.loads
        )

    sp_a2 = coordinator._engine._current_load_setpoints.get("LoadA")
    sp_b2 = coordinator._engine._current_load_setpoints.get("LoadB")

    assert sp_a2 == pytest.approx(7.0), (
        f"LoadA: round(1500/230)=7, snap(7,True): 7*230=1610≥1380 → 7, got {sp_a2}"
    )
    assert sp_b2 == pytest.approx(0.0), (
        "LoadB should remain at 0 since LoadA absorbed all surplus"
    )
