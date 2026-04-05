"""Online Recursive Least Squares parameter estimator for the Zero Grid Controller."""

from __future__ import annotations


class RLSEstimator:
    """Single-parameter RLS estimator: delta_grid_w ≈ K * delta_setpoint_w.

    K is the system gain. For a well-functioning system K ≈ -1.0.
    - K > -0.5  → system responds weaker than expected (cloud, saturation) — don't adjust.
    - K < -1.5  → system responds stronger than expected — reduce Kp.
    """

    def __init__(
        self,
        forgetting_factor: float = 0.98,
        settling_time_s: int = 15,
    ) -> None:
        self._lambda = forgetting_factor
        self._K: float = -1.0          # prior: gain ≈ -1
        self._P: float = 1000.0        # high initial uncertainty
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
        if abs(denominator) < 1e-9:
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
            self._n_updates >= 20
            and abs(self._K) > 0.1
            and self._P < 10.0
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

        Uses a first-order approximation: Kp_opt ≈ response_factor / (|K| * tau_est)
        where tau_est = settling_time_s / 3.

        Applies at most 20% change per call to avoid instability.
        """
        if not self.is_reliable:
            return current_kp
        tau_est = max(self._settling_time_s / 3.0, 1.0)
        kp_opt = response_factor / (abs(self._K) * tau_est)
        # Clamp to reasonable range
        kp_opt = max(0.001, min(10.0, kp_opt))
        # Gradual adaptation: max 20% change
        return current_kp * 0.8 + kp_opt * 0.2

    # ------------------------------------------------------------------
    # Serialisation
    # ------------------------------------------------------------------

    def to_dict(self) -> dict:
        """Serialise state for persistence across HA restarts."""
        return {
            "K": self._K,
            "P": self._P,
            "n": self._n_updates,
            "lambda": self._lambda,
            "settling_time_s": self._settling_time_s,
        }

    @classmethod
    def from_dict(cls, data: dict) -> "RLSEstimator":
        """Restore state from a previously serialised dict."""
        inst = cls(data["lambda"], data["settling_time_s"])
        inst._K = data["K"]
        inst._P = data["P"]
        inst._n_updates = data["n"]
        return inst
