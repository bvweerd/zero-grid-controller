"""Tests for the Zero Grid Controller __init__.py module."""
from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock, patch

import pytest

import pytest
from homeassistant.core import HomeAssistant
from pytest_homeassistant_custom_component.common import MockConfigEntry

from custom_components.zero_grid_controller import (
    _async_update_listener,
    _register_services,
    async_remove_config_entry_device,
    async_setup_entry,
)
from custom_components.zero_grid_controller.const import (
    ARRAY_SUBENTRY_TYPE,
    CONF_GRID_MEASUREMENT_TYPE,
    CONF_GRID_SENSOR,
    CONF_INVERT_SIGN,
    CONF_SETPOINT_ENTITY,
    DOMAIN,
    SERVICE_OVERRIDE_SETPOINT,
    SERVICE_RECALIBRATE,
    SERVICE_RESET_PID,
    SERVICE_SET_RESPONSE_SPEED,
)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _make_mock_hass(services_registered: bool = False) -> MagicMock:
    """Create a mock hass instance for service registration tests."""
    hass = MagicMock()
    hass.services.has_service.return_value = services_registered
    handlers: dict = {}

    def _capture(domain, service, handler, **kwargs):
        handlers[service] = handler

    hass.services.async_register.side_effect = _capture
    hass._captured_handlers = handlers
    hass.services.async_call = AsyncMock()
    return hass


# ---------------------------------------------------------------------------
# Test 1: async_remove_config_entry_device — stale device allowed
# ---------------------------------------------------------------------------

async def test_remove_stale_device() -> None:
    """Stale device (not in entry_id, not in subentries) can be removed."""
    device_entry = MagicMock()
    device_entry.identifiers = {(DOMAIN, "stale_id")}
    config_entry = MagicMock()
    config_entry.entry_id = "main_entry"
    config_entry.subentries = {}

    result = await async_remove_config_entry_device(None, config_entry, device_entry)
    assert result is True


# ---------------------------------------------------------------------------
# Test 2: async_remove_config_entry_device — main device blocked
# ---------------------------------------------------------------------------

async def test_remove_main_device_blocked() -> None:
    """Main device cannot be removed while the entry is active."""
    device_entry = MagicMock()
    device_entry.identifiers = {(DOMAIN, "main_entry")}
    config_entry = MagicMock()
    config_entry.entry_id = "main_entry"
    config_entry.subentries = {}

    result = await async_remove_config_entry_device(None, config_entry, device_entry)
    assert result is False


# ---------------------------------------------------------------------------
# Test 3: async_remove_config_entry_device — active subentry blocked
# ---------------------------------------------------------------------------

async def test_remove_active_subentry_blocked() -> None:
    """Active subentry device cannot be removed."""
    subentry_id = "subentry_1"
    device_entry = MagicMock()
    device_entry.identifiers = {(DOMAIN, subentry_id)}
    config_entry = MagicMock()
    config_entry.entry_id = "main_entry"
    config_entry.subentries = {subentry_id: MagicMock()}

    result = await async_remove_config_entry_device(None, config_entry, device_entry)
    assert result is False


# ---------------------------------------------------------------------------
# Test 4: async_remove_config_entry_device — non-domain identifier skipped
# ---------------------------------------------------------------------------

async def test_remove_device_non_domain_identifier() -> None:
    """Identifiers from other domains are skipped, stale device is removable."""
    device_entry = MagicMock()
    device_entry.identifiers = {("other_domain", "some_id"), (DOMAIN, "stale_id")}
    config_entry = MagicMock()
    config_entry.entry_id = "main_entry"
    config_entry.subentries = {}

    result = await async_remove_config_entry_device(None, config_entry, device_entry)
    assert result is True


# ---------------------------------------------------------------------------
# Test 5: _async_update_listener — early return when runtime_data is None
# ---------------------------------------------------------------------------

async def test_update_listener_no_runtime_data() -> None:
    """Listener returns early when runtime_data is None (entry not set up)."""
    hass = MagicMock()
    hass.config_entries.async_reload = AsyncMock()
    entry = MagicMock()
    entry.runtime_data = None

    await _async_update_listener(hass, entry)

    hass.config_entries.async_reload.assert_not_called()


# ---------------------------------------------------------------------------
# Test 6: _async_update_listener — triggers reload when runtime_data exists
# ---------------------------------------------------------------------------

async def test_update_listener_triggers_reload() -> None:
    """Listener triggers entry reload when runtime_data is present."""
    hass = MagicMock()
    hass.config_entries.async_reload = AsyncMock()
    entry = MagicMock()
    entry.runtime_data = MagicMock()
    entry.entry_id = "test_entry"

    await _async_update_listener(hass, entry)

    hass.config_entries.async_reload.assert_called_once_with("test_entry")


# ---------------------------------------------------------------------------
# Test 7: _register_services — early return when already registered
# ---------------------------------------------------------------------------

def test_register_services_early_return() -> None:
    """_register_services is a no-op when services are already registered."""
    hass = _make_mock_hass(services_registered=True)
    _register_services(hass)
    hass.services.async_register.assert_not_called()


# ---------------------------------------------------------------------------
# Test 8: _handle_reset_pid — called for all entries (no entry_id filter)
# ---------------------------------------------------------------------------

async def test_handle_reset_pid_all_entries() -> None:
    """reset_pid service calls coordinator.reset_pid() on all active entries."""
    hass = _make_mock_hass()
    _register_services(hass)

    coordinator = MagicMock()
    coordinator.reset_pid = MagicMock()
    runtime_data = MagicMock()
    runtime_data.coordinator = coordinator

    mock_entry = MagicMock()
    mock_entry.entry_id = "entry_1"
    mock_entry.runtime_data = runtime_data

    hass.config_entries.async_entries.return_value = [mock_entry]

    call = MagicMock()
    call.data = {}

    handler = hass._captured_handlers[SERVICE_RESET_PID]
    await handler(call)

    coordinator.reset_pid.assert_called_once()


# ---------------------------------------------------------------------------
# Test 9: _handle_reset_pid — filtered by entry_id
# ---------------------------------------------------------------------------

async def test_handle_reset_pid_entry_id_filter() -> None:
    """reset_pid with entry_id only resets matching entries."""
    hass = _make_mock_hass()
    _register_services(hass)

    coordinator_1 = MagicMock()
    coordinator_2 = MagicMock()

    entry_1 = MagicMock()
    entry_1.entry_id = "entry_1"
    entry_1.runtime_data = MagicMock()
    entry_1.runtime_data.coordinator = coordinator_1

    entry_2 = MagicMock()
    entry_2.entry_id = "entry_2"
    entry_2.runtime_data = MagicMock()
    entry_2.runtime_data.coordinator = coordinator_2

    hass.config_entries.async_entries.return_value = [entry_1, entry_2]

    call = MagicMock()
    call.data = {"entry_id": "entry_2"}

    handler = hass._captured_handlers[SERVICE_RESET_PID]
    await handler(call)

    coordinator_1.reset_pid.assert_not_called()
    coordinator_2.reset_pid.assert_called_once()


# ---------------------------------------------------------------------------
# Test 10: _handle_reset_pid — skips entries without runtime_data
# ---------------------------------------------------------------------------

async def test_handle_reset_pid_no_runtime_data() -> None:
    """reset_pid skips entries where runtime_data is falsy."""
    hass = _make_mock_hass()
    _register_services(hass)

    mock_entry = MagicMock()
    mock_entry.entry_id = "entry_1"
    mock_entry.runtime_data = None

    hass.config_entries.async_entries.return_value = [mock_entry]

    call = MagicMock()
    call.data = {}

    handler = hass._captured_handlers[SERVICE_RESET_PID]
    await handler(call)  # should not raise


# ---------------------------------------------------------------------------
# Test 11: _handle_recalibrate — creates calibration task
# ---------------------------------------------------------------------------

async def test_handle_recalibrate() -> None:
    """recalibrate service creates a calibration task for matching arrays."""
    hass = _make_mock_hass()
    _register_services(hass)

    array = MagicMock()
    array.name = "PV West"

    coordinator = MagicMock()
    coordinator.arrays = [array]
    coordinator.grid_entity = "sensor.grid_power"
    coordinator.invert_sign = False

    mock_entry = MagicMock()
    mock_entry.runtime_data = MagicMock()
    mock_entry.runtime_data.coordinator = coordinator

    hass.config_entries.async_entries.return_value = [mock_entry]
    hass.async_create_task = MagicMock()

    call = MagicMock()
    call.data = {}

    handler = hass._captured_handlers[SERVICE_RECALIBRATE]

    with patch(
        "custom_components.zero_grid_controller.ArrayCalibrator"
    ) as MockCalibrator:
        mock_cal = MagicMock()
        mock_cal.run = AsyncMock()
        MockCalibrator.return_value = mock_cal
        await handler(call)

    hass.async_create_task.assert_called_once()


# ---------------------------------------------------------------------------
# Test 12: _handle_recalibrate — filters by array_name
# ---------------------------------------------------------------------------

async def test_handle_recalibrate_array_name_filter() -> None:
    """recalibrate with array_name only calibrates matching arrays."""
    hass = _make_mock_hass()
    _register_services(hass)

    array_a = MagicMock()
    array_a.name = "PV West"
    array_b = MagicMock()
    array_b.name = "PV Zuid"

    coordinator = MagicMock()
    coordinator.arrays = [array_a, array_b]
    coordinator.grid_entity = "sensor.grid_power"
    coordinator.invert_sign = False

    mock_entry = MagicMock()
    mock_entry.runtime_data = MagicMock()
    mock_entry.runtime_data.coordinator = coordinator

    hass.config_entries.async_entries.return_value = [mock_entry]
    hass.async_create_task = MagicMock()

    call = MagicMock()
    call.data = {"array_name": "PV West"}

    handler = hass._captured_handlers[SERVICE_RECALIBRATE]

    with patch("custom_components.zero_grid_controller.ArrayCalibrator") as MockCalibrator:
        mock_cal = MagicMock()
        mock_cal.run = AsyncMock()
        MockCalibrator.return_value = mock_cal
        await handler(call)

    hass.async_create_task.assert_called_once()


# ---------------------------------------------------------------------------
# Test 13: _handle_recalibrate — skips entry without runtime_data
# ---------------------------------------------------------------------------

async def test_handle_recalibrate_no_runtime_data() -> None:
    """recalibrate skips entries without runtime_data."""
    hass = _make_mock_hass()
    _register_services(hass)

    mock_entry = MagicMock()
    mock_entry.runtime_data = None

    hass.config_entries.async_entries.return_value = [mock_entry]
    hass.async_create_task = MagicMock()

    call = MagicMock()
    call.data = {}

    handler = hass._captured_handlers[SERVICE_RECALIBRATE]
    await handler(call)

    hass.async_create_task.assert_not_called()


# ---------------------------------------------------------------------------
# Test 14: _handle_recalibrate — no matching arrays, skips task creation
# ---------------------------------------------------------------------------

async def test_handle_recalibrate_no_matching_arrays() -> None:
    """recalibrate does not create task when no arrays match the filter."""
    hass = _make_mock_hass()
    _register_services(hass)

    array = MagicMock()
    array.name = "PV West"

    coordinator = MagicMock()
    coordinator.arrays = [array]

    mock_entry = MagicMock()
    mock_entry.runtime_data = MagicMock()
    mock_entry.runtime_data.coordinator = coordinator

    hass.config_entries.async_entries.return_value = [mock_entry]
    hass.async_create_task = MagicMock()

    call = MagicMock()
    call.data = {"array_name": "PV East"}  # No match

    handler = hass._captured_handlers[SERVICE_RECALIBRATE]
    await handler(call)

    hass.async_create_task.assert_not_called()


# ---------------------------------------------------------------------------
# Test 15: _handle_set_response_speed — valid speed updates options
# ---------------------------------------------------------------------------

async def test_handle_set_response_speed_valid() -> None:
    """set_response_speed with a valid speed updates entry options."""
    hass = _make_mock_hass()
    _register_services(hass)

    mock_entry = MagicMock()
    mock_entry.options = {}
    hass.config_entries.async_entries.return_value = [mock_entry]

    call = MagicMock()
    call.data = {"speed": "fast"}

    handler = hass._captured_handlers[SERVICE_SET_RESPONSE_SPEED]
    await handler(call)

    hass.config_entries.async_update_entry.assert_called()


# ---------------------------------------------------------------------------
# Test 16: _handle_set_response_speed — unknown speed logs error
# ---------------------------------------------------------------------------

async def test_handle_set_response_speed_invalid() -> None:
    """set_response_speed with an unknown speed logs an error and does nothing."""
    hass = _make_mock_hass()
    _register_services(hass)

    hass.config_entries.async_entries.return_value = []

    call = MagicMock()
    call.data = {"speed": "turbo"}  # invalid

    handler = hass._captured_handlers[SERVICE_SET_RESPONSE_SPEED]
    await handler(call)  # should not raise

    hass.config_entries.async_update_entry.assert_not_called()


# ---------------------------------------------------------------------------
# Test 17: _handle_override_setpoint — with runtime_data
# ---------------------------------------------------------------------------

async def test_handle_override_setpoint() -> None:
    """override_setpoint service calls coordinator.override_setpoint."""
    hass = _make_mock_hass()
    _register_services(hass)

    coordinator = MagicMock()
    coordinator.override_setpoint = MagicMock()

    mock_entry = MagicMock()
    mock_entry.runtime_data = MagicMock()
    mock_entry.runtime_data.coordinator = coordinator

    hass.config_entries.async_entries.return_value = [mock_entry]

    call = MagicMock()
    call.data = {"array_name": "PV West", "value": 75.0}

    handler = hass._captured_handlers[SERVICE_OVERRIDE_SETPOINT]
    await handler(call)

    coordinator.override_setpoint.assert_called_once_with("PV West", 75.0)


# ---------------------------------------------------------------------------
# Test 18: _handle_override_setpoint — skips entry without runtime_data
# ---------------------------------------------------------------------------

async def test_handle_override_setpoint_no_runtime() -> None:
    """override_setpoint skips entries without runtime_data."""
    hass = _make_mock_hass()
    _register_services(hass)

    mock_entry = MagicMock()
    mock_entry.runtime_data = None

    hass.config_entries.async_entries.return_value = [mock_entry]

    call = MagicMock()
    call.data = {"array_name": "PV West", "value": 75.0}

    handler = hass._captured_handlers[SERVICE_OVERRIDE_SETPOINT]
    await handler(call)  # should not raise


# ---------------------------------------------------------------------------
# Test 19: async_setup_entry — subentry loop covers lines 73-76
# ---------------------------------------------------------------------------

@pytest.fixture(autouse=True)
def auto_enable_custom_integrations(enable_custom_integrations):
    """Enable custom integrations for all tests in this module."""
    return


async def test_setup_entry_with_subentries(hass: HomeAssistant) -> None:
    """async_setup_entry creates DeviceInfo for ARRAY_SUBENTRY_TYPE subentries only."""
    entry = MockConfigEntry(
        domain=DOMAIN,
        unique_id=DOMAIN,
        data={
            "name": "Test ZGC",
            CONF_GRID_MEASUREMENT_TYPE: "net",
            CONF_GRID_SENSOR: "sensor.grid_power",
            CONF_INVERT_SIGN: False,
        },
        options={},
        subentries_data=[
            {
                "subentry_type": ARRAY_SUBENTRY_TYPE,
                "title": "PV West",
                "data": {
                    "array_name": "PV West",
                    CONF_SETPOINT_ENTITY: "number.pv_west_limit",
                },
                "unique_id": None,
            },
            {
                "subentry_type": "other_type",
                "title": "Other",
                "data": {},
                "unique_id": None,
            },
        ],
    )
    entry.add_to_hass(hass)
    hass.states.async_set("sensor.grid_power", "0")

    # Patch the HA lifecycle methods that are not under test here
    with patch.object(
        hass.config_entries, "async_forward_entry_setups", new=AsyncMock(return_value=None)
    ), patch(
        "custom_components.zero_grid_controller.ZeroGridCoordinator.async_config_entry_first_refresh",
        new=AsyncMock(return_value=None),
    ):
        result = await async_setup_entry(hass, entry)

    assert result is True
    # Only the ARRAY_SUBENTRY_TYPE subentry should produce an array_device
    assert len(entry.runtime_data.array_devices) == 1
