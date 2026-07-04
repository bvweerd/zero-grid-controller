"""Constants for Zero Grid Controller."""

from enum import StrEnum

from homeassistant.const import Platform

DOMAIN = "zero_grid_controller"

ARRAY_SUBENTRY_TYPE = "array"
BATTERY_SUBENTRY_TYPE = "battery"
LOAD_SUBENTRY_TYPE = "load"

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

# Load subentry config keys
CONF_LOAD_NAME = "load_name"
CONF_LOAD_TYPE = "load_type"
CONF_LOAD_POWER_W = "load_power_w"
CONF_LOAD_ABSOLUTE_MIN_W = "load_absolute_min_w"
CONF_LOAD_PRIORITY = "load_priority"

# Load types
LOAD_TYPE_NUMERIC = "numeric"
LOAD_TYPE_SWITCH = "switch"

# Battery subentry config keys
CONF_BATTERY_SENSOR = "battery_sensor"
CONF_BATTERY_MAX_CHARGE_W = "battery_max_charge_w"
CONF_BATTERY_MAX_DISCHARGE_W = "battery_max_discharge_w"
CONF_BATTERY_SETPOINT_ENTITY = "battery_setpoint_entity"
CONF_BATTERY_SCHEDULE_SENSOR = "battery_schedule_sensor"
CONF_BATTERY_SOC_SENSOR = "battery_soc_sensor"
CONF_BATTERY_MIN_SOC = "battery_min_soc"
CONF_BATTERY_MAX_SOC = "battery_max_soc"

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
DEFAULT_BATTERY_MIN_SOC = 10.0
DEFAULT_BATTERY_MAX_SOC = 95.0

# Battery control (reactive batteries)
# Trust window for commanded-but-not-yet-measured battery response.  Within
# this window the commanded delta is fed forward into the residual so the PID
# does not double-correct; after it expires the measured power is the truth,
# so a non-responsive battery cannot block curtailment forever.
BATTERY_PENDING_S = 30.0
# Skip battery setpoint writes smaller than this (avoids service-call spam).
BATTERY_WRITE_THRESHOLD_W = 1.0

# PV recovery: when reactive batteries have spare charge capacity and numeric
# arrays are curtailed, bias the PID by this virtual import per cycle so
# curtailed production is gradually recovered into the battery.
PV_RECOVERY_STEP_W = 200.0
# Recovery requires the batteries to actually track their commands; a larger
# command-vs-measured mismatch blocks recovery (prevents a limit cycle
# against a non-responsive battery).
PV_RECOVERY_TRACKING_TOLERANCE_W = 100.0
DEFAULT_AGGRESSIVENESS = "normal"
DEFAULT_LOAD_PRIORITY = 50
DEFAULT_LOAD_DEBOUNCE_S = 30

# Switch loads: once on, only turn off when grid import exceeds this fraction
# of the load's fixed power.  After turn-on the residual sits at ~0 W, which
# is exactly the old off-threshold — any noise beyond the deadband flapped
# the load off again.
SWITCH_LOAD_OFF_FRACTION = 0.1

# Failsafe behaviour when grid sensors are unavailable / controller disabled
CONF_FAILSAFE_MODE = "failsafe_mode"
FAILSAFE_MODE_MAXIMIZE = "maximize"  # PV to max (self-consumption setups)
FAILSAFE_MODE_CURTAIL = "curtail"  # PV to min (zero-export requirements)
DEFAULT_FAILSAFE_MODE = FAILSAFE_MODE_MAXIMIZE
# Consecutive unavailable grid reads tolerated (state held) before failsafe.
GRID_UNAVAILABLE_TOLERANCE_CYCLES = 3

# Calibration constants
CALIB_MAX_GRID_W = 3000.0  # Abort if |grid_w| exceeds this during calibration
CALIB_MAX_TIME_S = 90  # Maximum seconds per array
CALIB_SETTLING_CONFIRM_COUNT = 3  # Consecutive stable samples to confirm settling
CALIB_SETTLING_THRESHOLD_W = 5.0  # Max grid deviation to be considered settled
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
class ControllerStatus(StrEnum):
    """Controller cycle status."""

    ACTIVE = "active"
    DISABLED = "disabled"
    DEADBAND = "deadband"
    IDLE_IMPORT_OK = "idle_import_ok"  # zero_export mode: grid positive → idle
    IDLE_EXPORT_OK = "idle_export_ok"  # zero_import mode: grid negative → idle
    MAXIMIZING_EXPORT = "maximizing_export"  # maximize_export mode active
    MAXIMIZING_IMPORT = "maximizing_import"  # maximize_import mode active


# Aliases for backward compatibility — existing imports are unchanged
STATUS_ACTIVE = ControllerStatus.ACTIVE
STATUS_DISABLED = ControllerStatus.DISABLED
STATUS_DEADBAND = ControllerStatus.DEADBAND
STATUS_IDLE_IMPORT_OK = ControllerStatus.IDLE_IMPORT_OK
STATUS_IDLE_EXPORT_OK = ControllerStatus.IDLE_EXPORT_OK
STATUS_MAXIMIZING_EXPORT = ControllerStatus.MAXIMIZING_EXPORT
STATUS_MAXIMIZING_IMPORT = ControllerStatus.MAXIMIZING_IMPORT


class ControllerMode(StrEnum):
    """Operating mode for the control loop."""

    ZERO_GRID = "zero_grid"  # no import or export (default)
    ZERO_IMPORT = "zero_import"  # allow export, prevent import
    ZERO_EXPORT = "zero_export"  # allow import, prevent export
    MAXIMIZE_EXPORT = "maximize_export"  # PV max, loads off — sell as much as possible
    MAXIMIZE_IMPORT = (
        "maximize_import"  # PV off, loads max — consume as much as possible
    )


CONF_CONTROL_MODE = "control_mode"
DEFAULT_CONTROL_MODE = ControllerMode.ZERO_GRID

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
    Platform.SELECT,
    Platform.SENSOR,
    Platform.NUMBER,
    Platform.BUTTON,
    Platform.SWITCH,
]

# Services
SERVICE_RESET_PID = "reset_pid"
SERVICE_RECALIBRATE = "recalibrate"
