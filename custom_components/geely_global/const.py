"""Constants for the Geely (international) integration.

ServiceId catalog and parameter shapes are AVD-Frida-verified
(see docs/AVD_CAPTURE_GUIDE.md). They reflect the actual Android Geely
Global app's network calls captured live via OkHttp interception.
"""

DOMAIN = "geely_global"

# App-level credentials - same across all users on the EU region.
APP_ID     = "GEELYE245"
APP_SECRET = "48d6fff3ea19447bbf6f3ed76a608ff9"

# Vehicle / client metadata sent in headers during control commands.
CLIENT_ID      = "OOGLE0000APPE64ARM64264T31485278"
VEHICLE_SERIES = "E245-J1"
VEHICLE_MODEL  = "E245-J1"

# Polling cadence
SCAN_INTERVAL_SECONDS = 90
JWT_REFRESH_SECONDS = 6500   # JWT lasts 7200s - refresh a bit early

# ConfigEntry data keys
CONF_EMAIL              = "email"
CONF_COUNTRY_CODE       = "country_code"
CONF_CIDPSSO_TOKEN      = "cidpsso_token"
CONF_USER_ID            = "user_id"
CONF_VIN                = "vin"
CONF_DEVICE_ID          = "device_id"
CONF_CERT_PATH          = "cert_path"
CONF_KEY_PATH           = "key_path"
CONF_DEVICE_IDFA        = "device_idfa"
CONF_DEVICE_IDFV        = "device_idfv"
CONF_VEHICLE_NICKNAME   = "vehicle_nickname"
CONF_VEHICLE_SERIES     = "vehicle_series"
CONF_VEHICLE_MODEL_CODE = "vehicle_model_code"
CONF_VEHICLE_COLOR      = "vehicle_color"
CONF_VEHICLE_POWER_TYPE = "vehicle_power_type"

DEFAULT_COUNTRY_CODE = "IL"

SERIES_TO_FRIENDLY_NAME: dict[str, str] = {
    "E245-J1": "EX5",
}

# === serviceId catalog ===
# Most controls fire `PUT /remote-control/vehicle/telematics/{VIN}` with
# `{serviceId, command, serviceParameters: [{key, value}, ...]}`.
# Rapid warm/cool is the exception - see SERVICE_RAPID_*_PATH below.

# --- Lock (verified live) ---
SERVICE_LOCK         = "RDL_2"
SERVICE_UNLOCK       = "RDU_2"
SERVICE_LOCK_PARAMS  = [{"key": "door", "value": "all"}]

# --- Find car (verified live) ---
SERVICE_FIND_CAR        = "RHL"
SERVICE_FIND_CAR_PARAMS = [{"key": "rhl", "value": "horn-light-flash"}]

# --- Tailgate UNLOCK (AVD-verified 2026-05-01) ---
# Uses RDU_2 (door-unlock service) with target=trunk - NOT a separate RTB
# service. Auto-relocks ~45s if not physically opened.
SERVICE_TAILGATE        = "RDU_2"
SERVICE_TAILGATE_PARAMS  = [{"key": "target", "value": "trunk"}]

# --- Climate (AVD-verified 2026-05-01) ---
# Master serviceId for AC, defrost, seat heat, seat vent on the Android app.
SERVICE_CLIMATE = "RCE_2"
# Param key + values inside RCE_2:
RCE_KEY_CONDITIONER = "rce.conditioner"   # value 1=AC, 2=defrost
RCE_VAL_AC          = "1"
RCE_VAL_DEFROST     = "2"
RCE_KEY_TEMP        = "rce.temp"          # AC target temp, "15.5".."28.5" str
RCE_KEY_LEVEL       = "rce.level"         # level "1"|"2"|"3" (or "0" with stop)
RCE_KEY_HEAT        = "rce.heat"          # value = seat name (front-left/right)
RCE_KEY_VENT        = "rce.ventilation"   # value = seat name OR "cabin" (RCC_2)
SEAT_FRONT_LEFT     = "front-left"
SEAT_FRONT_RIGHT    = "front-right"
SEAT_REAR_LEFT      = "rear-left"
SEAT_REAR_RIGHT     = "rear-right"
RCE_DURATION_SECONDS = 90    # default duration for seat features (matches AVD)
RCE_AC_DURATION_SEC = 180    # default duration for AC (matches AVD)

# --- G-clean (AVD-verified) - ventilation pulse, ~6s/burst ---
# Reuses serviceId RCC_2 (different from RCE_2). Param value "cabin".
SERVICE_GCLEAN          = "RCC_2"
SERVICE_GCLEAN_PARAMS   = [{"key": "rcc.ventilation", "value": "cabin"}]
SERVICE_GCLEAN_DURATION = 6

# --- Engine pre-conditioning (verified earlier; not used by Android app) ---
SERVICE_CLIMATE_ENGINE = "RES"

# --- Parking Comfort (UNVERIFIED on this trim) ---
SERVICE_PARKING_COMFORT = "RSM"

# --- charge-server family (AVD-verified) ---
# All these go to POST /charge-server/ecarx_charge_set/{VIN}.
# bizType picks the feature; the body shape varies per bizType.
CHARGE_SERVER_PATH = "/charge-server/ecarx_charge_set"
BIZ_TYPE_PARKING_COMFORT  = "4"   # GET to read schedule, POST to set
BIZ_TYPE_SCHED_CHARGING   = "6"   # rbc fields (rbcStartTime, rbcEndTime, rbcTarget, rbc, rbcModel, pin)
BIZ_TYPE_RAPID            = "7"   # ac+heat[]/ventilation[]+temp+vlt
RAPID_DEFAULT_DURATION    = "180"
RAPID_DEFAULT_VLT_POS     = "12"
RAPID_DEFAULT_VLT_DUR     = "60"
# Legacy aliases
SERVICE_RAPID_PATH      = CHARGE_SERVER_PATH
SERVICE_RAPID_BIZ_TYPE  = BIZ_TYPE_RAPID

# --- Steering wheel heat (read field exists, command unverified on this trim) ---
SERVICE_STEERING_HEAT_KEY = "steerWhlHeatingSts"  # status field

# --- Window / sunshade / sunroof / ventilate (verified) ---
SERVICE_WINDOW = "RWS_2"
SERVICE_WINDOW_VENT_PARAMS = [{"key": "target", "value": "ventilate"}]

# --- Charging (AVD-verified 2026-05-01) ---
SERVICE_CHARGING            = "RCS"
SERVICE_CHARGING_START_PARAMS = [
    {"key": "operation",   "value": "1"},
    {"key": "rcs.restart", "value": "1"},
]
SERVICE_CHARGING_STOP_PARAMS = [
    {"key": "operation",     "value": "0"},
    {"key": "rcs.terminate", "value": "1"},
]
SERVICE_CHARGING_START_CMD = "start"
SERVICE_CHARGING_STOP_CMD  = "stop"
# Legacy aliases (kept for old switch.py import path)
SERVICE_CHARGING_SCHED_CMD = "RCS_SETTING"   # deprecated - use bizType=6
SERVICE_SCHEDULED_CHARGING = SERVICE_CHARGING

# --- Capability discovery endpoint ---
# GET /geelyTCAccess/tcservices/capability/{VIN}?pageSize=2000&pageIndex=1&vehicleType=0
# Returns the per-vehicle feature catalog. Used to build dynamic entities.
CAPABILITY_PATH = "/geelyTCAccess/tcservices/capability"

# === Climate entity defaults (overridden by capability if available) ===
CLIMATE_MIN_TEMP_C  = 15.5
CLIMATE_MAX_TEMP_C  = 28.5
CLIMATE_TEMP_STEP_C = 0.5
CLIMATE_SEAT_LEVELS = ["Off", "Low", "Medium", "High"]   # index = level

# === Climate preset names ===
# HA uses the preset_mode value as-is in the dropdown UI, so user-facing
# labels go directly here. PRESET_NONE stays lowercase because HA's
# frontend has built-in "None" rendering for the "no preset" state.
PRESET_NONE          = "none"
PRESET_RAPID_WARMING = "Rapid Warming"
PRESET_RAPID_COOLING = "Rapid Cooling"
