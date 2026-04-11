"""Config flow for Zero Grid Controller."""

from __future__ import annotations

import logging
from typing import Any

import voluptuous as vol
from homeassistant import config_entries
from homeassistant.config_entries import ConfigFlowResult, SubentryFlowResult
from homeassistant.core import callback
from homeassistant.helpers.selector import selector

from .const import (
    ARRAY_SUBENTRY_TYPE,
    BATTERY_SUBENTRY_TYPE,
    CALIBRATION_CONFIDENCE_ESTIMATED,
    CONF_AGGRESSIVENESS,
    CONF_ARRAY_NAME,
    CONF_BATTERY_MAX_CHARGE_W,
    CONF_BATTERY_MAX_DISCHARGE_W,
    CONF_BATTERY_SENSOR,
    CONF_BATTERY_SETPOINT_ENTITY,
    CONF_DEADBAND_W,
    CONF_EWM_ALPHA,
    CONF_GRID_EXPORT_SENSORS,
    CONF_GRID_IMPORT_SENSORS,
    CONF_NAME,
    CONF_OUTPUT_TYPE,
    CONF_SETPOINT_ENTITY,
    CONF_SETPOINT_MAX,
    CONF_SETPOINT_MIN,
    CONF_SWITCH_DEBOUNCE_S,
    CONF_SWITCH_OFF_THRESHOLD_W,
    CONF_SWITCH_ON_THRESHOLD_W,
    CONF_W_PER_UNIT,
    DEFAULT_AGGRESSIVENESS,
    DEFAULT_BATTERY_MAX_CHARGE_W,
    DEFAULT_BATTERY_MAX_DISCHARGE_W,
    DEFAULT_DEADBAND_W,
    DEFAULT_EWM_ALPHA,
    DEFAULT_SETPOINT_MAX,
    DEFAULT_SETPOINT_MIN,
    DEFAULT_SWITCH_DEBOUNCE_S,
    DEFAULT_SWITCH_OFF_THRESHOLD_W,
    DEFAULT_SWITCH_ON_THRESHOLD_W,
    DEFAULT_W_PER_UNIT,
    DOMAIN,
    OUTPUT_TYPE_PERCENT,
    OUTPUT_TYPE_SWITCH,
    OUTPUT_TYPE_WATT,
)

_LOGGER = logging.getLogger(__name__)

_POWER_SENSOR_MULTI = selector(
    {"entity": {"domain": "sensor", "device_class": "power", "multiple": True}}
)
_POWER_SENSOR_SINGLE = selector(
    {"entity": {"domain": "sensor", "device_class": "power"}}
)


# ---------------------------------------------------------------------------
# Schemas
# ---------------------------------------------------------------------------


def _main_schema(defaults: dict[str, Any]) -> vol.Schema:
    return vol.Schema(
        {
            vol.Required(CONF_NAME, default=defaults.get(CONF_NAME, "Zero Grid")): str,
            vol.Required(
                CONF_GRID_IMPORT_SENSORS,
                default=defaults.get(CONF_GRID_IMPORT_SENSORS, []),
            ): _POWER_SENSOR_MULTI,
            vol.Required(
                CONF_GRID_EXPORT_SENSORS,
                default=defaults.get(CONF_GRID_EXPORT_SENSORS, []),
            ): _POWER_SENSOR_MULTI,
            vol.Optional(
                CONF_DEADBAND_W,
                default=defaults.get(CONF_DEADBAND_W, DEFAULT_DEADBAND_W),
            ): selector(
                {
                    "number": {
                        "min": 0,
                        "max": 500,
                        "step": 1,
                        "unit_of_measurement": "W",
                    }
                }
            ),
            vol.Optional(
                CONF_EWM_ALPHA,
                default=defaults.get(CONF_EWM_ALPHA, DEFAULT_EWM_ALPHA),
            ): selector({"number": {"min": 0.05, "max": 1.0, "step": 0.05}}),
            vol.Optional(
                CONF_AGGRESSIVENESS,
                default=defaults.get(CONF_AGGRESSIVENESS, DEFAULT_AGGRESSIVENESS),
            ): selector(
                {
                    "select": {
                        "options": ["cautious", "normal", "fast"],
                        "translation_key": "aggressiveness",
                    }
                }
            ),
        }
    )


def _array_type_schema(defaults: dict[str, Any]) -> vol.Schema:
    return vol.Schema(
        {
            vol.Required(
                CONF_ARRAY_NAME, default=defaults.get(CONF_ARRAY_NAME, "")
            ): str,
            vol.Required(
                CONF_OUTPUT_TYPE,
                default=defaults.get(CONF_OUTPUT_TYPE, OUTPUT_TYPE_PERCENT),
            ): selector(
                {
                    "select": {
                        "options": [
                            OUTPUT_TYPE_PERCENT,
                            OUTPUT_TYPE_WATT,
                            OUTPUT_TYPE_SWITCH,
                        ],
                        "translation_key": "output_type",
                    }
                }
            ),
        }
    )


def _array_numeric_schema(defaults: dict[str, Any]) -> vol.Schema:
    return vol.Schema(
        {
            vol.Required(
                CONF_SETPOINT_ENTITY,
                default=defaults.get(CONF_SETPOINT_ENTITY, ""),
            ): selector({"entity": {"domain": ["number", "input_number"]}}),
            vol.Required(
                CONF_SETPOINT_MIN,
                default=defaults.get(CONF_SETPOINT_MIN, DEFAULT_SETPOINT_MIN),
            ): selector({"number": {"min": 0, "max": 10000, "step": 1}}),
            vol.Required(
                CONF_SETPOINT_MAX,
                default=defaults.get(CONF_SETPOINT_MAX, DEFAULT_SETPOINT_MAX),
            ): selector({"number": {"min": 1, "max": 10000, "step": 1}}),
        }
    )


def _array_switch_schema(defaults: dict[str, Any]) -> vol.Schema:
    return vol.Schema(
        {
            vol.Required(
                CONF_SETPOINT_ENTITY,
                default=defaults.get(CONF_SETPOINT_ENTITY, ""),
            ): selector({"entity": {"domain": "switch"}}),
            vol.Required(
                CONF_SWITCH_ON_THRESHOLD_W,
                default=defaults.get(
                    CONF_SWITCH_ON_THRESHOLD_W, DEFAULT_SWITCH_ON_THRESHOLD_W
                ),
            ): selector(
                {
                    "number": {
                        "min": 0,
                        "max": 10000,
                        "step": 10,
                        "unit_of_measurement": "W",
                    }
                }
            ),
            vol.Required(
                CONF_SWITCH_OFF_THRESHOLD_W,
                default=defaults.get(
                    CONF_SWITCH_OFF_THRESHOLD_W, DEFAULT_SWITCH_OFF_THRESHOLD_W
                ),
            ): selector(
                {
                    "number": {
                        "min": 0,
                        "max": 10000,
                        "step": 10,
                        "unit_of_measurement": "W",
                    }
                }
            ),
            vol.Optional(
                CONF_SWITCH_DEBOUNCE_S,
                default=defaults.get(CONF_SWITCH_DEBOUNCE_S, DEFAULT_SWITCH_DEBOUNCE_S),
            ): selector(
                {
                    "number": {
                        "min": 5,
                        "max": 300,
                        "step": 5,
                        "unit_of_measurement": "s",
                    }
                }
            ),
        }
    )


def _battery_schema(defaults: dict[str, Any]) -> vol.Schema:
    return vol.Schema(
        {
            vol.Required(CONF_NAME, default=defaults.get(CONF_NAME, "")): str,
            vol.Required(
                CONF_BATTERY_SENSOR,
                default=defaults.get(CONF_BATTERY_SENSOR, ""),
            ): _POWER_SENSOR_SINGLE,
            vol.Required(
                CONF_BATTERY_MAX_CHARGE_W,
                default=defaults.get(
                    CONF_BATTERY_MAX_CHARGE_W, DEFAULT_BATTERY_MAX_CHARGE_W
                ),
            ): selector(
                {
                    "number": {
                        "min": 100,
                        "max": 50000,
                        "step": 100,
                        "unit_of_measurement": "W",
                    }
                }
            ),
            vol.Required(
                CONF_BATTERY_MAX_DISCHARGE_W,
                default=defaults.get(
                    CONF_BATTERY_MAX_DISCHARGE_W, DEFAULT_BATTERY_MAX_DISCHARGE_W
                ),
            ): selector(
                {
                    "number": {
                        "min": 100,
                        "max": 50000,
                        "step": 100,
                        "unit_of_measurement": "W",
                    }
                }
            ),
            vol.Required(
                CONF_BATTERY_SETPOINT_ENTITY,
                default=defaults.get(CONF_BATTERY_SETPOINT_ENTITY, ""),
            ): selector({"entity": {"domain": ["number", "input_number"]}}),
        }
    )


# ---------------------------------------------------------------------------
# Array subentry flow
# ---------------------------------------------------------------------------


class ArraySubEntryFlow(config_entries.ConfigSubentryFlow):
    """Flow for adding or editing a PV array subentry."""

    def __init__(self) -> None:
        super().__init__()
        self._draft: dict[str, Any] = {}
        self._reconfigure_mode: bool = False

    async def async_step_user(
        self, user_input: dict[str, Any] | None = None
    ) -> SubentryFlowResult:
        """Step 1 (add): name and output type."""
        errors: dict[str, str] = {}
        if user_input is not None:
            name = str(user_input.get(CONF_ARRAY_NAME) or "").strip()
            if name and self._array_name_exists(name, current_id=None):
                errors[CONF_ARRAY_NAME] = "duplicate_name"
            if not errors:
                self._draft.update(user_input)
                return await self._step_params()
        return self.async_show_form(
            step_id="user", data_schema=_array_type_schema(self._draft), errors=errors
        )

    async def async_step_reconfigure(
        self, user_input: dict[str, Any] | None = None
    ) -> SubentryFlowResult:
        """Step 1 (reconfigure): pre-filled name and output type."""
        self._reconfigure_mode = True
        if not self._draft:
            self._draft = dict(self._get_reconfigure_subentry().data)
        if user_input is not None:
            self._draft.update(user_input)
            return await self._step_params()
        return self.async_show_form(
            step_id="reconfigure", data_schema=_array_type_schema(self._draft)
        )

    async def async_step_numeric_params(
        self, user_input: dict[str, Any] | None = None
    ) -> SubentryFlowResult:
        """Step 2a: numeric setpoint parameters."""
        errors: dict[str, str] = {}
        if user_input is not None:
            if float(user_input[CONF_SETPOINT_MIN]) >= float(
                user_input[CONF_SETPOINT_MAX]
            ):
                errors[CONF_SETPOINT_MIN] = "min_gte_max"
            if not errors:
                self._draft.update(user_input)
                return self._finish()
        return self.async_show_form(
            step_id="numeric_params",
            data_schema=_array_numeric_schema(self._draft),
            errors=errors,
        )

    async def async_step_switch_params(
        self, user_input: dict[str, Any] | None = None
    ) -> SubentryFlowResult:
        """Step 2b: switch hysteresis parameters."""
        errors: dict[str, str] = {}
        if user_input is not None:
            if float(user_input[CONF_SWITCH_OFF_THRESHOLD_W]) >= float(
                user_input[CONF_SWITCH_ON_THRESHOLD_W]
            ):
                errors[CONF_SWITCH_OFF_THRESHOLD_W] = "off_gte_on"
            if not errors:
                self._draft.update(user_input)
                return self._finish()
        return self.async_show_form(
            step_id="switch_params",
            data_schema=_array_switch_schema(self._draft),
            errors=errors,
        )

    def _array_name_exists(self, name: str, current_id: str | None) -> bool:
        for se in self._get_entry().subentries.values():
            if se.subentry_type != ARRAY_SUBENTRY_TYPE:
                continue
            if se.subentry_id == current_id:
                continue
            if se.data.get(CONF_ARRAY_NAME, "").lower() == name.lower():
                return True
        return False

    async def _step_params(self) -> SubentryFlowResult:
        if self._draft.get(CONF_OUTPUT_TYPE) == OUTPUT_TYPE_SWITCH:
            return await self.async_step_switch_params()
        return await self.async_step_numeric_params()

    def _finish(self) -> SubentryFlowResult:
        data = {
            **self._draft,
            CONF_W_PER_UNIT: self._draft.get(CONF_W_PER_UNIT, DEFAULT_W_PER_UNIT),
            "calibration_confidence": self._draft.get(
                "calibration_confidence", CALIBRATION_CONFIDENCE_ESTIMATED
            ),
        }
        if self._reconfigure_mode:
            return self.async_update_and_abort(
                self._get_entry(), self._get_reconfigure_subentry(), data=data
            )
        return self.async_create_entry(title=self._draft[CONF_ARRAY_NAME], data=data)


# ---------------------------------------------------------------------------
# Battery subentry flow
# ---------------------------------------------------------------------------


class BatterySubEntryFlow(config_entries.ConfigSubentryFlow):
    """Flow for adding or editing a battery subentry."""

    def __init__(self) -> None:
        super().__init__()
        self._draft: dict[str, Any] = {}

    async def async_step_user(
        self, user_input: dict[str, Any] | None = None
    ) -> SubentryFlowResult:
        """Single step: all battery settings."""
        errors: dict[str, str] = {}
        if user_input is not None:
            name = str(user_input.get(CONF_NAME) or "").strip()
            if name and self._name_exists(name, current_id=None):
                errors[CONF_NAME] = "duplicate_name"
            if not errors:
                return self.async_create_entry(
                    title=user_input[CONF_NAME], data=user_input
                )
        return self.async_show_form(
            step_id="user",
            data_schema=_battery_schema(user_input or {}),
            errors=errors,
        )

    async def async_step_reconfigure(
        self, user_input: dict[str, Any] | None = None
    ) -> SubentryFlowResult:
        """Reconfigure: pre-filled battery settings."""
        if not self._draft:
            self._draft = dict(self._get_reconfigure_subentry().data)
        if user_input is not None:
            return self.async_update_and_abort(
                self._get_entry(), self._get_reconfigure_subentry(), data=user_input
            )
        return self.async_show_form(
            step_id="reconfigure", data_schema=_battery_schema(self._draft)
        )

    def _name_exists(self, name: str, current_id: str | None) -> bool:
        for se in self._get_entry().subentries.values():
            if se.subentry_type != BATTERY_SUBENTRY_TYPE:
                continue
            if se.subentry_id == current_id:
                continue
            if se.data.get(CONF_NAME, "").lower() == name.lower():
                return True
        return False


# ---------------------------------------------------------------------------
# Main config flow (must come after subentry flow class definitions)
# ---------------------------------------------------------------------------


class ZeroGridConfigFlow(config_entries.ConfigFlow, domain=DOMAIN):
    """Setup wizard for Zero Grid Controller."""

    VERSION = 1

    @classmethod
    @callback
    def async_get_supported_subentry_types(
        cls, config_entry: config_entries.ConfigEntry
    ) -> dict[str, type[config_entries.ConfigSubentryFlow]]:
        """Return supported subentry flow handlers."""
        return {
            ARRAY_SUBENTRY_TYPE: ArraySubEntryFlow,
            BATTERY_SUBENTRY_TYPE: BatterySubEntryFlow,
        }

    async def async_step_user(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        errors: dict[str, str] = {}
        if user_input is not None:
            if not user_input.get(CONF_GRID_IMPORT_SENSORS) and not user_input.get(
                CONF_GRID_EXPORT_SENSORS
            ):
                errors["base"] = "no_sensors"
            if not errors:
                return self.async_create_entry(
                    title=user_input[CONF_NAME],
                    data=user_input,
                )
        return self.async_show_form(
            step_id="user",
            data_schema=_main_schema(user_input or {}),
            errors=errors,
        )

    @staticmethod
    @callback
    def async_get_options_flow(
        config_entry: config_entries.ConfigEntry,
    ) -> ZeroGridOptionsFlow:
        return ZeroGridOptionsFlow(config_entry)


class ZeroGridOptionsFlow(config_entries.OptionsFlow):
    """Options flow: edit main settings."""

    def __init__(self, config_entry: config_entries.ConfigEntry) -> None:
        self._config_entry = config_entry

    async def async_step_init(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        defaults = {**self._config_entry.data, **self._config_entry.options}
        if user_input is not None:
            return self.async_create_entry(data=user_input)
        return self.async_show_form(
            step_id="init",
            data_schema=_main_schema(user_input or defaults),
        )
