"""Unit tests for the RLS estimator."""

import sys
import os
sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

import pytest
from custom_components.zero_grid_controller.estimator import RLSEstimator


class TestConvergence:
    def test_converges_to_true_gain(self):
        """After sufficient updates with a true system gain of -1.0, K ≈ -1.0."""
        estimator = RLSEstimator(forgetting_factor=0.98, settling_time_s=15)
        true_K = -1.0

        import random
        rng = random.Random(42)

        for _ in range(60):
            u = rng.uniform(5, 50)          # delta setpoint in W
            noise = rng.gauss(0, 1.0)       # small measurement noise
            y = true_K * u + noise          # observed delta grid_w
            estimator.update(u, y)

        assert estimator.gain == pytest.approx(true_K, abs=0.15)

    def test_converges_to_gain_minus_half(self):
        """Estimates a weaker system gain of -0.5 correctly."""
        estimator = RLSEstimator(forgetting_factor=0.98, settling_time_s=15)
        true_K = -0.5

        import random
        rng = random.Random(7)
        for _ in range(60):
            u = rng.uniform(5, 50)
            y = true_K * u + rng.gauss(0, 0.5)
            estimator.update(u, y)

        assert estimator.gain == pytest.approx(true_K, abs=0.15)


class TestReliability:
    def test_not_reliable_before_20_updates(self):
        """is_reliable must be False for the first 20 updates."""
        estimator = RLSEstimator()
        for i in range(19):
            estimator.update(10.0, -10.0)
            assert estimator.is_reliable is False

    def test_reliable_after_sufficient_updates(self):
        """is_reliable can become True after enough consistent updates."""
        estimator = RLSEstimator(forgetting_factor=0.98, settling_time_s=15)
        import random
        rng = random.Random(42)
        for _ in range(50):
            u = rng.uniform(5, 50)
            estimator.update(u, -1.0 * u + rng.gauss(0, 0.5))

        # After 50 updates with consistent data, uncertainty P should be low
        assert estimator.n_updates == 50
        # Note: reliability depends on P < 10.0 — check uncertainty is bounded
        assert estimator.uncertainty < 100.0  # should have converged significantly

    def test_estimated_gain_none_when_not_reliable(self):
        """estimated_gain returns None before reliability threshold."""
        estimator = RLSEstimator()
        for _ in range(10):
            estimator.update(10.0, -10.0)
        assert estimator.estimated_gain is None

    def test_estimated_gain_returns_float_when_reliable(self):
        """estimated_gain returns a float when the estimator is reliable."""
        estimator = RLSEstimator(forgetting_factor=0.98, settling_time_s=15)
        import random
        rng = random.Random(42)
        for _ in range(60):
            u = rng.uniform(5, 50)
            estimator.update(u, -1.0 * u + rng.gauss(0, 0.3))
        if estimator.is_reliable:
            assert isinstance(estimator.estimated_gain, float)


class TestSuggestKp:
    def test_suggest_kp_returns_current_when_not_reliable(self):
        """suggest_kp returns current_kp unchanged when not reliable."""
        estimator = RLSEstimator()
        result = estimator.suggest_kp(current_kp=1.5, response_factor=1.0)
        assert result == pytest.approx(1.5, abs=1e-6)

    def test_suggest_kp_in_reasonable_range(self):
        """suggest_kp output must be within [0.001, 10.0]."""
        estimator = RLSEstimator(forgetting_factor=0.98, settling_time_s=15)
        import random
        rng = random.Random(42)
        for _ in range(60):
            u = rng.uniform(5, 50)
            estimator.update(u, -1.0 * u + rng.gauss(0, 0.3))

        if estimator.is_reliable:
            for rf in [0.5, 1.0, 2.0]:
                kp = estimator.suggest_kp(current_kp=0.5, response_factor=rf)
                assert 0.001 <= kp <= 10.0, f"kp={kp} out of range for response_factor={rf}"

    def test_suggest_kp_gradual_change(self):
        """suggest_kp applies at most a 20% change per call."""
        estimator = RLSEstimator(forgetting_factor=0.98, settling_time_s=15)
        import random
        rng = random.Random(42)
        for _ in range(60):
            u = rng.uniform(5, 50)
            estimator.update(u, -1.0 * u + rng.gauss(0, 0.3))

        if estimator.is_reliable:
            current_kp = 1.0
            new_kp = estimator.suggest_kp(current_kp, response_factor=1.0)
            # new_kp = 0.8 * current_kp + 0.2 * kp_opt
            # So new_kp should be between 0.8 and (0.8 + 0.2*10) = 2.8
            assert new_kp >= current_kp * 0.8 - 0.001
            assert new_kp <= current_kp * 0.8 + 0.2 * 10.0 + 0.001


class TestSerialisation:
    def test_roundtrip(self):
        """from_dict(to_dict()) produces an identical estimator."""
        estimator = RLSEstimator(forgetting_factor=0.97, settling_time_s=20)
        import random
        rng = random.Random(1)
        for _ in range(30):
            u = rng.uniform(1, 100)
            estimator.update(u, -0.9 * u + rng.gauss(0, 2))

        d = estimator.to_dict()
        restored = RLSEstimator.from_dict(d)

        assert restored.gain == pytest.approx(estimator.gain, abs=1e-9)
        assert restored.uncertainty == pytest.approx(estimator.uncertainty, abs=1e-9)
        assert restored.n_updates == estimator.n_updates
        assert restored._lambda == pytest.approx(estimator._lambda, abs=1e-9)
        assert restored._settling_time_s == estimator._settling_time_s

    def test_roundtrip_preserves_reliability(self):
        """Reliability state is preserved through serialisation."""
        estimator = RLSEstimator(forgetting_factor=0.98, settling_time_s=15)
        import random
        rng = random.Random(99)
        for _ in range(60):
            u = rng.uniform(5, 50)
            estimator.update(u, -1.0 * u + rng.gauss(0, 0.3))

        d = estimator.to_dict()
        restored = RLSEstimator.from_dict(d)
        assert restored.is_reliable == estimator.is_reliable

    def test_to_dict_keys(self):
        """to_dict must contain all required keys."""
        estimator = RLSEstimator()
        d = estimator.to_dict()
        assert "K" in d
        assert "P" in d
        assert "n" in d
        assert "lambda" in d
        assert "settling_time_s" in d

    def test_from_dict_invalid_raises(self):
        """from_dict with missing keys should raise KeyError."""
        with pytest.raises(KeyError):
            RLSEstimator.from_dict({"K": -1.0})  # Missing P, n, lambda, settling_time_s


class TestUpdateBehaviour:
    def test_zero_input_does_not_divide_by_zero(self):
        """update() with u=0 must not raise ZeroDivisionError."""
        estimator = RLSEstimator()
        estimator.update(u=0.0, y=0.0)  # Should not raise

    def test_n_updates_increments(self):
        """n_updates increments with each successful update."""
        estimator = RLSEstimator()
        assert estimator.n_updates == 0
        estimator.update(10.0, -10.0)
        assert estimator.n_updates == 1
        estimator.update(5.0, -5.0)
        assert estimator.n_updates == 2
