"""Unit test for the scheduled-charging (bizType=6) POST body shape.

Runs standalone (no Home Assistant, no pytest): api.py imports only stdlib,
so we load it directly via importlib and monkeypatch the mTLS sender to
capture the body bytes that would go on the wire.

Regression target (verified live 2026-05-31): the Geely charge-server's
schedule write key for the charge model is `chargeModel`, NOT `rbcModel`.
`rbcModel` is only the read-only echo from GET; sending it as the write key
makes the server reject the populated window with
    illegal request parameter: rbcStartTime must be empty
With `chargeModel` present, `rbcStartTime`/`rbcEndTime` are the writable
window and must be POPULATED on both start and stop.

Run:  .venv/bin/python tests/test_scheduled_charging_body.py
"""
import importlib.util
import json
import os
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
API_PATH = os.path.join(HERE, "..", "custom_components", "geely_global", "api.py")

spec = importlib.util.spec_from_file_location("geely_api_under_test", API_PATH)
api = importlib.util.module_from_spec(spec)
spec.loader.exec_module(api)


def _make_api():
    return api.GeelyApi(
        app_id="x", app_secret="x", user_id="u", vin="VINTEST00000000",
        cidpsso_token="t", client_id="c", vehicle_series="s",
        vehicle_model="m", device_id="d", cert_path="/dev/null",
        key_path="/dev/null",
    )


def _capture_body(command):
    """Call scheduled_charging_set with the sender stubbed; return the body dict."""
    client = _make_api()
    captured = {}

    def fake_send(host, method, path, body, extra_headers=None):
        captured["body"] = json.loads(body)
        captured["path"] = path
        captured["method"] = method
        return 200, b'{"code":"1000","success":true}'

    client._mtls_send = fake_send
    client._headers_with_jwt = lambda: {}
    client.scheduled_charging_set(
        command=command, start_time="23:00", end_time="07:00",
        rbc_target="2", charge_model="0",
    )
    return captured["body"]


def _assert_write_shape(body, command):
    assert body["command"] == command, body
    # The model MUST be written as `chargeModel`, never `rbcModel` (the
    # read-only echo) - that is the exact bug that broke every write.
    assert "chargeModel" in body, f"body must use chargeModel, got keys {sorted(body)}"
    assert "rbcModel" not in body, f"rbcModel must NOT be sent (read-only echo): {body}"
    assert body["chargeModel"] == "0", body
    # With chargeModel present the window is writable and must be populated
    # on BOTH start and stop.
    assert body["rbcStartTime"] == "23:00", f"{command} must send populated rbcStartTime, got {body['rbcStartTime']!r}"
    assert body["rbcEndTime"] == "07:00", f"{command} must send populated rbcEndTime, got {body['rbcEndTime']!r}"
    assert body["rbcTarget"] == "2", body


def test_start_uses_chargeModel_with_populated_window():
    _assert_write_shape(_capture_body("start"), "start")


def test_stop_uses_chargeModel_with_populated_window():
    _assert_write_shape(_capture_body("stop"), "stop")


if __name__ == "__main__":
    failures = 0
    for fn in (test_start_uses_chargeModel_with_populated_window,
               test_stop_uses_chargeModel_with_populated_window):
        try:
            fn()
            print(f"PASS {fn.__name__}")
        except AssertionError as e:
            failures += 1
            print(f"FAIL {fn.__name__}: {e}")
    sys.exit(1 if failures else 0)
