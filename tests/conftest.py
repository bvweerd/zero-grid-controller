"""Shared test fixtures for Zero Grid Controller tests."""
from __future__ import annotations

import pytest
from pytest_homeassistant_custom_component.common import MockConfigEntry

from custom_components.zero_grid_controller.const import DOMAIN


@pytest.fixture
def mock_config_entry() -> MockConfigEntry:
    return MockConfigEntry(
        domain=DOMAIN,
        title="Test ZGC",
        unique_id=DOMAIN,
        data={
            "name": "Test ZGC",
            "grid_measurement_type": "net",
            "grid_sensor": "sensor.grid_power",
            "invert_sign": False,
        },
        options={},
    )
