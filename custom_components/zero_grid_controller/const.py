"""Constants for Zero Grid Controller."""

from homeassistant.const import Platform

DOMAIN = "zero_grid_controller"

ARRAY_SUBENTRY_TYPE = "array"
BATTERY_SUBENTRY_TYPE = "battery"

# Config keys — main entry
CONF_NAME = "name"
CONF_GRID_IMPORT_SENSORS = "grid_import_sensors"
CONF_GRID_EXPORT_SENSORS = "grid_export_sensors"
CONF_CONTROLLER_ENABLED = "controller_enabled"
CONF_EWM_ALPHA = "ewm_alpha"
CONF_DEADBAND_W = "deadband_w"
CONF_KP = "kp"
CONF_KI = "ki"
CONF_KD = "kd"
CONF_OUTPUT_MAX_W = "output_max_w"
CONF_AGGRESSIVENESS = "aggressiveness"

# Array subentry config keys
CONF_ARRAY_NAME = "array_name"
CONF_SETPOINT_ENTITY = "setpoint_entity"
CONF_OUTPUT_TYPE = "output_type"
CONF_SETPOINT_MIN = "setpoint_min"
CONF_SETPOINT_MAX = "setpoint_max"
CONF_SETTLING_TIME_S = "settling_time_s"
CONF_W_PER_UNIT = "w_per_unit"
CONF_CALIBRATION_CONFIDENCE = "calibration_confidence"
CONF_POWER_SENSOR_ENTITY = "power_sensor_entity"
CONF_DERIVED_MAX_POWER_W = "derived_max_power_w"
CONF_SETTLING_DOWN_S = "settling_down_s"
CONF_SETTLING_UP_S = "settling_up_s"

# Switch array config keys
CONF_SWITCH_ON_THRESHOLD_W = "switch_on_threshold_w"
CONF_SWITCH_OFF_THRESHOLD_W = "switch_off_threshold_w"
CONF_SWITCH_DEBOUNCE_S = "switch_debounce_s"

# Battery subentry config keys
CONF_BATTERY_SENSOR = "battery_sensor"
CONF_BATTERY_MAX_CHARGE_W = "battery_max_charge_w"
CONF_BATTERY_MAX_DISCHARGE_W = "battery_max_discharge_w"
CONF_BATTERY_SETPOINT_ENTITY = "battery_setpoint_entity"

# Defaults
DEFAULT_EWM_ALPHA = 0.3
DEFAULT_DEADBAND_W = 20.0
DEFAULT_KP = 0.5
DEFAULT_KI = 0.02
DEFAULT_KD = 0.0
DEFAULT_OUTPUT_MAX_W = 10000.0
DEFAULT_W_PER_UNIT = 10.0
DEFAULT_SETTLING_TIME_S = 15
DEFAULT_SETPOINT_MIN = 0.0
DEFAULT_SETPOINT_MAX = 100.0
DEFAULT_SWITCH_ON_THRESHOLD_W = 100.0
DEFAULT_SWITCH_OFF_THRESHOLD_W = 50.0
DEFAULT_SWITCH_DEBOUNCE_S = 30
DEFAULT_BATTERY_MAX_CHARGE_W = 5000.0
DEFAULT_BATTERY_MAX_DISCHARGE_W = 5000.0
DEFAULT_AGGRESSIVENESS = "normal"

# Calibration constants
CALIB_MAX_GRID_W = 3000.0  # Abort if |grid_w| exceeds this
CALIB_MAX_TIME_S = 90  # Maximum seconds per array
CALIB_BASELINE_SAMPLES = 10  # Samples for baseline measurement
CALIB_SETTLING_CONFIRM_COUNT = 3  # Consecutive stable samples to confirm settling
CALIB_SETTLING_THRESHOLD_W = 5.0  # Max grid deviation to be considered settled
CALIB_STEP_RATIO = 0.10  # Step size as fraction of setpoint range
CALIB_STEP_MIN = 2  # Minimum step size in setpoint units
CALIB_STEP_MAX = 20  # Maximum step size in setpoint units
CALIB_MIN_W_PER_UNIT = 0.5  # Below this → calibration failed
CALIB_INTER_ARRAY_SLEEP_S = 15  # Pause between arrays
CALIB_SETTLING_MIN_S = 3
CALIB_SETTLING_MAX_S = 60
CALIBRATION_CONFIDENCE_ESTIMATED = "estimated"
CALIBRATION_CONFIDENCE_MEASURED = "measured"

# Control loop timing
CONTROL_DT_MIN = 0.1
CONTROL_DT_MAX = 10.0
CONTROL_INTERVAL_S = 5

# Aggressiveness → PID factor mapping
# Kp = aggressiveness_factor  (direct — distribution headroom handles system-size scaling)
AGGRESSIVENESS_FACTORS = {
    "cautious": 0.5,
    "normal": 1.0,
    "fast": 1.5,
}
# Ki = Kp × KI_RATIO  (integrator time constant = kp/ki ≈ 100 s for normal)
AGGRESSIVENESS_KI_RATIO = 0.01

# Output types
OUTPUT_TYPE_PERCENT = "percent"
OUTPUT_TYPE_WATT = "watt"
OUTPUT_TYPE_SWITCH = "switch"

# Status values
STATUS_ACTIVE = "active"
STATUS_DISABLED = "disabled"
STATUS_DEADBAND = "deadband"

# Number entity UI constraints
EWM_ALPHA_MIN = 0.05
EWM_ALPHA_MAX = 1.0
EWM_ALPHA_STEP = 0.05
DEADBAND_MIN_W = 0.0
DEADBAND_MAX_W = 500.0
DEADBAND_STEP_W = 1.0

# Platforms
PLATFORMS = [
    Platform.BINARY_SENSOR,
    Platform.SENSOR,
    Platform.NUMBER,
    Platform.BUTTON,
    Platform.SWITCH,
]

# Services
SERVICE_RESET_PID = "reset_pid"
SERVICE_RECALIBRATE = "recalibrate"
