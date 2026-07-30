"""Every numeric sensor must declare a state_class, and no textual one may.

The failure this guards against is silent in both directions: a sensor without
state_class is simply absent from HA's long-term statistics (no error, no log,
the history just stops at the recorder's ~10-day purge), and a state class on a
sensor that reports a label is meaningless - and, the day those sensors gain
device_class ENUM, makes HA drop the entity with a validation error at startup.

The allowed combinations below are transcribed from DEVICE_CLASS_STATE_CLASSES
in homeassistant/components/sensor/const.py (read against HA 2026.7.4). Keys and
device classes are read from sensor.py rather than listed here, so this test
tracks the code instead of a copy of it.

Run:  python3 tests/test_state_class.py
"""
import ast
import os
import pathlib
import sys

HERE = pathlib.Path(os.path.dirname(os.path.abspath(__file__)))
COMPONENT = HERE.parent / "custom_components" / "geely_global"

# device_class -> state classes HA accepts for it. A device class the code uses
# but that is missing here fails the test on purpose: look it up in HA's table
# before adding it. `None` (no device class) accepts anything.
ALLOWED: dict[str | None, set[str]] = {
    "BATTERY":     {"MEASUREMENT"},
    "DISTANCE":    {"MEASUREMENT", "MEASUREMENT_ANGLE", "TOTAL", "TOTAL_INCREASING"},
    "ENUM":        set(),
    "PRESSURE":    {"MEASUREMENT"},
    "SPEED":       {"MEASUREMENT"},
    "TEMPERATURE": {"MEASUREMENT"},
    "VOLTAGE":     {"MEASUREMENT"},
}


def _tree():
    return ast.parse((COMPONENT / "sensor.py").read_text())


def _enum_name(node):
    """SensorDeviceClass.BATTERY -> "BATTERY"; None -> None."""
    if isinstance(node, ast.Attribute):
        return node.attr
    return None


def _specs():
    """[(key, device_class_name_or_None, value_type), ...] from SENSOR_SPECS.

    Row layout: (key, friendly_name, path, unit, device_class, value_type, map).
    """
    for node in ast.walk(_tree()):
        target = getattr(node, "target", None) or (
            node.targets[0] if isinstance(node, ast.Assign) else None
        )
        if isinstance(target, ast.Name) and target.id == "SENSOR_SPECS":
            return [
                (e.elts[0].value, _enum_name(e.elts[4]), e.elts[5].value)
                for e in node.value.elts
            ]
    raise AssertionError("SENSOR_SPECS not found in sensor.py")


def _state_classes():
    """{key: state_class_name} from _STATE_CLASSES."""
    for node in ast.walk(_tree()):
        target = getattr(node, "target", None) or (
            node.targets[0] if isinstance(node, ast.Assign) else None
        )
        if isinstance(target, ast.Name) and target.id == "_STATE_CLASSES":
            return {
                k.value: _enum_name(v)
                for k, v in zip(node.value.keys, node.value.values)
            }
    raise AssertionError("_STATE_CLASSES not found in sensor.py")


def test_every_numeric_sensor_has_a_state_class():
    """Without it the sensor never reaches long-term statistics."""
    declared = _state_classes()
    missing = [
        key for key, _device_class, value_type in _specs()
        if value_type != "map" and key not in declared
    ]
    assert not missing, f"sensors with no state_class: {missing}"


def test_no_textual_sensor_declares_a_state_class():
    """A mapped sensor reports a label; statistics over labels mean nothing,
    and HA rejects the entity outright once the device class is ENUM."""
    declared = _state_classes()
    offenders = [
        key for key, _device_class, value_type in _specs()
        if value_type == "map" and key in declared
    ]
    assert not offenders, f"textual sensors with a state_class: {offenders}"


def test_state_classes_are_valid_for_their_device_class():
    declared = _state_classes()
    for key, device_class, _value_type in _specs():
        state_class = declared.get(key)
        if state_class is None:
            continue
        assert device_class in ALLOWED or device_class is None, (
            f"{key}: device_class {device_class} is not in this test's copy of "
            "HA's table - check DEVICE_CLASS_STATE_CLASSES before adding it"
        )
        if device_class is None:
            continue
        assert state_class in ALLOWED[device_class], (
            f"{key}: HA does not accept state_class {state_class} on a "
            f"{device_class} sensor (allowed: {sorted(ALLOWED[device_class]) or 'none'})"
        )


def test_no_state_class_is_left_over():
    """A key here but not in SENSOR_SPECS is dead weight - usually a rename."""
    keys = {key for key, _device_class, _value_type in _specs()}
    extra = sorted(set(_state_classes()) - keys)
    assert not extra, f"_STATE_CLASSES has stale keys {extra}"


def test_the_odometer_accumulates():
    """total_mileage and trip_meter feed "distance driven" statistics; as plain
    measurements HA would average them, which means nothing for a counter."""
    declared = _state_classes()
    for key in ("total_mileage", "trip_meter"):
        assert declared.get(key) == "TOTAL_INCREASING", (
            f"{key} is {declared.get(key)}, expected TOTAL_INCREASING"
        )


if __name__ == "__main__":
    tests = [v for k, v in sorted(globals().items()) if k.startswith("test_")]
    failed = 0
    for t in tests:
        try:
            t()
            print(f"ok   {t.__name__}")
        except AssertionError as e:
            failed += 1
            print(f"FAIL {t.__name__}: {e}")
    print()
    print(f"{len(tests) - failed}/{len(tests)} passaram")
    sys.exit(1 if failed else 0)
