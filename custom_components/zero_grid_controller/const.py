"""Constants for Zero Grid Controller."""

DOMAIN = "zero_grid_controller"

ARRAY_SUBENTRY_TYPE = "array"

# Config keys — main entry
CONF_NAME = "name"
CONF_GRID_SENSOR = "grid_sensor"
CONF_GRID_SENSOR_IMPORT = "grid_sensor_import"
CONF_GRID_SENSOR_EXPORT = "grid_sensor_export"
CONF_GRID_MEASUREMENT_TYPE = "grid_measurement_type"  # "net" | "split"
CONF_INVERT_SIGN = "invert_sign"

# Battery config
CONF_BATTERY_SENSOR = "battery_sensor"
CONF_BATTERY_MAX_CHARGE_W = "battery_max_charge_w"
CONF_BATTERY_MAX_DISCHARGE_W = "battery_max_discharge_w"
CONF_BATTERY_CONTROL_ENABLED = "battery_control_enabled"
CONF_BATTERY_SETPOINT_ENTITY = "battery_setpoint_entity"

# Mode guard config
CONF_MODE_GUARD_ENABLED = "mode_guard_enabled"
CONF_MODE_GUARD_ENTITY = "mode_guard_entity"
CONF_MODE_GUARD_MAPPING = "mode_guard_mapping"  # dict: mode_value -> "active"|"passive"|"disabled"

# PID tuning
CONF_KP = "kp"
CONF_KI = "ki"
CONF_KD = "kd"
CONF_EWM_ALPHA = "ewm_alpha"
CONF_DEADBAND_W = "deadband_w"
CONF_OUTPUT_MAX_W = "output_max_w"
CONF_RESPONSE_FACTOR = "response_factor"
CONF_EXPERT_MODE = "expert_mode"

# Estimator persistence
CONF_ESTIMATOR_STATE = "estimator_state"

# Array subentry config keys
CONF_ARRAY_NAME = "array_name"
CONF_SETPOINT_ENTITY = "setpoint_entity"
CONF_OUTPUT_TYPE = "output_type"
CONF_PV_POWER_ENTITY = "pv_power_entity"
CONF_SETPOINT_MIN = "setpoint_min"
CONF_SETPOINT_MAX = "setpoint_max"
CONF_SETTLING_TIME_S = "settling_time_s"
CONF_W_PER_UNIT = "w_per_unit"
CONF_CALIBRATION_CONFIDENCE = "calibration_confidence"
CONF_PRIORITY = "priority"
CONF_INVERTER_SPEED = "inverter_speed"  # "slow" | "normal" | "fast"

# Switch config
CONF_SWITCH_ON_THRESHOLD_W = "switch_on_threshold_w"
CONF_SWITCH_OFF_THRESHOLD_W = "switch_off_threshold_w"
CONF_SWITCH_DEBOUNCE_S = "switch_debounce_s"

# Defaults
DEFAULT_KP = 0.5
DEFAULT_KI = 0.02
DEFAULT_KD = 0.01
DEFAULT_EWM_ALPHA = 0.3
DEFAULT_DEADBAND_W = 20.0
DEFAULT_OUTPUT_MAX_W = 10000.0
DEFAULT_RESPONSE_FACTOR = 1.0
DEFAULT_W_PER_UNIT = 10.0
DEFAULT_SETTLING_TIME_S = 15
DEFAULT_SETPOINT_MIN = 0.0
DEFAULT_SETPOINT_MAX = 100.0
DEFAULT_SWITCH_ON_THRESHOLD_W = 100.0
DEFAULT_SWITCH_OFF_THRESHOLD_W = 50.0
DEFAULT_SWITCH_DEBOUNCE_S = 30

# Inverter speed → settling time mapping (seconds)
INVERTER_SPEED_SETTLING = {
    "slow": 30,
    "normal": 15,
    "fast": 5,
}

# Output types
OUTPUT_TYPE_PERCENT = "percent"
OUTPUT_TYPE_WATT = "watt"
OUTPUT_TYPE_SWITCH = "switch"

# Controller modes
MODE_ACTIVE = "active"
MODE_PASSIVE = "passive"
MODE_DISABLED = "disabled"

# Status values
STATUS_ACTIVE = "active"
STATUS_PASSIVE = "passive"
STATUS_DISABLED = "disabled"
STATUS_DEADBAND = "deadband"
STATUS_CLOUD_SHADOW = "cloud_shadow"
STATUS_SATURATION = "saturation"

# Response factor presets
RESPONSE_FACTORS = {
    "cautious": 0.5,
    "normal": 1.0,
    "fast": 2.0,
}

# Platforms
from homeassistant.const import Platform

PLATFORMS = [
    Platform.SENSOR,
    Platform.NUMBER,
    Platform.SWITCH,
]

# Services
SERVICE_RESET_PID = "reset_pid"
SERVICE_RECALIBRATE = "recalibrate"
SERVICE_SET_RESPONSE_SPEED = "set_response_speed"
SERVICE_OVERRIDE_SETPOINT = "override_setpoint"
