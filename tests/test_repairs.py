"""Unit tests for the repairs module."""

from __future__ import annotations

from unittest.mock import patch

from homeassistant.core import HomeAssistant

from custom_components.zero_grid_controller.repairs import (
    ISSUE_GRID_SENSOR_UNAVAILABLE,
    dismiss_grid_sensor_unavailable,
    raise_grid_sensor_unavailable,
)


class TestRaiseGridSensorUnavailable:
    def test_creates_issue(self, hass: HomeAssistant) -> None:
        """raise_grid_sensor_unavailable creates a repair issue."""
        with patch(
            "custom_components.zero_grid_controller.repairs.ir.async_create_issue"
        ) as mock_create:
            raise_grid_sensor_unavailable(hass)

        mock_create.assert_called_once()
        call_args = mock_create.call_args
        assert call_args.args[2] == ISSUE_GRID_SENSOR_UNAVAILABLE
        assert call_args.kwargs["is_fixable"] is False

    def test_issue_id_constant(self) -> None:
        """ISSUE_GRID_SENSOR_UNAVAILABLE constant has expected value."""
        assert ISSUE_GRID_SENSOR_UNAVAILABLE == "grid_sensor_unavailable"


class TestDismissGridSensorUnavailable:
    def test_deletes_issue(self, hass: HomeAssistant) -> None:
        """dismiss_grid_sensor_unavailable deletes the repair issue."""
        with patch(
            "custom_components.zero_grid_controller.repairs.ir.async_delete_issue"
        ) as mock_delete:
            dismiss_grid_sensor_unavailable(hass)

        mock_delete.assert_called_once()
        call_args = mock_delete.call_args
        assert call_args.args[2] == ISSUE_GRID_SENSOR_UNAVAILABLE

    def test_raise_then_dismiss(self, hass: HomeAssistant) -> None:
        """Raising then dismissing calls both HA issue registry functions."""
        with (
            patch(
                "custom_components.zero_grid_controller.repairs.ir.async_create_issue"
            ) as mock_create,
            patch(
                "custom_components.zero_grid_controller.repairs.ir.async_delete_issue"
            ) as mock_delete,
        ):
            raise_grid_sensor_unavailable(hass)
            dismiss_grid_sensor_unavailable(hass)

        mock_create.assert_called_once()
        mock_delete.assert_called_once()
