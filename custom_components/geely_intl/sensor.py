"""Sensors for Geely (international).

Reads from coordinator.data, which is the `data` block of
GET /remote-control/vehicle/status/{VIN}. Live keys are nested under
`vehicleStatus.{basicVehicleStatus|additionalVehicleStatus.{...}}`.
The server sends every numeric value as a string - we coerce here.
"""
from __future__ import annotations

from typing import Any

from homeassistant.components.sensor import SensorDeviceClass, SensorEntity
from homeassistant.config_entries import ConfigEntry
from homeassistant.const import (
    PERCENTAGE,
    UnitOfElectricPotential,
    UnitOfLength,
    UnitOfSpeed,
    UnitOfTemperature,
)
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity import DeviceInfo, EntityCategory
from homeassistant.helpers.entity_platform import AddEntitiesCallback
from homeassistant.helpers.update_coordinator import CoordinatorEntity

from .const import DOMAIN

# Shorthand for nested status branches
_BASIC  = ("vehicleStatus", "basicVehicleStatus")
_ADD    = ("vehicleStatus", "additionalVehicleStatus")
_MAINT  = (*_ADD, "maintenanceStatus")
_EV     = (*_ADD, "electricVehicleStatus")
_CLIM   = (*_ADD, "climateStatus")
_SAFE   = (*_ADD, "drivingSafetyStatus")
_RUN    = (*_ADD, "runningStatus")

# Value mappers for sensors that should display a readable label instead
# of the raw numeric/string code from the API.
_CHARGER_CONNECTION_MAP = {
    "0": "Disconnected", 0: "Disconnected",
    "1": "Plugged in",   1: "Plugged in",
    "2": "Plugged in",   2: "Plugged in",
    "3": "Charging",     3: "Charging",
}

_PARK_BRAKE_MAP = {
    "0": "Released", 0: "Released",
    "1": "Engaged",  1: "Engaged",
}

_ENGINE_STATE_MAP = {
    "engine_off":     "Off",
    "engine_running": "Running",
    "running":        "Running",
    "off":            "Off",
    "1":              "Running", 1: "Running",
    "0":              "Off",     0: "Off",
}

# Icon overrides per sensor key (entries with explicit device_class get
# auto-icons from HA, but a few sensors look better with a custom icon).
_SENSOR_ICONS: dict[str, str] = {
    "time_to_full_min":  "mdi:battery-charging",
    "charger_connected": "mdi:ev-plug-type2",
    "12v_battery":       "mdi:car-battery",
    "12v_voltage":       "mdi:car-battery",
    "avg_consumption":   "mdi:lightning-bolt",
    "trip_meter":        "mdi:map-marker-distance",
    "avg_speed":         "mdi:speedometer-medium",
    "engine_state":      "mdi:engine",
    "park_brake":        "mdi:car-brake-parking",
    "tire_pressure_fl":  "mdi:car-tire-alert",
    "tire_pressure_fr":  "mdi:car-tire-alert",
    "tire_pressure_rl":  "mdi:car-tire-alert",
    "tire_pressure_rr":  "mdi:car-tire-alert",
    "days_to_service":     "mdi:calendar-clock",
    "distance_to_service": "mdi:road-variant",
}

# (key, friendly_name, dotted-path, unit, device_class, value_type, value_map?)
SENSOR_SPECS: tuple[tuple, ...] = (
    ("battery",             "Battery",              (*_EV,    "chargeLevel"),                          PERCENTAGE,                       SensorDeviceClass.BATTERY,     "float", None),
    ("range",               "Electric Range",       (*_EV,    "distanceToEmptyOnBatteryOnly"),         UnitOfLength.KILOMETERS,          SensorDeviceClass.DISTANCE,    "int",   None),
    ("total_mileage",       "Total Mileage",        (*_MAINT, "odometer"),                             UnitOfLength.KILOMETERS,          SensorDeviceClass.DISTANCE,    "float", None),
    ("interior_temp",       "Interior Temperature", (*_CLIM,  "interiorTemp"),                         UnitOfTemperature.CELSIUS,        SensorDeviceClass.TEMPERATURE, "float", None),
    ("exterior_temp",       "Exterior Temperature", (*_CLIM,  "exteriorTemp"),                         UnitOfTemperature.CELSIUS,        SensorDeviceClass.TEMPERATURE, "float", None),
    ("speed",               "Speed",                (*_BASIC, "speed"),                                UnitOfSpeed.KILOMETERS_PER_HOUR,  SensorDeviceClass.SPEED,       "float", None),
    ("engine_state",        "Engine State",         (*_BASIC, "engineStatus"),                         None,                             None,                          "map",   _ENGINE_STATE_MAP),
    ("park_brake",          "Park Brake",           (*_SAFE,  "electricParkBrakeStatus"),              None,                             None,                          "map",   _PARK_BRAKE_MAP),
    ("charger_connected",   "Charger Connection",   (*_EV,    "statusOfChargerConnection"),            None,                             None,                          "map",   _CHARGER_CONNECTION_MAP),
    ("time_to_full_min",    "Time To Full Charge",  (*_EV,    "timeToFullyCharged"),                   "min",                            None,                          "int",   None),
    ("12v_battery",         "12V Battery",          (*_MAINT, "mainBatteryStatus", "chargeLevel"),     PERCENTAGE,                       None,                          "float", None),
    ("12v_voltage",         "12V Voltage",          (*_MAINT, "mainBatteryStatus", "voltage"),         UnitOfElectricPotential.VOLT,     SensorDeviceClass.VOLTAGE,     "float", None),
    ("avg_consumption",     "Average Consumption",  (*_EV,    "averPowerConsumption"),                 "kWh/100km",                      None,                          "float", None),
    ("trip_meter",          "Trip Meter",           (*_RUN,   "tripMeter1"),                           UnitOfLength.KILOMETERS,          SensorDeviceClass.DISTANCE,    "float", None),
    ("avg_speed",           "Average Speed",        (*_RUN,   "avgSpeed"),                             UnitOfSpeed.KILOMETERS_PER_HOUR,  SensorDeviceClass.SPEED,       "float", None),
    ("tire_pressure_fl",    "Tire Pressure FL",     (*_MAINT, "tyreStatusDriver"),                     "kPa",                            SensorDeviceClass.PRESSURE,    "float", None),
    ("tire_pressure_fr",    "Tire Pressure FR",     (*_MAINT, "tyreStatusPassenger"),                  "kPa",                            SensorDeviceClass.PRESSURE,    "float", None),
    ("tire_pressure_rl",    "Tire Pressure RL",     (*_MAINT, "tyreStatusDriverRear"),                 "kPa",                            SensorDeviceClass.PRESSURE,    "float", None),
    ("tire_pressure_rr",    "Tire Pressure RR",     (*_MAINT, "tyreStatusPassengerRear"),              "kPa",                            SensorDeviceClass.PRESSURE,    "float", None),
    ("days_to_service",     "Days To Service",      (*_MAINT, "daysToService"),                        "d",                              None,                          "int",   None),
    ("distance_to_service", "Distance To Service",  (*_MAINT, "distanceToService"),                    UnitOfLength.KILOMETERS,          SensorDeviceClass.DISTANCE,    "int",   None),
)

# Sensors marked diagnostic appear in HA's collapsed "Diagnostic" section
# on the device page rather than the main entity list.
_DIAGNOSTIC_KEYS: set[str] = {
    "park_brake",
    "12v_battery", "12v_voltage",
    "avg_consumption", "trip_meter", "avg_speed",
    "tire_pressure_fl", "tire_pressure_fr",
    "tire_pressure_rl", "tire_pressure_rr",
    "days_to_service", "distance_to_service",
}


def _walk(d: Any, path: tuple[str, ...]) -> Any:
    cur = d
    for key in path:
        if not isinstance(cur, dict):
            return None
        cur = cur.get(key)
        if cur is None:
            return None
    return cur


def _coerce(v: Any, kind: str, value_map: dict | None = None) -> Any:
    if v is None or v == "":
        return None
    try:
        if kind == "int":
            return int(float(v))
        if kind == "float":
            return float(v)
        if kind == "map" and value_map is not None:
            return value_map.get(v, value_map.get(str(v), v))
    except (TypeError, ValueError):
        return None
    return v


async def async_setup_entry(hass: HomeAssistant, entry: ConfigEntry, add_entities: AddEntitiesCallback) -> None:
    bundle = hass.data[DOMAIN][entry.entry_id]
    coordinator = bundle["coordinator"]
    vin = bundle["vin"]
    device_name = bundle.get("device_name") or f"Geely ({vin})"
    add_entities(GeelySensor(coordinator, vin, device_name, *spec) for spec in SENSOR_SPECS)


class GeelySensor(CoordinatorEntity, SensorEntity):
    _attr_has_entity_name = True

    def __init__(self, coordinator, vin: str, device_name: str,
                 key: str, friendly_name: str, path: tuple[str, ...],
                 unit: str | None, device_class: SensorDeviceClass | None,
                 kind: str, value_map: dict | None = None) -> None:
        super().__init__(coordinator)
        self._key = key
        self._path = path
        self._kind = kind
        self._value_map = value_map
        self._attr_unique_id = f"geely_{vin}_{key}"
        self._attr_name = friendly_name
        if unit is not None:
            self._attr_native_unit_of_measurement = unit
        if device_class is not None:
            self._attr_device_class = device_class
        icon = _SENSOR_ICONS.get(key)
        if icon:
            self._attr_icon = icon
        if key in _DIAGNOSTIC_KEYS:
            self._attr_entity_category = EntityCategory.DIAGNOSTIC
        self._attr_device_info = DeviceInfo(
            identifiers={(DOMAIN, vin)},
            manufacturer="Geely",
            name=device_name,
        )

    @property
    def native_value(self) -> Any:
        v = _walk(self.coordinator.data or {}, self._path)
        return _coerce(v, self._kind, self._value_map)
