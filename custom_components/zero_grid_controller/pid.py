"""PID Controller implementation with anti-windup and integrator freeze."""

from __future__ import annotations

from .const import CONTROL_DT_MIN


class PIDController:
    """Discrete PID controller with conditional anti-windup.

    Positive output  = tighten limit (grid_w was positive = importing from grid).
    Negative output  = open limit   (grid_w was negative = exporting to grid).
    """

    def __init__(
        self,
        kp: float,
        ki: float,
        kd: float,
        setpoint: float = 0.0,
        output_min: float | None = None,
        output_max: float | None = None,
    ) -> None:
        self._kp = kp
        self._ki = ki
        self._kd = kd
        self.setpoint = setpoint
        self._output_min = output_min
        self._output_max = output_max

        self._integral: float = 0.0
        self._prev_error: float | None = None
        self._freeze: bool = False

        # Last computed components for diagnostics
        self._p: float = 0.0
        self._i: float = 0.0
        self._d: float = 0.0

    # ------------------------------------------------------------------
    # Public interface
    # ------------------------------------------------------------------

    def compute(self, measurement: float, dt: float) -> float:
        """Compute PID output for the given measurement and timestep.

        Anti-windup via conditional integration: only accumulate I-term when
        the output is not clamped, OR when error and output have the same sign
        (meaning integration would help bring the output back in range).
        """
        if dt <= 0:
            dt = CONTROL_DT_MIN

        error = self.setpoint - measurement

        # Proportional term
        self._p = self._kp * error

        # Derivative term (backward difference; skip on first call)
        if self._prev_error is not None:
            d_error = (error - self._prev_error) / dt
        else:
            d_error = 0.0
        self._d = self._kd * d_error
        self._prev_error = error

        # Anti-windup check uses only P+I (D is transient and should not affect windup detection)
        output_pi_only = self._p + self._ki * (self._integral + error * dt)
        clamped_pi_only = self._clamp(output_pi_only)
        at_limit = clamped_pi_only != output_pi_only
        # "pushing into saturation" = error and P+I output have the same sign
        pushing_into_limit = (error >= 0 and output_pi_only >= 0) or (
            error < 0 and output_pi_only < 0
        )

        if not self._freeze and not (at_limit and pushing_into_limit):
            self._integral += error * dt
        self._freeze = False  # reset per-cycle freeze flag

        self._i = self._ki * self._integral

        output = self._p + self._i + self._d
        return self._clamp(output)

    def freeze_integrator(self) -> None:
        """Freeze the I-term for the next compute() cycle.

        Call this when we cannot attribute grid changes to our own action
        (cloud shadow, battery clipping, inside deadband).
        """
        self._freeze = True

    def reset(self) -> None:
        """Reset I-term and derivative history.

        Call when the controller transitions to disabled mode.
        """
        self._integral = 0.0
        self._prev_error = None
        self._p = 0.0
        self._i = 0.0
        self._d = 0.0

    def set_gains(self, kp: float, ki: float, kd: float) -> None:
        """Update gains live without resetting the integrator."""
        self._kp = kp
        self._ki = ki
        self._kd = kd

    def set_output_limits(
        self, output_min: float | None, output_max: float | None
    ) -> None:
        """Update output clamp limits live."""
        self._output_min = output_min
        self._output_max = output_max

    # ------------------------------------------------------------------
    # Properties
    # ------------------------------------------------------------------

    @property
    def kp(self) -> float:
        return self._kp

    @property
    def ki(self) -> float:
        return self._ki

    @property
    def kd(self) -> float:
        return self._kd

    @property
    def integral(self) -> float:
        return self._integral

    @property
    def components(self) -> dict[str, float]:
        """Return last P/I/D components for diagnostics."""
        return {"p": self._p, "i": self._i, "d": self._d}

    # ------------------------------------------------------------------
    # Internal helpers
    # ------------------------------------------------------------------

    def _clamp(self, value: float) -> float:
        if self._output_min is not None and value < self._output_min:
            return self._output_min
        if self._output_max is not None and value > self._output_max:
            return self._output_max
        return value
