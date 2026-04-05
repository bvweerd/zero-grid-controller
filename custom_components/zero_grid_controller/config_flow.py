"""Config flow for Zero Grid Controller."""

from __future__ import annotations

import logging
from typing import Any

import voluptuous as vol

from homeassistant import config_entries
from homeassistant.config_entries import ConfigFlowResult, SubentryFlowResult
from homeassistant.core import HomeAssistant, callback
from homeassistant.helpers.selector import selector

from .const import (
    ARRAY_SUBENTRY_TYPE,
    CONF_ARRAY_NAME,
    CONF_BATTERY_CONTROL_ENABLED,
    CONF_BATTERY_MAX_CHARGE_W,
    CONF_BATTERY_MAX_DISCHARGE_W,
    CONF_BATTERY_SENSOR,
    CONF_BATTERY_SETPOINT_ENTITY,
    CONF_CALIBRATION_CONFIDENCE,
    CONF_DEADBAND_W,
    CONF_EWM_ALPHA,
    CONF_EXPERT_MODE,
    CONF_GRID_MEASUREMENT_TYPE,
    CONF_GRID_SENSOR,
    CONF_GRID_SENSOR_EXPORT,
    CONF_GRID_SENSOR_IMPORT,
    CONF_INVERT_SIGN,
    CONF_INVERTER_SPEED,
    CONF_KD,
    CONF_KI,
    CONF_KP,
    CONF_MODE_GUARD_ENABLED,
    CONF_MODE_GUARD_ENTITY,
    CONF_MODE_GUARD_MAPPING,
    CONF_NAME,
    CONF_OUTPUT_MAX_W,
    CONF_OUTPUT_TYPE,
    CONF_PRIORITY,
    CONF_PV_POWER_ENTITY,
    CONF_RESPONSE_FACTOR,
    CONF_SETPOINT_ENTITY,
    CONF_SETPOINT_MAX,
    CONF_SETPOINT_MIN,
    CONF_SETTLING_TIME_S,
    CONF_W_PER_UNIT,
    DEFAULT_DEADBAND_W,
    DEFAULT_EWM_ALPHA,
    DEFAULT_KD,
    DEFAULT_KI,
    DEFAULT_KP,
    DEFAULT_OUTPUT_MAX_W,
    DEFAULT_RESPONSE_FACTOR,
    DEFAULT_SETPOINT_MAX,
    DEFAULT_SETPOINT_MIN,
    DEFAULT_SETTLING_TIME_S,
    DEFAULT_W_PER_UNIT,
    DOMAIN,
    INVERTER_SPEED_SETTLING,
    OUTPUT_TYPE_PERCENT,
    OUTPUT_TYPE_SWITCH,
    OUTPUT_TYPE_WATT,
    RESPONSE_FACTORS,
)

_LOGGER = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Array subentry flow
# ---------------------------------------------------------------------------

class ArraySubEntryFlow(config_entries.ConfigSubentryFlow):
    """Flow for adding or editing a PV array subentry."""

    async def async_step_user(
        self, user_input: dict[str, Any] | None = None
    ) -> SubentryFlowResult:
        """Handle adding a new PV array."""
        errors: dict[str, str] = {}

        if user_input is not None:
            data = _build_array_data(user_input)
            return self.async_create_entry(
                title=user_input.get(CONF_ARRAY_NAME) or "PV Array",
                data=data,
            )

        return self.async_show_form(
            step_id="user",
            data_schema=_array_schema(),
            errors=errors,
        )

    async def async_step_reconfigure(
        self, user_input: dict[str, Any] | None = None
    ) -> SubentryFlowResult:
        """Handle editing an existing PV array."""
        entry = self._get_entry()
        subentry = self._get_reconfigure_subentry()
        current = dict(subentry.data)

        if user_input is not None:
            data = _build_array_data(user_input)
            return self.async_update_and_abort(
                entry,
                subentry,
                title=user_input.get(CONF_ARRAY_NAME) or subentry.title,
                data=data,
            )

        return self.async_show_form(
            step_id="reconfigure",
            data_schema=_array_schema(current),
        )


def _array_schema(defaults: dict | None = None) -> vol.Schema:
    d = defaults or {}
    return vol.Schema(
        {
            vol.Required(
                CONF_ARRAY_NAME,
                description={"suggested_value": d.get(CONF_ARRAY_NAME)},
            ): str,
            vol.Required(
                CONF_SETPOINT_ENTITY,
                description={"suggested_value": d.get(CONF_SETPOINT_ENTITY)},
            ): selector({"entity": {"domain": ["number", "switch"]}}),
            vol.Required(
                CONF_OUTPUT_TYPE,
                default=d.get(CONF_OUTPUT_TYPE, OUTPUT_TYPE_PERCENT),
            ): selector(
                {
                    "select": {
                        "options": [OUTPUT_TYPE_PERCENT, OUTPUT_TYPE_WATT, OUTPUT_TYPE_SWITCH],
                        "translation_key": "output_type",
                    }
                }
            ),
            vol.Optional(
                CONF_PV_POWER_ENTITY,
                description={"suggested_value": d.get(CONF_PV_POWER_ENTITY)},
            ): selector({"entity": {"domain": "sensor", "device_class": "power"}}),
            vol.Required(
                CONF_INVERTER_SPEED,
                default=d.get(CONF_INVERTER_SPEED, "normal"),
            ): selector(
                {
                    "select": {
                        "options": ["slow", "normal", "fast"],
                        "translation_key": "inverter_speed",
                    }
                }
            ),
            vol.Optional(
                CONF_SETPOINT_MIN,
                default=d.get(CONF_SETPOINT_MIN, DEFAULT_SETPOINT_MIN),
            ): vol.All(vol.Coerce(float), vol.Range(min=0)),
            vol.Optional(
                CONF_SETPOINT_MAX,
                default=d.get(CONF_SETPOINT_MAX, DEFAULT_SETPOINT_MAX),
            ): vol.All(vol.Coerce(float), vol.Range(min=1)),
            vol.Optional(
                CONF_PRIORITY,
                default=d.get(CONF_PRIORITY, 1),
            ): vol.All(vol.Coerce(int), vol.Range(min=1, max=10)),
        }
    )


def _build_array_data(user_input: dict[str, Any]) -> dict[str, Any]:
    speed = user_input.get(CONF_INVERTER_SPEED, "normal")
    settling_time_s = INVERTER_SPEED_SETTLING.get(speed, DEFAULT_SETTLING_TIME_S)
    data: dict[str, Any] = {
        CONF_ARRAY_NAME: user_input.get(CONF_ARRAY_NAME, "PV Array"),
        CONF_SETPOINT_ENTITY: user_input[CONF_SETPOINT_ENTITY],
        CONF_OUTPUT_TYPE: user_input.get(CONF_OUTPUT_TYPE, OUTPUT_TYPE_PERCENT),
        CONF_INVERTER_SPEED: speed,
        CONF_SETTLING_TIME_S: settling_time_s,
        CONF_SETPOINT_MIN: float(user_input.get(CONF_SETPOINT_MIN, DEFAULT_SETPOINT_MIN)),
        CONF_SETPOINT_MAX: float(user_input.get(CONF_SETPOINT_MAX, DEFAULT_SETPOINT_MAX)),
        CONF_PRIORITY: int(user_input.get(CONF_PRIORITY, 1)),
        CONF_W_PER_UNIT: float(user_input.get(CONF_W_PER_UNIT, DEFAULT_W_PER_UNIT)),
        CONF_CALIBRATION_CONFIDENCE: user_input.get(CONF_CALIBRATION_CONFIDENCE, "estimated"),
        "enabled": True,
    }
    if pv := user_input.get(CONF_PV_POWER_ENTITY):
        data[CONF_PV_POWER_ENTITY] = pv
    return data


# ---------------------------------------------------------------------------
# Main config flow
# ---------------------------------------------------------------------------

class ZeroGridConfigFlow(config_entries.ConfigFlow, domain=DOMAIN):
    """Wizard-style config flow for Zero Grid Controller."""

    VERSION = 1

    def __init__(self) -> None:
        self._data: dict[str, Any] = {}
        self._arrays: list[dict[str, Any]] = []
        self._mode_guard_states: list[str] = []

    @classmethod
    @callback
    def async_get_supported_subentry_types(
        cls, config_entry: config_entries.ConfigEntry
    ) -> dict[str, type[config_entries.ConfigSubentryFlow]]:
        return {ARRAY_SUBENTRY_TYPE: ArraySubEntryFlow}

    # --- Step 1: Name + grid sensor ---
    async def async_step_user(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        await self.async_set_unique_id(DOMAIN)
        self._abort_if_unique_id_configured()

        errors: dict[str, str] = {}
        if user_input is not None:
            mtype = user_input.get(CONF_GRID_MEASUREMENT_TYPE, "net")
            if mtype == "net" and not user_input.get(CONF_GRID_SENSOR):
                errors[CONF_GRID_SENSOR] = "required"
            elif mtype == "split" and (
                not user_input.get(CONF_GRID_SENSOR_IMPORT)
                or not user_input.get(CONF_GRID_SENSOR_EXPORT)
            ):
                errors["base"] = "split_sensors_required"
            else:
                self._data.update(user_input)
                return await self.async_step_add_array()

        schema = vol.Schema(
            {
                vol.Required(CONF_NAME, default="Zero Grid Controller"): str,
                vol.Required(
                    CONF_GRID_MEASUREMENT_TYPE, default="net"
                ): selector(
                    {
                        "select": {
                            "options": ["net", "split"],
                            "translation_key": "grid_measurement_type",
                        }
                    }
                ),
                vol.Optional(CONF_GRID_SENSOR): selector(
                    {"entity": {"domain": "sensor", "device_class": "power"}}
                ),
                vol.Optional(CONF_INVERT_SIGN, default=False): bool,
                vol.Optional(CONF_GRID_SENSOR_IMPORT): selector(
                    {"entity": {"domain": "sensor", "device_class": "power"}}
                ),
                vol.Optional(CONF_GRID_SENSOR_EXPORT): selector(
                    {"entity": {"domain": "sensor", "device_class": "power"}}
                ),
            }
        )
        return self.async_show_form(
            step_id="user", data_schema=schema, errors=errors
        )

    # --- Step 2: Add first array ---
    async def async_step_add_array(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        errors: dict[str, str] = {}
        if user_input is not None:
            self._arrays.append(_build_array_data(user_input))
            return await self.async_step_another_array()

        return self.async_show_form(
            step_id="add_array",
            data_schema=_array_schema(),
            errors=errors,
        )

    # --- Step 2b: Another array? ---
    async def async_step_another_array(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        if user_input is not None:
            if user_input.get("add_another"):
                return await self.async_step_add_array()
            return await self.async_step_battery()

        return self.async_show_form(
            step_id="another_array",
            data_schema=vol.Schema(
                {vol.Required("add_another", default=False): bool}
            ),
        )

    # --- Step 3: Battery ---
    async def async_step_battery(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        if user_input is not None:
            if user_input.get("has_battery"):
                self._data.update(user_input)
                self._data.pop("has_battery", None)
            return await self.async_step_mode_guard()

        return self.async_show_form(
            step_id="battery",
            data_schema=vol.Schema(
                {
                    vol.Required("has_battery", default=False): bool,
                    vol.Optional(CONF_BATTERY_SENSOR): selector(
                        {"entity": {"domain": "sensor", "device_class": "power"}}
                    ),
                    vol.Optional(CONF_BATTERY_MAX_CHARGE_W, default=5000): vol.All(
                        vol.Coerce(float), vol.Range(min=100, max=50000)
                    ),
                    vol.Optional(CONF_BATTERY_MAX_DISCHARGE_W, default=5000): vol.All(
                        vol.Coerce(float), vol.Range(min=100, max=50000)
                    ),
                    vol.Optional(
                        CONF_BATTERY_CONTROL_ENABLED, default=False
                    ): bool,
                    vol.Optional(CONF_BATTERY_SETPOINT_ENTITY): selector(
                        {"entity": {"domain": ["number", "input_number"]}}
                    ),
                }
            ),
        )

    # --- Step 4: Mode guard ---
    async def async_step_mode_guard(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        if user_input is not None:
            if user_input.get(CONF_MODE_GUARD_ENABLED):
                self._data[CONF_MODE_GUARD_ENABLED] = True
                self._data[CONF_MODE_GUARD_ENTITY] = user_input.get(CONF_MODE_GUARD_ENTITY)
                # Read current states from that entity for mapping
                entity_id = user_input.get(CONF_MODE_GUARD_ENTITY, "")
                state = self.hass.states.get(entity_id)
                if state:
                    # For select/input_select: use options attribute
                    options = state.attributes.get("options", [state.state])
                    self._mode_guard_states = list(options)
                self._data[CONF_MODE_GUARD_MAPPING] = {}
                return await self.async_step_mode_mapping()
            else:
                self._data[CONF_MODE_GUARD_ENABLED] = False
            return await self.async_step_done()

        return self.async_show_form(
            step_id="mode_guard",
            data_schema=vol.Schema(
                {
                    vol.Required(CONF_MODE_GUARD_ENABLED, default=False): bool,
                    vol.Optional(CONF_MODE_GUARD_ENTITY): selector(
                        {"entity": {"domain": ["sensor", "input_select", "select"]}}
                    ),
                }
            ),
        )

    # --- Step 4b: Mode mapping (one pass for all states) ---
    async def async_step_mode_mapping(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        if user_input is not None:
            self._data[CONF_MODE_GUARD_MAPPING].update(user_input)
            return await self.async_step_done()

        if not self._mode_guard_states:
            return await self.async_step_done()

        schema_dict: dict = {}
        for state_val in self._mode_guard_states:
            schema_dict[
                vol.Required(
                    state_val,
                    default="active",
                    description={"suggested_value": "active"},
                )
            ] = selector(
                {
                    "select": {
                        "options": ["active", "passive", "disabled"],
                        "translation_key": "mode_guard_action",
                    }
                }
            )

        return self.async_show_form(
            step_id="mode_mapping",
            data_schema=vol.Schema(schema_dict),
        )

    # --- Step 6: Done ---
    async def async_step_done(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        # Store array configs in data
        self._data["arrays"] = self._arrays
        title = self._data.get(CONF_NAME, "Zero Grid Controller")
        return self.async_create_entry(title=title, data=self._data)

    # ------------------------------------------------------------------
    # Options flow
    # ------------------------------------------------------------------

    @staticmethod
    @callback
    def async_get_options_flow(
        config_entry: config_entries.ConfigEntry,
    ) -> "ZeroGridOptionsFlow":
        return ZeroGridOptionsFlow()


class ZeroGridOptionsFlow(config_entries.OptionsFlow):
    """Options flow: response speed, expert mode, recalibrate."""

    async def async_step_init(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        return self.async_show_menu(
            step_id="init",
            menu_options=["speed", "expert", "recalibrate"],
        )

    async def async_step_speed(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        if user_input is not None:
            speed_key = user_input.get("response_speed", "normal")
            factor = RESPONSE_FACTORS.get(speed_key, DEFAULT_RESPONSE_FACTOR)
            return self.async_create_entry(
                data={**self.config_entry.options, CONF_RESPONSE_FACTOR: factor}
            )

        current_factor = self.config_entry.options.get(CONF_RESPONSE_FACTOR, DEFAULT_RESPONSE_FACTOR)
        # Map factor back to name
        current_speed = "normal"
        for name, factor in RESPONSE_FACTORS.items():
            if abs(factor - current_factor) < 0.01:
                current_speed = name
                break

        return self.async_show_form(
            step_id="speed",
            data_schema=vol.Schema(
                {
                    vol.Required("response_speed", default=current_speed): selector(
                        {
                            "select": {
                                "options": ["cautious", "normal", "fast"],
                                "translation_key": "response_speed",
                            }
                        }
                    )
                }
            ),
        )

    async def async_step_expert(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        if user_input is not None:
            return self.async_create_entry(
                data={**self.config_entry.options, CONF_EXPERT_MODE: user_input.get(CONF_EXPERT_MODE, False)}
            )

        return self.async_show_form(
            step_id="expert",
            data_schema=vol.Schema(
                {
                    vol.Required(
                        CONF_EXPERT_MODE,
                        default=self.config_entry.options.get(CONF_EXPERT_MODE, False),
                    ): bool
                }
            ),
        )

    async def async_step_recalibrate(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        """Trigger recalibration via a service call."""
        if user_input is not None:
            # Fire service for recalibration — coordinator handles it
            await self.hass.services.async_call(
                DOMAIN,
                "recalibrate",
                {},
                blocking=False,
            )
            return self.async_create_entry(data=self.config_entry.options)

        return self.async_show_form(
            step_id="recalibrate",
            data_schema=vol.Schema({}),
            description_placeholders={
                "info": "Recalibration will start in the background. Check the learning status sensor."
            },
        )
