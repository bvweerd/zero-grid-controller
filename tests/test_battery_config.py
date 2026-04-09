"""Isolated unit tests for BatteryConfig."""

from __future__ import annotations

import pytest

from custom_components.zero_grid_controller.battery import BatteryConfig
from custom_components.zero_grid_controller.const import (
    BATTERY_RECOVERY_BLEND,
    BATTERY_RESPONSE_EWM_ALPHA,
    BATTERY_UNRESPONSIVE_CYCLES,
)


@pytest.fixture(autouse=True)
def auto_enable_custom_integrations(enable_custom_integrations):
    """Enable custom integrations for all tests in this module."""
    return


def _make_battery(
    max_charge_w: float = 5000.0,
    max_discharge_w: float = 5000.0,
) -> BatteryConfig:
    return BatteryConfig(
        subentry_id="sub1",
        name="Home Battery",
        sensor_entity="sensor.battery_power",
        max_charge_w=max_charge_w,
        max_discharge_w=max_discharge_w,
        control_enabled=True,
        setpoint_entity="number.battery_setpoint",
    )


# ---------------------------------------------------------------------------
# update_response — normal EWM tracking
# ---------------------------------------------------------------------------


def test_update_response_ewm_update() -> None:
    """update_response blends observed ratio into measured_response_factor via EWM."""
    b = _make_battery()
    initial_factor = b.measured_response_factor  # 1.0

    # Commanded -1000 W, actual -900 W → ratio 0.9
    b.update_response(actual_w=-900.0, commanded_w=-1000.0)

    expected = BATTERY_RESPONSE_EWM_ALPHA * 0.9 + (1 - BATTERY_RESPONSE_EWM_ALPHA) * initial_factor
    assert b.measured_response_factor == pytest.approx(expected, abs=1e-6)


def test_update_response_small_command_skipped() -> None:
    """update_response ignores commands smaller than BATTERY_UNRESPONSIVE_THRESHOLD_W."""
    b = _make_battery()
    initial = b.measured_response_factor
    b.update_response(actual_w=-10.0, commanded_w=-40.0)  # below threshold
    assert b.measured_response_factor == initial


# ---------------------------------------------------------------------------
# update_response — unresponsive detection
# ---------------------------------------------------------------------------


def test_update_response_unresponsive_detection() -> None:
    """Battery is declared unresponsive after BATTERY_UNRESPONSIVE_CYCLES bad readings."""
    b = _make_battery()

    # observed_ratio < BATTERY_HARD_RESET_RATIO  → unresponsive counter increments
    tiny_actual = -1.0  # almost nothing delivered
    large_cmd = -1000.0

    assert not b.is_unresponsive()

    for _ in range(BATTERY_UNRESPONSIVE_CYCLES - 1):
        b.update_response(actual_w=tiny_actual, commanded_w=large_cmd)
        assert not b.is_unresponsive()

    # One more cycle tips it over
    b.update_response(actual_w=tiny_actual, commanded_w=large_cmd)
    assert b.is_unresponsive()


def test_update_response_hard_reset_on_unresponsive() -> None:
    """On unresponsive threshold, measured_response_factor snaps to observed ratio."""
    b = _make_battery()
    tiny_actual = -50.0
    large_cmd = -1000.0
    observed_ratio = abs(tiny_actual) / abs(large_cmd)  # 0.05

    for _ in range(BATTERY_UNRESPONSIVE_CYCLES):
        b.update_response(actual_w=tiny_actual, commanded_w=large_cmd)

    # Factor must have snapped to max(0.1, observed_ratio)
    assert b.measured_response_factor == pytest.approx(max(0.1, observed_ratio), abs=1e-6)


# ---------------------------------------------------------------------------
# update_response — recovery blending
# ---------------------------------------------------------------------------


def test_update_response_recovery_blend() -> None:
    """On first good reading after unresponsive, factor blends toward 1.0."""
    b = _make_battery()
    tiny_actual = -1.0
    large_cmd = -1000.0

    # Drive to unresponsive
    for _ in range(BATTERY_UNRESPONSIVE_CYCLES):
        b.update_response(actual_w=tiny_actual, commanded_w=large_cmd)
    assert b.is_unresponsive()
    factor_at_reset = b.measured_response_factor

    # One good reading (ratio ≥ BATTERY_HARD_RESET_RATIO) → recovery
    good_actual = -900.0
    b.update_response(actual_w=good_actual, commanded_w=large_cmd)

    # Recovery blends measured_response_factor toward 1.0
    blended = (
        BATTERY_RECOVERY_BLEND * factor_at_reset
        + (1.0 - BATTERY_RECOVERY_BLEND) * 1.0
    )
    # Then normal EWM applied on top
    observed_ratio = abs(good_actual) / abs(large_cmd)
    expected_final = (
        BATTERY_RESPONSE_EWM_ALPHA * observed_ratio
        + (1.0 - BATTERY_RESPONSE_EWM_ALPHA) * blended
    )
    assert b.measured_response_factor == pytest.approx(expected_final, abs=1e-4)
    assert not b.is_unresponsive()


# ---------------------------------------------------------------------------
# is_clipping
# ---------------------------------------------------------------------------


def test_is_clipping_true_at_max_charge() -> None:
    """is_clipping returns True when battery power approaches max_charge_w."""
    b = _make_battery(max_charge_w=5000.0)
    # Battery charging at -4800 W (96 % of 5000) — above 95 % threshold
    assert b.is_clipping(current_power_w=-4800.0) is True


def test_is_clipping_false_below_threshold() -> None:
    """is_clipping returns False when battery power is well below max_charge_w."""
    b = _make_battery(max_charge_w=5000.0)
    # Battery charging at -2000 W (40 % of 5000)
    assert b.is_clipping(current_power_w=-2000.0) is False


def test_is_clipping_discharging_is_false() -> None:
    """is_clipping returns False for positive (discharging) battery power."""
    b = _make_battery(max_charge_w=5000.0)
    assert b.is_clipping(current_power_w=3000.0) is False


# ---------------------------------------------------------------------------
# is_unresponsive / reset_unresponsive
# ---------------------------------------------------------------------------


def test_is_unresponsive_initially_false() -> None:
    """Fresh BatteryConfig is not unresponsive."""
    b = _make_battery()
    assert b.is_unresponsive() is False


def test_reset_unresponsive_clears_counter() -> None:
    """reset_unresponsive clears the counter so is_unresponsive returns False."""
    b = _make_battery()
    for _ in range(BATTERY_UNRESPONSIVE_CYCLES):
        b.update_response(actual_w=-1.0, commanded_w=-1000.0)
    assert b.is_unresponsive()

    b.reset_unresponsive()
    assert not b.is_unresponsive()


# ---------------------------------------------------------------------------
# measured_response_factor clamping
# ---------------------------------------------------------------------------


def test_update_response_factor_clamped_to_bounds() -> None:
    """measured_response_factor is always clamped to [0.1, 2.0]."""
    b = _make_battery()
    # commanded -100 W, actual -5000 W (ratio 50 → would exceed 2.0)
    b.update_response(actual_w=-5000.0, commanded_w=-100.0)
    assert b.measured_response_factor <= 2.0

    b2 = _make_battery()
    # Multiple unresponsive cycles: factor must not go below 0.1
    for _ in range(BATTERY_UNRESPONSIVE_CYCLES * 2):
        b2.update_response(actual_w=0.0, commanded_w=-1000.0)
    assert b2.measured_response_factor >= 0.1
