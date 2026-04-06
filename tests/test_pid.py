"""Unit tests for the PID controller."""

import pytest

from custom_components.zero_grid_controller.pid import PIDController


class TestPIDBasic:
    def test_proportional_only(self):
        """P-only controller output equals Kp * error."""
        pid = PIDController(kp=2.0, ki=0.0, kd=0.0, setpoint=0.0)
        output = pid.compute(measurement=10.0, dt=1.0)
        # error = 0 - 10 = -10; P = 2 * -10 = -20
        assert output == pytest.approx(-20.0, abs=0.01)

    def test_setpoint_zero_gives_zero_error(self):
        """When measurement equals setpoint, output should be zero (PI only)."""
        pid = PIDController(kp=1.0, ki=0.1, kd=0.0, setpoint=0.0)
        output = pid.compute(measurement=0.0, dt=1.0)
        assert output == pytest.approx(0.0, abs=0.001)


class TestAntiWindup:
    def test_integrator_does_not_windup_when_clamped(self):
        """Integrator must not accumulate when output is at the clamp limit."""
        pid = PIDController(
            kp=1.0,
            ki=10.0,
            kd=0.0,
            setpoint=0.0,
            output_min=-5.0,
            output_max=5.0,
        )
        # Sustained large error that saturates the output
        for _ in range(50):
            out = pid.compute(measurement=100.0, dt=1.0)
            # Output should stay clamped at -5
            assert out == pytest.approx(-5.0, abs=0.01)

        # Now remove the error — integrator should not have wound up far
        # If anti-windup works, integral should be small (bounded by saturation logic)
        # After 50 cycles with ki=10 and no windup, integral should be bounded
        assert abs(pid.integral) < 200  # Without anti-windup it would be ~5000

    def test_integrator_accumulates_when_not_clamped(self):
        """When output is not clamped, the integrator should accumulate normally."""
        pid = PIDController(
            kp=0.0,
            ki=1.0,
            kd=0.0,
            setpoint=0.0,
            output_min=-10000.0,
            output_max=10000.0,
        )
        # error = 0 - 1 = -1; with ki=1 and dt=1, I grows by -1 each step
        pid.compute(measurement=1.0, dt=1.0)
        pid.compute(measurement=1.0, dt=1.0)
        assert pid.integral == pytest.approx(-2.0, abs=0.001)

    def test_anti_windup_allows_integration_when_pulling_back(self):
        """When clamped at min and error is positive (pulling back), integration IS allowed."""
        pid = PIDController(
            kp=0.0,
            ki=1.0,
            kd=0.0,
            setpoint=0.0,
            output_min=-5.0,
            output_max=5.0,
        )
        # First: push integral negative so output clamps at -5
        for _ in range(10):
            pid.compute(measurement=1.0, dt=1.0)  # error = -1, output goes negative

        # Now: positive error (measurement negative) while still clamped negative.
        # error > 0, output_pi < 0 → NOT pushing into limit → integration allowed.
        before = pid.integral
        pid.compute(measurement=-10.0, dt=1.0)  # error = +10
        assert pid.integral > before  # integral should have increased (pulled back)


class TestFreezeIntegrator:
    def test_freeze_stops_integration(self):
        """freeze_integrator() prevents the I-term from changing on next compute."""
        pid = PIDController(kp=0.0, ki=1.0, kd=0.0, setpoint=0.0)
        pid.freeze_integrator()
        before = pid.integral
        pid.compute(measurement=5.0, dt=1.0)
        assert pid.integral == pytest.approx(before, abs=1e-9)

    def test_freeze_is_per_cycle(self):
        """After one frozen cycle, integration resumes normally."""
        pid = PIDController(kp=0.0, ki=1.0, kd=0.0, setpoint=0.0)
        pid.freeze_integrator()
        pid.compute(measurement=5.0, dt=1.0)
        before = pid.integral
        # Second compute without freeze — integral should change
        pid.compute(measurement=5.0, dt=1.0)
        assert pid.integral != pytest.approx(before, abs=1e-9)

    def test_freeze_does_not_affect_p_or_d(self):
        """Freeze should not affect P-term or D-term."""
        pid = PIDController(kp=2.0, ki=0.0, kd=0.0, setpoint=0.0)
        pid.freeze_integrator()
        out = pid.compute(measurement=3.0, dt=1.0)
        # P = 2 * (0 - 3) = -6; I = 0 (frozen); D = 0
        assert out == pytest.approx(-6.0, abs=0.01)


class TestReset:
    def test_reset_clears_integral(self):
        """reset() must clear the integrator."""
        pid = PIDController(kp=0.0, ki=1.0, kd=0.0, setpoint=0.0)
        pid.compute(measurement=5.0, dt=1.0)
        pid.compute(measurement=5.0, dt=1.0)
        assert abs(pid.integral) > 0
        pid.reset()
        assert pid.integral == pytest.approx(0.0, abs=1e-9)

    def test_reset_clears_derivative_history(self):
        """After reset, first compute should have zero D-term."""
        pid = PIDController(kp=0.0, ki=0.0, kd=5.0, setpoint=0.0)
        pid.compute(measurement=1.0, dt=1.0)
        pid.reset()
        out = pid.compute(measurement=3.0, dt=1.0)
        # D-term requires a previous error; after reset, prev_error is None → d=0
        assert out == pytest.approx(0.0, abs=0.01)


class TestSetGains:
    def test_set_gains_live(self):
        """set_gains() changes gains without resetting state."""
        pid = PIDController(kp=1.0, ki=0.5, kd=0.0, setpoint=0.0)
        pid.compute(measurement=1.0, dt=1.0)
        integral_before = pid.integral

        pid.set_gains(2.0, 0.1, 0.05)
        assert pid.kp == pytest.approx(2.0)
        assert pid.ki == pytest.approx(0.1)
        assert pid.kd == pytest.approx(0.05)
        # Integral preserved after set_gains
        assert pid.integral == pytest.approx(integral_before, abs=1e-9)


class TestComponents:
    def test_components_dict(self):
        """components property should return p, i, d keys."""
        pid = PIDController(kp=1.0, ki=0.5, kd=0.1, setpoint=0.0)
        pid.compute(measurement=2.0, dt=1.0)
        comps = pid.components
        assert "p" in comps and "i" in comps and "d" in comps

    def test_components_sum_to_output(self):
        """P + I + D should approximately equal the unclamped output."""
        pid = PIDController(
            kp=1.0,
            ki=0.5,
            kd=0.1,
            setpoint=0.0,
            output_min=-1000.0,
            output_max=1000.0,
        )
        out = pid.compute(measurement=5.0, dt=1.0)
        comps = pid.components
        assert out == pytest.approx(comps["p"] + comps["i"] + comps["d"], abs=0.01)


class TestOutputClamping:
    def test_output_clamp_max(self):
        """Output must not exceed output_max."""
        pid = PIDController(kp=100.0, ki=0.0, kd=0.0, setpoint=0.0, output_max=50.0)
        out = pid.compute(measurement=-10.0, dt=1.0)
        assert out <= 50.0

    def test_output_clamp_min(self):
        """Output must not go below output_min."""
        pid = PIDController(kp=100.0, ki=0.0, kd=0.0, setpoint=0.0, output_min=-50.0)
        out = pid.compute(measurement=10.0, dt=1.0)
        assert out >= -50.0


class TestEdgeCases:
    def test_dt_zero_uses_fallback(self):
        """When dt <= 0, compute uses dt=0.1 to avoid division by zero."""
        pid = PIDController(kp=1.0, ki=0.0, kd=1.0, setpoint=0.0)
        # First call to set prev_error
        pid.compute(measurement=0.0, dt=1.0)
        # Second call with dt=0 — should use fallback 0.1
        out = pid.compute(measurement=1.0, dt=0.0)
        # With dt=0.1 and kd=1: d_error = (0 - 1) / 0.1 = -10 → D = -10
        # P = -1; D = -10; I = 0
        assert out == pytest.approx(-11.0, abs=0.1)

    def test_set_output_limits(self):
        """set_output_limits updates the clamp bounds live."""
        pid = PIDController(
            kp=100.0,
            ki=0.0,
            kd=0.0,
            setpoint=0.0,
            output_min=-1000.0,
            output_max=1000.0,
        )
        pid.set_output_limits(-10.0, 10.0)
        out = pid.compute(measurement=-100.0, dt=1.0)
        assert out == pytest.approx(10.0, abs=0.01)
