"""Constants for Zero Grid Controller."""

from homeassistant.const import Platform

DOMAIN = "zero_grid_controller"

ARRAY_SUBENTRY_TYPE = "array"
BATTERY_SUBENTRY_TYPE = "battery"

# Config keys — main entry
CONF_NAME = "name"
CONF_GRID_IMPORT_SENSORS = "grid_import_sensors"  # list[str]: sensors summed for import
CONF_GRID_EXPORT_SENSORS = "grid_export_sensors"  # list[str]: sensors summed for export
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
CONF_MODE_GUARD_MAPPING = (
    "mode_guard_mapping"  # dict: mode_value -> "active"|"passive"|"disabled"
)

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
CONF_INVERTER_SPEED = "inverter_speed"  # "slow" | "normal" | "fast"

# Switch config
CONF_SWITCH_ON_THRESHOLD_W = "switch_on_threshold_w"
CONF_SWITCH_OFF_THRESHOLD_W = "switch_off_threshold_w"
CONF_SWITCH_DEBOUNCE_S = "switch_debounce_s"

# Defaults
DEFAULT_KP = 0.5
DEFAULT_KI = 0.02
DEFAULT_KD = 0.0
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
DEFAULT_BATTERY_MAX_CHARGE_W = 5000.0
CALIBRATION_CONFIDENCE_ESTIMATED = "estimated"

# Calibration constants
CALIB_MAX_GRID_W = 3000.0  # Safety limit: abort if |grid_w| exceeds this
CALIB_MAX_TIME_S = 180  # Maximum seconds per array (3 minutes)
CALIB_STABLE_VARIANCE_PCT = 5.0  # Max PV variance % to consider conditions stable
CALIB_STABLE_WINDOW_S = 30  # Stability observation window (seconds)
CALIB_BASELINE_SAMPLES = 10  # Samples for baseline grid measurement
CALIB_SETTLING_CONFIRM_COUNT = 5  # Consecutive samples required to confirm settling
CALIB_SETTLING_THRESHOLD_W = 5.0  # Max deviation from avg to be considered settled
CALIB_MIN_PV_W = 100  # Minimum average PV watts to start calibration
CALIB_GRID_VARIANCE_FACTOR = 3  # Grid variance tolerance = PV threshold × this
CALIB_INTER_ARRAY_SLEEP_S = 30  # Pause between arrays for grid to stabilise
CALIB_STEP_RATIO = 0.10  # Step size as fraction of usable setpoint range
CALIB_STEP_MIN = 2  # Minimum step size in setpoint units
CALIB_STEP_MAX = 20  # Maximum step size in setpoint units
CALIB_MIN_W_PER_UNIT = 0.5  # Minimum measurable response; below → failed
CALIB_SETTLING_MIN_S = 3  # Clamp floor for measured settling time
CALIB_SETTLING_MAX_S = 60  # Clamp ceiling for measured settling time
CALIB_DEFAULT_FAIL_SETTLING_S = 30  # Settling time used in failed-calibration result

# Control loop timing
CONTROL_DT_MIN = 0.1  # Minimum dt (seconds) to protect against division by zero
CONTROL_DT_MAX = 10.0  # Maximum dt (seconds) to avoid large integral jumps after pause
CONTROL_INTERVAL_S = 5  # Default coordinator update interval

# Clipping detection thresholds
ARRAY_CLIPPING_THRESHOLD = 0.90  # PV power ≥ 90 % of setpoint → clipping active
BATTERY_CLIPPING_THRESHOLD = 0.95  # Battery power ≥ 95 % of max → clipping active

# RLS estimator tuning
RLS_EPSILON = 1e-9  # Numerical guard: skip update if denominator < this
RLS_MIN_UPDATES = 20  # Minimum updates before estimator is considered reliable
RLS_MIN_GAIN_ABS = 0.1  # Minimum |K| for reliability (screens out near-zero gains)
RLS_MAX_UNCERTAINTY = 10.0  # Maximum P (covariance) for reliability
RLS_KP_MIN = 0.001  # Lower bound for suggested Kp
RLS_KP_MAX = 10.0  # Upper bound for suggested Kp
RLS_KP_BLEND_FACTOR = 0.2  # Weight of new Kp in 80/20 conservative blend

# Number entity UI scale constraints
KP_MIN = 0.001
KP_MAX = 10.0
KP_STEP = 0.001
KI_MIN = 0.0
KI_MAX = 1.0
KI_STEP = 0.001
KD_MIN = 0.0
KD_MAX = 1.0
KD_STEP = 0.001
EWM_ALPHA_MIN = 0.05
EWM_ALPHA_MAX = 1.0
EWM_ALPHA_STEP = 0.05
DEADBAND_MIN_W = 0.0
DEADBAND_MAX_W = 500.0
DEADBAND_STEP_W = 1.0
OUTPUT_MAX_MIN_W = 100.0
OUTPUT_MAX_MAX_W = 50000.0
OUTPUT_MAX_STEP_W = 100.0
SETTLING_TIME_MIN_S = 2
SETTLING_TIME_MAX_S = 120
SETTLING_TIME_STEP_S = 1
W_PER_UNIT_MIN = 0.1
W_PER_UNIT_MAX = 1000.0
W_PER_UNIT_STEP = 0.1

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
PLATFORMS = [
    Platform.SENSOR,
    Platform.NUMBER,
    Platform.SWITCH,
    Platform.BUTTON,
]

# Services
SERVICE_RESET_PID = "reset_pid"
SERVICE_RECALIBRATE = "recalibrate"
SERVICE_OVERRIDE_SETPOINT = "override_setpoint"
