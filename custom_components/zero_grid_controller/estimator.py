"""Online Recursive Least Squares parameter estimator for the Zero Grid Controller."""

from __future__ import annotations

from typing import Any

from .const import (
    RLS_EPSILON,
    RLS_KP_BLEND_FACTOR,
    RLS_KP_MAX,
    RLS_KP_MIN,
    RLS_MAX_UNCERTAINTY,
    RLS_MIN_GAIN_ABS,
    RLS_MIN_UPDATES,
)


class RLSEstimator:
    """Single-parameter RLS estimator: delta_grid_w ≈ K * delta_setpoint_w.

    K is the system gain. For a well-functioning system K ≈ -1.0.
    - K > -0.5  → system responds weaker than expected (cloud, saturation) — don't adjust.
    - K < -1.5  → system responds stronger than expected — reduce Kp.
    """

    def __init__(
        self,
        forgetting_factor_per_s: float = 0.99,
        settling_time_s: int = 15,
        update_interval_s: float = 5.0,
    ) -> None:
        # Convert per-second factor to per-update-cycle factor
        self._lambda = forgetting_factor_per_s**update_interval_s
        self._forgetting_factor_per_s = forgetting_factor_per_s
        self._update_interval_s = update_interval_s
        self._K: float = -1.0  # prior: gain ≈ -1
        self._P: float = 1000.0  # high initial uncertainty
        self._n_updates: int = 0
        self._settling_time_s = settling_time_s

    # ------------------------------------------------------------------
    # Core RLS update
    # ------------------------------------------------------------------

    def update(self, u: float, y: float) -> float:
        """Update the gain estimate.

        Args:
            u: delta_setpoint_w  — the setpoint change we applied one cycle ago.
            y: delta_grid_w      — the observed grid change this cycle.

        Returns:
            Updated estimated gain K.
        """
        y_hat = self._K * u
        e = y - y_hat
        denominator = self._lambda + u * self._P * u
        if abs(denominator) < RLS_EPSILON:
            return self._K
        g = self._P * u / denominator
        self._K += g * e
        self._P = (self._P - g * u * self._P) / self._lambda
        self._n_updates += 1
        return self._K

    # ------------------------------------------------------------------
    # Properties
    # ------------------------------------------------------------------

    @property
    def is_reliable(self) -> bool:
        """True when the estimate is trustworthy enough to use for auto-tuning."""
        return (
            self._n_updates >= RLS_MIN_UPDATES
            and abs(self._K) > RLS_MIN_GAIN_ABS
            and self._P < RLS_MAX_UNCERTAINTY
        )

    @property
    def estimated_gain(self) -> float | None:
        """Return gain estimate if reliable, else None."""
        return self._K if self.is_reliable else None

    @property
    def n_updates(self) -> int:
        return self._n_updates

    @property
    def gain(self) -> float:
        return self._K

    @property
    def uncertainty(self) -> float:
        return self._P

    # ------------------------------------------------------------------
    # Auto-tuning suggestion
    # ------------------------------------------------------------------

    def suggest_kp(self, current_kp: float, response_factor: float) -> float:
        """Suggest a new Kp based on the estimated system gain.

        Optimal Kp for a unity closed-loop gain: Kp = response_factor / |K|.
        Applies at most 20 % change per call to avoid instability.
        """
        if not self.is_reliable:
            return current_kp
        kp_opt = max(RLS_KP_MIN, min(RLS_KP_MAX, response_factor / abs(self._K)))
        return current_kp * (1.0 - RLS_KP_BLEND_FACTOR) + kp_opt * RLS_KP_BLEND_FACTOR

    # ------------------------------------------------------------------
    # Serialisation
    # ------------------------------------------------------------------

    def to_dict(self) -> dict[str, Any]:
        """Serialise state for persistence across HA restarts."""
        return {
            "K": self._K,
            "P": self._P,
            "n": self._n_updates,
            "forgetting_factor_per_s": self._forgetting_factor_per_s,
            "update_interval_s": self._update_interval_s,
            "settling_time_s": self._settling_time_s,
        }

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> RLSEstimator:
        """Restore state from a previously serialised dict."""
        # Support both old format (lambda key) and new format (forgetting_factor_per_s)
        if "forgetting_factor_per_s" in data:
            inst = cls(
                data["forgetting_factor_per_s"],
                data["settling_time_s"],
                data.get("update_interval_s", 5.0),
            )
        else:
            # Legacy: stored per-cycle lambda; convert back to approximate per-second
            lam_per_cycle = data.get("lambda", 0.98)
            update_interval_s = data.get("update_interval_s", 5.0)
            # lambda_per_s = lambda_per_cycle ^ (1/update_interval_s)
            forgetting_factor_per_s = lam_per_cycle ** (1.0 / update_interval_s)
            inst = cls(
                forgetting_factor_per_s, data["settling_time_s"], update_interval_s
            )
        inst._K = data["K"]
        inst._P = data["P"]
        inst._n_updates = data["n"]
        return inst
