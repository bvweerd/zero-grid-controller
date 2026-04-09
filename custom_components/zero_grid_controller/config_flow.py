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
    ARRAY_CLIPPING_THRESHOLD,
    ARRAY_SUBENTRY_TYPE,
    BATTERY_CLIPPING_THRESHOLD,
    BATTERY_HARD_RESET_RATIO,
    BATTERY_RECOVERY_BLEND,
    BATTERY_RESPONSE_EWM_ALPHA,
    BATTERY_SUBENTRY_TYPE,
    BATTERY_UNRESPONSIVE_CYCLES,
    BATTERY_UNRESPONSIVE_THRESHOLD_W,
    BATTERY_VERIFICATION_MIN_W,
    BATTERY_WRITE_THRESHOLD_W,
    CALIB_BASELINE_SAMPLES,
    CALIB_GRID_VARIANCE_FACTOR,
    CALIB_INTER_ARRAY_SLEEP_S,
    CALIB_MIN_PV_W,
    CALIB_PV_SENSOR_MAX_WAIT_S,
    CALIB_SETTLING_CONFIRM_COUNT,
    CALIB_SETTLING_THRESHOLD_W,
    CALIB_STABLE_VARIANCE_PCT,
    CALIB_STABLE_WINDOW_S,
    CALIBRATION_CONFIDENCE_ESTIMATED,
    CLOUD_SHADOW_MIN_GAP_W,
    CLOUD_SHADOW_PV_RATIO,
    CONF_ARRAY_CLIPPING_THRESHOLD,
    CONF_ARRAY_NAME,
    CONF_BATTERY_CLIPPING_THRESHOLD,
    CONF_BATTERY_CONTROL_ENABLED,
    CONF_BATTERY_HARD_RESET_RATIO,
    CONF_BATTERY_MAX_CHARGE_W,
    CONF_BATTERY_MAX_DISCHARGE_W,
    CONF_BATTERY_RECOVERY_BLEND,
    CONF_BATTERY_RESPONSE_EWM_ALPHA,
    CONF_BATTERY_SENSOR,
    CONF_BATTERY_SETPOINT_ENTITY,
    CONF_BATTERY_UNRESPONSIVE_CYCLES,
    CONF_BATTERY_UNRESPONSIVE_THRESHOLD_W,
    CONF_BATTERY_VERIFICATION_MIN_W,
    CONF_BATTERY_WRITE_THRESHOLD_W,
    CONF_CALIB_BASELINE_SAMPLES,
    CONF_CALIB_GRID_VARIANCE_FACTOR,
    CONF_CALIB_INTER_ARRAY_SLEEP_S,
    CONF_CALIB_MAX_GRID_W,
    CONF_CALIB_MIN_PV_W,
    CONF_CALIB_PV_SENSOR_MAX_WAIT_S,
    CONF_CALIB_SETTLING_CONFIRM_COUNT,
    CONF_CALIB_SETTLING_THRESHOLD_W,
    CONF_CALIB_STABLE_VARIANCE_PCT,
    CONF_CALIB_STABLE_WINDOW_S,
    CONF_CALIBRATION_CONFIDENCE,
    CONF_CLOUD_SHADOW_MIN_GAP_W,
    CONF_CLOUD_SHADOW_PV_RATIO,
    CONF_EXPERT_MODE,
    CONF_GRID_EXPORT_SENSORS,
    CONF_GRID_IMPORT_SENSORS,
    CONF_INVERT_SIGN,
    CONF_INVERTER_SPEED,
    CONF_MODE_GUARD_ENABLED,
    CONF_MODE_GUARD_ENTITY,
    CONF_MODE_GUARD_MAPPING,
    CONF_NAME,
    CONF_OUTPUT_TYPE,
    CONF_PV_POWER_ENTITY,
    CONF_RESPONSE_FACTOR,
    CONF_SENSOR_STALE_S,
    CONF_SETPOINT_ENTITY,
    CONF_SETPOINT_MAX,
    CONF_SETPOINT_MIN,
    CONF_SETTLING_TIME_S,
    CONF_SWITCH_DEBOUNCE_S,
    CONF_SWITCH_OFF_THRESHOLD_W,
    CONF_SWITCH_ON_THRESHOLD_W,
    CONF_W_PER_UNIT,
    DEFAULT_BATTERY_MAX_CHARGE_W,
    DEFAULT_BATTERY_SETTLING_TIME_S,
    DEFAULT_CALIB_MAX_GRID_W,
    DEFAULT_RESPONSE_FACTOR,
    DEFAULT_SENSOR_STALE_S,
    DEFAULT_SETPOINT_MAX,
    DEFAULT_SETPOINT_MIN,
    DEFAULT_SETTLING_TIME_S,
    DEFAULT_SWITCH_DEBOUNCE_S,
    DEFAULT_SWITCH_OFF_THRESHOLD_W,
    DEFAULT_SWITCH_ON_THRESHOLD_W,
    DEFAULT_W_PER_UNIT,
    DOMAIN,
    INVERTER_SPEED_SETTLING,
    OUTPUT_TYPE_PERCENT,
    OUTPUT_TYPE_SWITCH,
    OUTPUT_TYPE_WATT,
    RESPONSE_FACTORS,
    SETTLING_TIME_MAX_S,
    SETTLING_TIME_MIN_S,
    SETTLING_TIME_STEP_S,
)

_LOGGER = logging.getLogger(__name__)

_POWER_SENSOR_MULTI = selector(
    {"entity": {"domain": "sensor", "device_class": "power", "multiple": True}}
)


# ---------------------------------------------------------------------------
# Battery subentry flow — two steps: basics → control settings
# ---------------------------------------------------------------------------


class BatterySubEntryFlow(config_entries.ConfigSubentryFlow):
    """Flow for adding or editing a battery subentry."""

    def __init__(self) -> None:
        super().__init__()
        self._draft: dict[str, Any] = {}
        self._reconfigure_mode: bool = False

    async def async_step_user(
        self, user_input: dict[str, Any] | None = None
    ) -> SubentryFlowResult:
        """Step 1 (add): basic battery settings."""
        if user_input is not None:
            self._draft.update(user_input)
            return await self.async_step_battery_control()

        return self.async_show_form(
            step_id="user",
            data_schema=_battery_basics_schema(self._draft),
        )

    async def async_step_reconfigure(
        self, user_input: dict[str, Any] | None = None
    ) -> SubentryFlowResult:
        """Step 1 (reconfigure): basic battery settings, pre-filled."""
        self._reconfigure_mode = True
        if not self._draft:
            subentry = self._get_reconfigure_subentry()
            self._draft = dict(subentry.data)

        if user_input is not None:
            self._draft.update(user_input)
            return await self.async_step_battery_control()

        return self.async_show_form(
            step_id="reconfigure",
            data_schema=_battery_basics_schema(self._draft),
        )

    async def async_step_battery_control(
        self, user_input: dict[str, Any] | None = None
    ) -> SubentryFlowResult:
        """Step 2: battery control settings."""
        errors: dict[str, str] = {}
        if user_input is not None:
            if user_input.get(CONF_BATTERY_CONTROL_ENABLED) and not user_input.get(
                CONF_BATTERY_SETPOINT_ENTITY
            ):
                errors[CONF_BATTERY_SETPOINT_ENTITY] = "setpoint_required"
            battery_name = str(self._draft.get(CONF_NAME) or "").strip()
            if battery_name and self._name_conflicts(
                BATTERY_SUBENTRY_TYPE, battery_name
            ):
                errors[CONF_NAME] = "duplicate_name"
            if not errors:
                self._draft.update(user_input)
                data = _build_battery_data(self._draft)
                if self._reconfigure_mode:
                    entry = self._get_entry()
                    subentry = self._get_reconfigure_subentry()
                    return self.async_update_and_abort(
                        entry,
                        subentry,
                        title=self._draft.get(CONF_NAME) or subentry.title,
                        data=data,
                    )
                return self.async_create_entry(
                    title=self._draft.get(CONF_NAME) or "Battery",
                    data=data,
                )

        return self.async_show_form(
            step_id="battery_control",
            data_schema=_battery_control_schema(self._draft),
            errors=errors,
        )

    def _name_conflicts(self, subentry_type: str, candidate: str) -> bool:
        """Return True if another subentry of the same type already uses this name."""
        entry = self._get_entry()
        candidate_folded = candidate.casefold()
        current_id = None
        if self._reconfigure_mode:
            current_id = self._get_reconfigure_subentry().subentry_id
        for subentry in entry.subentries.values():
            if (
                subentry.subentry_type != subentry_type
                or subentry.subentry_id == current_id
            ):
                continue
            existing_name = str(subentry.data.get(CONF_NAME) or subentry.title).strip()
            if existing_name and existing_name.casefold() == candidate_folded:
                return True
        return False


def _battery_basics_schema(defaults: dict[str, Any] | None = None) -> vol.Schema:
    d = defaults or {}
    return vol.Schema(
        {
            vol.Optional(
                CONF_NAME,
                description={"suggested_value": d.get(CONF_NAME)},
            ): str,
            vol.Required(
                CONF_BATTERY_SENSOR,
                description={"suggested_value": d.get(CONF_BATTERY_SENSOR)},
            ): selector({"entity": {"domain": "sensor", "device_class": "power"}}),
            vol.Required(
                CONF_BATTERY_MAX_CHARGE_W,
                default=d.get(CONF_BATTERY_MAX_CHARGE_W, DEFAULT_BATTERY_MAX_CHARGE_W),
            ): vol.All(vol.Coerce(float), vol.Range(min=100, max=50000)),
            vol.Required(
                CONF_BATTERY_MAX_DISCHARGE_W,
                default=d.get(
                    CONF_BATTERY_MAX_DISCHARGE_W, DEFAULT_BATTERY_MAX_CHARGE_W
                ),
            ): vol.All(vol.Coerce(float), vol.Range(min=100, max=50000)),
        }
    )


def _battery_control_schema(defaults: dict[str, Any] | None = None) -> vol.Schema:
    d = defaults or {}
    current_speed = _factor_to_speed(
        float(d.get(CONF_RESPONSE_FACTOR, DEFAULT_RESPONSE_FACTOR))
    )
    return vol.Schema(
        {
            vol.Optional(
                CONF_BATTERY_CONTROL_ENABLED,
                default=d.get(CONF_BATTERY_CONTROL_ENABLED, False),
            ): bool,
            vol.Optional(
                CONF_BATTERY_SETPOINT_ENTITY,
                description={"suggested_value": d.get(CONF_BATTERY_SETPOINT_ENTITY)},
            ): selector({"entity": {"domain": ["number", "input_number"]}}),
            vol.Required(
                "response_speed",
                default=current_speed,
            ): selector(
                {
                    "select": {
                        "options": ["cautious", "normal", "fast"],
                        "translation_key": "response_speed",
                    }
                }
            ),
            vol.Required(
                CONF_SETTLING_TIME_S,
                default=int(
                    d.get(CONF_SETTLING_TIME_S, DEFAULT_BATTERY_SETTLING_TIME_S)
                ),
            ): selector(
                {
                    "number": {
                        "min": SETTLING_TIME_MIN_S,
                        "max": SETTLING_TIME_MAX_S,
                        "step": SETTLING_TIME_STEP_S,
                        "unit_of_measurement": "s",
                        "mode": "box",
                    }
                }
            ),
            vol.Optional(
                CONF_BATTERY_WRITE_THRESHOLD_W,
                default=d.get(
                    CONF_BATTERY_WRITE_THRESHOLD_W, BATTERY_WRITE_THRESHOLD_W
                ),
            ): vol.All(vol.Coerce(float), vol.Range(min=0, max=5000)),
            vol.Optional(
                CONF_BATTERY_VERIFICATION_MIN_W,
                default=d.get(
                    CONF_BATTERY_VERIFICATION_MIN_W, BATTERY_VERIFICATION_MIN_W
                ),
            ): vol.All(vol.Coerce(float), vol.Range(min=0, max=5000)),
            vol.Optional(
                CONF_BATTERY_UNRESPONSIVE_THRESHOLD_W,
                default=d.get(
                    CONF_BATTERY_UNRESPONSIVE_THRESHOLD_W,
                    BATTERY_UNRESPONSIVE_THRESHOLD_W,
                ),
            ): vol.All(vol.Coerce(float), vol.Range(min=0, max=10000)),
            vol.Optional(
                CONF_BATTERY_UNRESPONSIVE_CYCLES,
                default=d.get(
                    CONF_BATTERY_UNRESPONSIVE_CYCLES, BATTERY_UNRESPONSIVE_CYCLES
                ),
            ): vol.All(vol.Coerce(int), vol.Range(min=1, max=20)),
            vol.Optional(
                CONF_BATTERY_RESPONSE_EWM_ALPHA,
                default=d.get(
                    CONF_BATTERY_RESPONSE_EWM_ALPHA, BATTERY_RESPONSE_EWM_ALPHA
                ),
            ): vol.All(vol.Coerce(float), vol.Range(min=0.01, max=1.0)),
            vol.Optional(
                CONF_BATTERY_HARD_RESET_RATIO,
                default=d.get(CONF_BATTERY_HARD_RESET_RATIO, BATTERY_HARD_RESET_RATIO),
            ): vol.All(vol.Coerce(float), vol.Range(min=0.0, max=1.0)),
            vol.Optional(
                CONF_BATTERY_RECOVERY_BLEND,
                default=d.get(CONF_BATTERY_RECOVERY_BLEND, BATTERY_RECOVERY_BLEND),
            ): vol.All(vol.Coerce(float), vol.Range(min=0.0, max=1.0)),
            vol.Optional(
                CONF_BATTERY_CLIPPING_THRESHOLD,
                default=d.get(
                    CONF_BATTERY_CLIPPING_THRESHOLD, BATTERY_CLIPPING_THRESHOLD
                ),
            ): vol.All(vol.Coerce(float), vol.Range(min=0.5, max=1.0)),
        }
    )


def _build_battery_data(draft: dict[str, Any]) -> dict[str, Any]:
    speed = draft.get("response_speed", "normal")
    data: dict[str, Any] = {
        CONF_BATTERY_SENSOR: draft[CONF_BATTERY_SENSOR],
        CONF_BATTERY_MAX_CHARGE_W: float(
            draft.get(CONF_BATTERY_MAX_CHARGE_W, DEFAULT_BATTERY_MAX_CHARGE_W)
        ),
        CONF_BATTERY_MAX_DISCHARGE_W: float(
            draft.get(CONF_BATTERY_MAX_DISCHARGE_W, DEFAULT_BATTERY_MAX_CHARGE_W)
        ),
        CONF_BATTERY_CONTROL_ENABLED: bool(
            draft.get(CONF_BATTERY_CONTROL_ENABLED, False)
        ),
        CONF_RESPONSE_FACTOR: RESPONSE_FACTORS.get(speed, DEFAULT_RESPONSE_FACTOR),
        CONF_SETTLING_TIME_S: int(
            draft.get(CONF_SETTLING_TIME_S, DEFAULT_BATTERY_SETTLING_TIME_S)
        ),
        CONF_BATTERY_WRITE_THRESHOLD_W: float(
            draft.get(CONF_BATTERY_WRITE_THRESHOLD_W, BATTERY_WRITE_THRESHOLD_W)
        ),
        CONF_BATTERY_VERIFICATION_MIN_W: float(
            draft.get(CONF_BATTERY_VERIFICATION_MIN_W, BATTERY_VERIFICATION_MIN_W)
        ),
        CONF_BATTERY_UNRESPONSIVE_THRESHOLD_W: float(
            draft.get(
                CONF_BATTERY_UNRESPONSIVE_THRESHOLD_W,
                BATTERY_UNRESPONSIVE_THRESHOLD_W,
            )
        ),
        CONF_BATTERY_UNRESPONSIVE_CYCLES: int(
            draft.get(CONF_BATTERY_UNRESPONSIVE_CYCLES, BATTERY_UNRESPONSIVE_CYCLES)
        ),
        CONF_BATTERY_RESPONSE_EWM_ALPHA: float(
            draft.get(CONF_BATTERY_RESPONSE_EWM_ALPHA, BATTERY_RESPONSE_EWM_ALPHA)
        ),
        CONF_BATTERY_HARD_RESET_RATIO: float(
            draft.get(CONF_BATTERY_HARD_RESET_RATIO, BATTERY_HARD_RESET_RATIO)
        ),
        CONF_BATTERY_RECOVERY_BLEND: float(
            draft.get(CONF_BATTERY_RECOVERY_BLEND, BATTERY_RECOVERY_BLEND)
        ),
        CONF_BATTERY_CLIPPING_THRESHOLD: float(
            draft.get(CONF_BATTERY_CLIPPING_THRESHOLD, BATTERY_CLIPPING_THRESHOLD)
        ),
    }
    if name := draft.get(CONF_NAME, "").strip():
        data[CONF_NAME] = name
    if setpoint := draft.get(CONF_BATTERY_SETPOINT_ENTITY):
        data[CONF_BATTERY_SETPOINT_ENTITY] = setpoint
    return data


# ---------------------------------------------------------------------------
# Array subentry flow — add: basics → setpoint_range or switch_params
#                       reconfigure: reconfigure → setpoint_range or switch_params
#                                    → recalibrate_option
# ---------------------------------------------------------------------------


class ArraySubEntryFlow(config_entries.ConfigSubentryFlow):
    """Flow for adding or editing a PV array subentry."""

    def __init__(self) -> None:
        super().__init__()
        self._draft: dict[str, Any] = {}
        self._reconfigure_mode: bool = False

    # --- Step 1 (add): basics ---

    async def async_step_user(
        self, user_input: dict[str, Any] | None = None
    ) -> SubentryFlowResult:
        """Step 1 (add): basic array settings."""
        if user_input is not None:
            errors = _validate_array_basics(user_input)
            if not errors and self._name_conflicts(user_input[CONF_ARRAY_NAME]):
                errors[CONF_ARRAY_NAME] = "duplicate_name"
            if errors:
                return self.async_show_form(
                    step_id="user",
                    data_schema=_array_basics_schema(user_input),
                    errors=errors,
                )
            self._draft.update(user_input)
            output_type = user_input.get(CONF_OUTPUT_TYPE, OUTPUT_TYPE_PERCENT)
            if output_type == OUTPUT_TYPE_SWITCH:
                return await self.async_step_switch_params()
            return await self.async_step_setpoint_range()

        return self.async_show_form(
            step_id="user",
            data_schema=_array_basics_schema(self._draft),
        )

    # --- Step 1 (reconfigure): basics pre-filled ---

    async def async_step_reconfigure(
        self, user_input: dict[str, Any] | None = None
    ) -> SubentryFlowResult:
        """Step 1 (reconfigure): basic array settings, pre-filled."""
        self._reconfigure_mode = True
        if not self._draft:
            subentry = self._get_reconfigure_subentry()
            self._draft = dict(subentry.data)

        if user_input is not None:
            errors = _validate_array_basics(user_input)
            if not errors and self._name_conflicts(user_input[CONF_ARRAY_NAME]):
                errors[CONF_ARRAY_NAME] = "duplicate_name"
            if errors:
                return self.async_show_form(
                    step_id="reconfigure",
                    data_schema=_array_basics_schema(user_input),
                    errors=errors,
                )
            self._draft.update(user_input)
            output_type = user_input.get(CONF_OUTPUT_TYPE, OUTPUT_TYPE_PERCENT)
            if output_type == OUTPUT_TYPE_SWITCH:
                return await self.async_step_switch_params()
            return await self.async_step_setpoint_range()

        return self.async_show_form(
            step_id="reconfigure",
            data_schema=_array_basics_schema(self._draft),
        )

    # --- Step 2a: setpoint range (percent / watt) ---

    async def async_step_setpoint_range(
        self, user_input: dict[str, Any] | None = None
    ) -> SubentryFlowResult:
        """Step 2 for percent/watt arrays: setpoint range and inverter timing."""
        if user_input is not None:
            errors = _validate_array_setpoint_range(user_input)
            if errors:
                return self.async_show_form(
                    step_id="setpoint_range",
                    data_schema=_array_setpoint_range_schema(
                        {**self._draft, **user_input}
                    ),
                    errors=errors,
                )
            self._draft.update(user_input)
            if self._reconfigure_mode:
                return await self.async_step_recalibrate_option()
            return self._finalize_create()

        return self.async_show_form(
            step_id="setpoint_range",
            data_schema=_array_setpoint_range_schema(self._draft),
        )

    # --- Step 2b: switch parameters ---

    async def async_step_switch_params(
        self, user_input: dict[str, Any] | None = None
    ) -> SubentryFlowResult:
        """Step 2 for switch arrays: on/off thresholds and debounce."""
        if user_input is not None:
            errors = _validate_array_switch_params(user_input)
            if errors:
                return self.async_show_form(
                    step_id="switch_params",
                    data_schema=_array_switch_params_schema(
                        {**self._draft, **user_input}
                    ),
                    errors=errors,
                )
            self._draft.update(user_input)
            if self._reconfigure_mode:
                return await self.async_step_recalibrate_option()
            return self._finalize_create()

        return self.async_show_form(
            step_id="switch_params",
            data_schema=_array_switch_params_schema(self._draft),
        )

    # --- Step 3 (reconfigure only): optional recalibration ---

    async def async_step_recalibrate_option(
        self, user_input: dict[str, Any] | None = None
    ) -> SubentryFlowResult:
        """Step 3 (reconfigure only): optionally trigger recalibration after saving."""
        if user_input is not None:
            recalibrate = user_input.get("_recalibrate", False)
            entry = self._get_entry()
            subentry = self._get_reconfigure_subentry()
            data = _build_array_data(self._draft)
            result = self.async_update_and_abort(
                entry,
                subentry,
                title=self._draft.get(CONF_ARRAY_NAME) or subentry.title,
                data=data,
            )
            if recalibrate:
                self.hass.async_create_task(
                    self.hass.services.async_call(
                        DOMAIN,
                        "recalibrate",
                        {"array_name": data.get(CONF_ARRAY_NAME)},
                        blocking=False,
                    )
                )
            return result

        return self.async_show_form(
            step_id="recalibrate_option",
            data_schema=vol.Schema({vol.Optional("_recalibrate", default=False): bool}),
        )

    def _finalize_create(self) -> SubentryFlowResult:
        data = _build_array_data(self._draft)
        return self.async_create_entry(
            title=self._draft.get(CONF_ARRAY_NAME) or "PV Array",
            data=data,
        )

    def _name_conflicts(self, candidate: str) -> bool:
        """Return True if another array already uses this name."""
        entry = self._get_entry()
        candidate_folded = candidate.strip().casefold()
        current_id = None
        if self._reconfigure_mode:
            current_id = self._get_reconfigure_subentry().subentry_id
        for subentry in entry.subentries.values():
            if (
                subentry.subentry_type != ARRAY_SUBENTRY_TYPE
                or subentry.subentry_id == current_id
            ):
                continue
            existing_name = str(
                subentry.data.get(CONF_ARRAY_NAME, subentry.subentry_id)
            ).strip()
            if existing_name.casefold() == candidate_folded:
                return True
        return False


# ---------------------------------------------------------------------------
# Array schema helpers
# ---------------------------------------------------------------------------


def _array_basics_schema(defaults: dict[str, Any] | None = None) -> vol.Schema:
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
                        "options": [
                            OUTPUT_TYPE_PERCENT,
                            OUTPUT_TYPE_WATT,
                            OUTPUT_TYPE_SWITCH,
                        ],
                        "translation_key": "output_type",
                    }
                }
            ),
            vol.Optional(
                CONF_PV_POWER_ENTITY,
                description={"suggested_value": d.get(CONF_PV_POWER_ENTITY)},
            ): selector({"entity": {"domain": "sensor", "device_class": "power"}}),
        }
    )


def _array_setpoint_range_schema(defaults: dict[str, Any] | None = None) -> vol.Schema:
    d = defaults or {}
    current_speed = _factor_to_speed(
        float(d.get(CONF_RESPONSE_FACTOR, DEFAULT_RESPONSE_FACTOR))
    )
    return vol.Schema(
        {
            vol.Optional(
                CONF_SETPOINT_MIN,
                default=d.get(CONF_SETPOINT_MIN, DEFAULT_SETPOINT_MIN),
            ): vol.All(vol.Coerce(float), vol.Range(min=0)),
            vol.Optional(
                CONF_SETPOINT_MAX,
                default=d.get(CONF_SETPOINT_MAX, DEFAULT_SETPOINT_MAX),
            ): vol.All(vol.Coerce(float), vol.Range(min=1)),
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
            vol.Required(
                "response_speed",
                default=current_speed,
            ): selector(
                {
                    "select": {
                        "options": ["cautious", "normal", "fast"],
                        "translation_key": "response_speed",
                    }
                }
            ),
            vol.Optional(
                CONF_CLOUD_SHADOW_PV_RATIO,
                default=d.get(CONF_CLOUD_SHADOW_PV_RATIO, CLOUD_SHADOW_PV_RATIO),
            ): vol.All(vol.Coerce(float), vol.Range(min=0.0, max=1.0)),
            vol.Optional(
                CONF_CLOUD_SHADOW_MIN_GAP_W,
                default=d.get(CONF_CLOUD_SHADOW_MIN_GAP_W, CLOUD_SHADOW_MIN_GAP_W),
            ): vol.All(vol.Coerce(float), vol.Range(min=0.0, max=5000)),
        }
    )


def _array_switch_params_schema(defaults: dict[str, Any] | None = None) -> vol.Schema:
    d = defaults or {}
    current_speed = _factor_to_speed(
        float(d.get(CONF_RESPONSE_FACTOR, DEFAULT_RESPONSE_FACTOR))
    )
    return vol.Schema(
        {
            vol.Optional(
                CONF_SWITCH_ON_THRESHOLD_W,
                default=d.get(
                    CONF_SWITCH_ON_THRESHOLD_W, DEFAULT_SWITCH_ON_THRESHOLD_W
                ),
            ): vol.All(vol.Coerce(float), vol.Range(min=0)),
            vol.Optional(
                CONF_SWITCH_OFF_THRESHOLD_W,
                default=d.get(
                    CONF_SWITCH_OFF_THRESHOLD_W, DEFAULT_SWITCH_OFF_THRESHOLD_W
                ),
            ): vol.All(vol.Coerce(float), vol.Range(min=0)),
            vol.Optional(
                CONF_SWITCH_DEBOUNCE_S,
                default=d.get(CONF_SWITCH_DEBOUNCE_S, DEFAULT_SWITCH_DEBOUNCE_S),
            ): vol.All(vol.Coerce(int), vol.Range(min=0, max=3600)),
            vol.Required(
                "response_speed",
                default=current_speed,
            ): selector(
                {
                    "select": {
                        "options": ["cautious", "normal", "fast"],
                        "translation_key": "response_speed",
                    }
                }
            ),
        }
    )


def _validate_array_basics(user_input: dict[str, Any]) -> dict[str, str]:
    """Validate compatibility between output type and selected setpoint entity."""
    errors: dict[str, str] = {}
    entity_id = str(user_input.get(CONF_SETPOINT_ENTITY, ""))
    entity_domain = entity_id.split(".", 1)[0] if "." in entity_id else ""
    output_type = user_input.get(CONF_OUTPUT_TYPE, OUTPUT_TYPE_PERCENT)

    if (
        output_type == OUTPUT_TYPE_SWITCH
        and entity_domain != "switch"
        or output_type in (OUTPUT_TYPE_PERCENT, OUTPUT_TYPE_WATT)
        and entity_domain != "number"
    ):
        errors[CONF_SETPOINT_ENTITY] = "setpoint_entity_mismatch"

    return errors


def _validate_array_setpoint_range(user_input: dict[str, Any]) -> dict[str, str]:
    """Validate cross-field constraints for numeric array outputs."""
    errors: dict[str, str] = {}
    setpoint_min = float(user_input.get(CONF_SETPOINT_MIN, DEFAULT_SETPOINT_MIN))
    setpoint_max = float(user_input.get(CONF_SETPOINT_MAX, DEFAULT_SETPOINT_MAX))
    if setpoint_min > setpoint_max:
        errors[CONF_SETPOINT_MAX] = "max_less_than_min"
    return errors


def _validate_array_switch_params(user_input: dict[str, Any]) -> dict[str, str]:
    """Validate cross-field constraints for switch arrays."""
    errors: dict[str, str] = {}
    switch_on_threshold = float(
        user_input.get(CONF_SWITCH_ON_THRESHOLD_W, DEFAULT_SWITCH_ON_THRESHOLD_W)
    )
    switch_off_threshold = float(
        user_input.get(CONF_SWITCH_OFF_THRESHOLD_W, DEFAULT_SWITCH_OFF_THRESHOLD_W)
    )
    if switch_on_threshold <= switch_off_threshold:
        errors[CONF_SWITCH_ON_THRESHOLD_W] = "must_exceed_off_threshold"
    return errors


def _extract_mode_guard_states(hass: HomeAssistant, entity_id: str) -> list[str]:
    """Return usable mode-guard states for the selected entity."""
    state = hass.states.get(entity_id)
    if state is None:
        return []

    options = state.attributes.get("options")
    if isinstance(options, list):
        usable_states = [str(option) for option in options if str(option).strip()]
        if usable_states:
            return usable_states

    state_value = str(state.state).strip()
    if state_value and state_value not in {"unknown", "unavailable"}:
        return [state_value]

    return []


def _build_array_data(draft: dict[str, Any]) -> dict[str, Any]:
    speed = draft.get(CONF_INVERTER_SPEED, "normal")
    response_speed = draft.get("response_speed", "normal")
    settling_time_s = INVERTER_SPEED_SETTLING.get(speed, DEFAULT_SETTLING_TIME_S)
    data: dict[str, Any] = {
        CONF_ARRAY_NAME: draft.get(CONF_ARRAY_NAME, "PV Array"),
        CONF_SETPOINT_ENTITY: draft.get(CONF_SETPOINT_ENTITY, ""),
        CONF_OUTPUT_TYPE: draft.get(CONF_OUTPUT_TYPE, OUTPUT_TYPE_PERCENT),
        CONF_INVERTER_SPEED: speed,
        CONF_SETTLING_TIME_S: settling_time_s,
        CONF_SETPOINT_MIN: float(draft.get(CONF_SETPOINT_MIN, DEFAULT_SETPOINT_MIN)),
        CONF_SETPOINT_MAX: float(draft.get(CONF_SETPOINT_MAX, DEFAULT_SETPOINT_MAX)),
        CONF_W_PER_UNIT: float(draft.get(CONF_W_PER_UNIT, DEFAULT_W_PER_UNIT)),
        CONF_CALIBRATION_CONFIDENCE: draft.get(
            CONF_CALIBRATION_CONFIDENCE, CALIBRATION_CONFIDENCE_ESTIMATED
        ),
        CONF_RESPONSE_FACTOR: RESPONSE_FACTORS.get(
            response_speed, DEFAULT_RESPONSE_FACTOR
        ),
        CONF_CLOUD_SHADOW_PV_RATIO: float(
            draft.get(CONF_CLOUD_SHADOW_PV_RATIO, CLOUD_SHADOW_PV_RATIO)
        ),
        CONF_CLOUD_SHADOW_MIN_GAP_W: float(
            draft.get(CONF_CLOUD_SHADOW_MIN_GAP_W, CLOUD_SHADOW_MIN_GAP_W)
        ),
        CONF_SWITCH_ON_THRESHOLD_W: float(
            draft.get(CONF_SWITCH_ON_THRESHOLD_W, DEFAULT_SWITCH_ON_THRESHOLD_W)
        ),
        CONF_SWITCH_OFF_THRESHOLD_W: float(
            draft.get(CONF_SWITCH_OFF_THRESHOLD_W, DEFAULT_SWITCH_OFF_THRESHOLD_W)
        ),
        CONF_SWITCH_DEBOUNCE_S: int(
            draft.get(CONF_SWITCH_DEBOUNCE_S, DEFAULT_SWITCH_DEBOUNCE_S)
        ),
        "enabled": True,
    }
    if pv := draft.get(CONF_PV_POWER_ENTITY):
        data[CONF_PV_POWER_ENTITY] = pv
    return data


def _factor_to_speed(factor: float) -> str:
    """Map a response_factor value back to a speed name."""
    for name, f in RESPONSE_FACTORS.items():
        if abs(f - factor) < 0.01:
            return name
    return "normal"


# ---------------------------------------------------------------------------
# Main config flow
# ---------------------------------------------------------------------------


class ZeroGridConfigFlow(config_entries.ConfigFlow, domain=DOMAIN):
    """Wizard-style config flow for Zero Grid Controller."""

    VERSION = 1

    def __init__(self) -> None:
        self._data: dict[str, Any] = {}
        self._mode_guard_states: list[str] = []

    @classmethod
    @callback
    def async_get_supported_subentry_types(
        cls, config_entry: config_entries.ConfigEntry
    ) -> dict[str, type[config_entries.ConfigSubentryFlow]]:
        return {
            ARRAY_SUBENTRY_TYPE: ArraySubEntryFlow,
            BATTERY_SUBENTRY_TYPE: BatterySubEntryFlow,
        }

    # --- Step 1: Name + grid sensors ---
    async def async_step_user(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        await self.async_set_unique_id(DOMAIN)
        self._abort_if_unique_id_configured()

        errors: dict[str, str] = {}
        if user_input is not None:
            if not user_input.get(CONF_GRID_IMPORT_SENSORS):
                errors[CONF_GRID_IMPORT_SENSORS] = "required"
            else:
                self._data.update(user_input)
                return await self.async_step_mode_guard()

        schema = vol.Schema(
            {
                vol.Required(CONF_NAME, default="Zero Grid Controller"): str,
                vol.Required(CONF_GRID_IMPORT_SENSORS): _POWER_SENSOR_MULTI,
                vol.Optional(CONF_GRID_EXPORT_SENSORS): _POWER_SENSOR_MULTI,
                vol.Optional(CONF_INVERT_SIGN, default=False): bool,
            }
        )
        return self.async_show_form(step_id="user", data_schema=schema, errors=errors)

    # --- Step 2: Mode guard ---
    async def async_step_mode_guard(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        errors: dict[str, str] = {}
        if user_input is not None:
            if user_input.get(CONF_MODE_GUARD_ENABLED):
                entity_id = str(user_input.get(CONF_MODE_GUARD_ENTITY, "")).strip()
                if not entity_id:
                    errors[CONF_MODE_GUARD_ENTITY] = "required"
                else:
                    mode_guard_states = _extract_mode_guard_states(self.hass, entity_id)
                    if not mode_guard_states:
                        errors[CONF_MODE_GUARD_ENTITY] = "invalid_mode_guard_entity"
                    else:
                        self._data[CONF_MODE_GUARD_ENABLED] = True
                        self._data[CONF_MODE_GUARD_ENTITY] = entity_id
                        self._mode_guard_states = mode_guard_states
                        self._data[CONF_MODE_GUARD_MAPPING] = {}
                        return await self.async_step_mode_mapping()
            else:
                self._data[CONF_MODE_GUARD_ENABLED] = False
                self._mode_guard_states = []
                self._data.pop(CONF_MODE_GUARD_ENTITY, None)
                self._data.pop(CONF_MODE_GUARD_MAPPING, None)
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
            errors=errors,
        )

    # --- Step 3: Mode mapping ---
    async def async_step_mode_mapping(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        if user_input is not None:
            errors = {
                state_val: "required"
                for state_val in self._mode_guard_states
                if state_val not in user_input
            }
            if errors:
                error_schema_dict: dict[Any, Any] = {}
                for state_val in self._mode_guard_states:
                    error_schema_dict[
                        vol.Required(
                            state_val,
                            default=user_input.get(state_val, "active"),
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
                    data_schema=vol.Schema(error_schema_dict),
                    errors=errors,
                )
            self._data[CONF_MODE_GUARD_MAPPING].update(user_input)
            return await self.async_step_done()

        if not self._mode_guard_states:
            return await self.async_step_done()

        schema_dict: dict[Any, Any] = {}
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

    # --- Step 4: Done ---
    async def async_step_done(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        title = self._data.get(CONF_NAME, "Zero Grid Controller")
        return self.async_create_entry(title=title, data=self._data)

    # ------------------------------------------------------------------
    # Options flow
    # ------------------------------------------------------------------

    @staticmethod
    @callback
    def async_get_options_flow(
        config_entry: config_entries.ConfigEntry,
    ) -> ZeroGridOptionsFlow:
        return ZeroGridOptionsFlow()


class ZeroGridOptionsFlow(config_entries.OptionsFlow):
    """Options flow: expert mode and site-wide tuning parameters."""

    async def async_step_init(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        opts = self.config_entry.options
        if user_input is not None:
            return self.async_create_entry(
                data={
                    **opts,
                    CONF_EXPERT_MODE: user_input.get(CONF_EXPERT_MODE, False),
                    CONF_SENSOR_STALE_S: int(
                        user_input.get(CONF_SENSOR_STALE_S, DEFAULT_SENSOR_STALE_S)
                    ),
                    CONF_CALIB_MAX_GRID_W: float(
                        user_input.get(CONF_CALIB_MAX_GRID_W, DEFAULT_CALIB_MAX_GRID_W)
                    ),
                    CONF_ARRAY_CLIPPING_THRESHOLD: float(
                        user_input.get(
                            CONF_ARRAY_CLIPPING_THRESHOLD, ARRAY_CLIPPING_THRESHOLD
                        )
                    ),
                    CONF_CALIB_STABLE_VARIANCE_PCT: float(
                        user_input.get(
                            CONF_CALIB_STABLE_VARIANCE_PCT,
                            CALIB_STABLE_VARIANCE_PCT,
                        )
                    ),
                    CONF_CALIB_STABLE_WINDOW_S: int(
                        user_input.get(
                            CONF_CALIB_STABLE_WINDOW_S, CALIB_STABLE_WINDOW_S
                        )
                    ),
                    CONF_CALIB_BASELINE_SAMPLES: int(
                        user_input.get(
                            CONF_CALIB_BASELINE_SAMPLES, CALIB_BASELINE_SAMPLES
                        )
                    ),
                    CONF_CALIB_SETTLING_CONFIRM_COUNT: int(
                        user_input.get(
                            CONF_CALIB_SETTLING_CONFIRM_COUNT,
                            CALIB_SETTLING_CONFIRM_COUNT,
                        )
                    ),
                    CONF_CALIB_SETTLING_THRESHOLD_W: float(
                        user_input.get(
                            CONF_CALIB_SETTLING_THRESHOLD_W,
                            CALIB_SETTLING_THRESHOLD_W,
                        )
                    ),
                    CONF_CALIB_MIN_PV_W: float(
                        user_input.get(CONF_CALIB_MIN_PV_W, CALIB_MIN_PV_W)
                    ),
                    CONF_CALIB_GRID_VARIANCE_FACTOR: float(
                        user_input.get(
                            CONF_CALIB_GRID_VARIANCE_FACTOR,
                            CALIB_GRID_VARIANCE_FACTOR,
                        )
                    ),
                    CONF_CALIB_INTER_ARRAY_SLEEP_S: float(
                        user_input.get(
                            CONF_CALIB_INTER_ARRAY_SLEEP_S, CALIB_INTER_ARRAY_SLEEP_S
                        )
                    ),
                    CONF_CALIB_PV_SENSOR_MAX_WAIT_S: float(
                        user_input.get(
                            CONF_CALIB_PV_SENSOR_MAX_WAIT_S,
                            CALIB_PV_SENSOR_MAX_WAIT_S,
                        )
                    ),
                }
            )

        return self.async_show_form(
            step_id="init",
            data_schema=vol.Schema(
                {
                    vol.Required(
                        CONF_EXPERT_MODE,
                        default=opts.get(CONF_EXPERT_MODE, False),
                    ): bool,
                    vol.Required(
                        CONF_SENSOR_STALE_S,
                        default=int(
                            opts.get(CONF_SENSOR_STALE_S, DEFAULT_SENSOR_STALE_S)
                        ),
                    ): selector(
                        {
                            "number": {
                                "min": 5,
                                "max": 300,
                                "step": 5,
                                "unit_of_measurement": "s",
                                "mode": "box",
                            }
                        }
                    ),
                    vol.Required(
                        CONF_CALIB_MAX_GRID_W,
                        default=float(
                            opts.get(CONF_CALIB_MAX_GRID_W, DEFAULT_CALIB_MAX_GRID_W)
                        ),
                    ): selector(
                        {
                            "number": {
                                "min": 500,
                                "max": 50000,
                                "step": 100,
                                "unit_of_measurement": "W",
                                "mode": "box",
                            }
                        }
                    ),
                    vol.Required(
                        CONF_ARRAY_CLIPPING_THRESHOLD,
                        default=float(
                            opts.get(
                                CONF_ARRAY_CLIPPING_THRESHOLD,
                                ARRAY_CLIPPING_THRESHOLD,
                            )
                        ),
                    ): selector(
                        {
                            "number": {
                                "min": 0.5,
                                "max": 1.0,
                                "step": 0.01,
                                "mode": "box",
                            }
                        }
                    ),
                    vol.Required(
                        CONF_CALIB_STABLE_VARIANCE_PCT,
                        default=float(
                            opts.get(
                                CONF_CALIB_STABLE_VARIANCE_PCT,
                                CALIB_STABLE_VARIANCE_PCT,
                            )
                        ),
                    ): selector(
                        {
                            "number": {
                                "min": 0.5,
                                "max": 100.0,
                                "step": 0.5,
                                "unit_of_measurement": "%",
                                "mode": "box",
                            }
                        }
                    ),
                    vol.Required(
                        CONF_CALIB_STABLE_WINDOW_S,
                        default=int(
                            opts.get(CONF_CALIB_STABLE_WINDOW_S, CALIB_STABLE_WINDOW_S)
                        ),
                    ): selector(
                        {
                            "number": {
                                "min": 5,
                                "max": 300,
                                "step": 1,
                                "unit_of_measurement": "s",
                                "mode": "box",
                            }
                        }
                    ),
                    vol.Required(
                        CONF_CALIB_BASELINE_SAMPLES,
                        default=int(
                            opts.get(
                                CONF_CALIB_BASELINE_SAMPLES,
                                CALIB_BASELINE_SAMPLES,
                            )
                        ),
                    ): selector(
                        {
                            "number": {
                                "min": 1,
                                "max": 120,
                                "step": 1,
                                "mode": "box",
                            }
                        }
                    ),
                    vol.Required(
                        CONF_CALIB_SETTLING_CONFIRM_COUNT,
                        default=int(
                            opts.get(
                                CONF_CALIB_SETTLING_CONFIRM_COUNT,
                                CALIB_SETTLING_CONFIRM_COUNT,
                            )
                        ),
                    ): selector(
                        {
                            "number": {
                                "min": 1,
                                "max": 30,
                                "step": 1,
                                "mode": "box",
                            }
                        }
                    ),
                    vol.Required(
                        CONF_CALIB_SETTLING_THRESHOLD_W,
                        default=float(
                            opts.get(
                                CONF_CALIB_SETTLING_THRESHOLD_W,
                                CALIB_SETTLING_THRESHOLD_W,
                            )
                        ),
                    ): selector(
                        {
                            "number": {
                                "min": 0,
                                "max": 1000,
                                "step": 1,
                                "unit_of_measurement": "W",
                                "mode": "box",
                            }
                        }
                    ),
                    vol.Required(
                        CONF_CALIB_MIN_PV_W,
                        default=float(opts.get(CONF_CALIB_MIN_PV_W, CALIB_MIN_PV_W)),
                    ): selector(
                        {
                            "number": {
                                "min": 0,
                                "max": 10000,
                                "step": 10,
                                "unit_of_measurement": "W",
                                "mode": "box",
                            }
                        }
                    ),
                    vol.Required(
                        CONF_CALIB_GRID_VARIANCE_FACTOR,
                        default=float(
                            opts.get(
                                CONF_CALIB_GRID_VARIANCE_FACTOR,
                                CALIB_GRID_VARIANCE_FACTOR,
                            )
                        ),
                    ): selector(
                        {
                            "number": {
                                "min": 1.0,
                                "max": 20.0,
                                "step": 0.5,
                                "mode": "box",
                            }
                        }
                    ),
                    vol.Required(
                        CONF_CALIB_INTER_ARRAY_SLEEP_S,
                        default=float(
                            opts.get(
                                CONF_CALIB_INTER_ARRAY_SLEEP_S,
                                CALIB_INTER_ARRAY_SLEEP_S,
                            )
                        ),
                    ): selector(
                        {
                            "number": {
                                "min": 0,
                                "max": 300,
                                "step": 1,
                                "unit_of_measurement": "s",
                                "mode": "box",
                            }
                        }
                    ),
                    vol.Required(
                        CONF_CALIB_PV_SENSOR_MAX_WAIT_S,
                        default=float(
                            opts.get(
                                CONF_CALIB_PV_SENSOR_MAX_WAIT_S,
                                CALIB_PV_SENSOR_MAX_WAIT_S,
                            )
                        ),
                    ): selector(
                        {
                            "number": {
                                "min": 1,
                                "max": 120,
                                "step": 1,
                                "unit_of_measurement": "s",
                                "mode": "box",
                            }
                        }
                    ),
                }
            ),
        )
