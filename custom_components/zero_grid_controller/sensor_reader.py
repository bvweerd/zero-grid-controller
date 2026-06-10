"""SensorReader protocol for decoupled HA state access."""

from __future__ import annotations

from typing import Protocol


class SensorReader(Protocol):
    """Read HA sensor states without depending on the HomeAssistant object directly."""

    def read_sensor_safe(self, entity_id: str) -> float | None:
        """Return sensor value as float, or None if unavailable/non-numeric."""
        ...

    def read_power_w(self, entity_id: str) -> float | None:
        """Return a power sensor value in Watts (unit-converted), or None."""
        ...

    def entity_state(self, entity_id: str) -> str | None:
        """Return entity state string, or None if missing/unavailable/unknown."""
        ...
