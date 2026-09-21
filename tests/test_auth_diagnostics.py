"""Unit test for the JWT-refresh diagnostics counters added while
investigating the "Geely session dies every few weeks" disconnect.

Runs standalone (no Home Assistant, no pytest): api.py imports only stdlib,
so we load it directly via importlib and stub the network so refresh_jwt
runs its bookkeeping without touching the wire.

What we lock in: every successful mint of a fresh JWT from the cidpsso token
bumps `jwt_refresh_count` and stamps `last_jwt_refresh_ts`. The coordinator
reads these on an auth failure to log how long the session survived, which is
the evidence we need to tell a fixed server-side token lifetime apart from
other invalidation causes.

Run:  .venv/bin/python tests/test_auth_diagnostics.py
"""
import importlib.util
import os

HERE = os.path.dirname(os.path.abspath(__file__))
API_PATH = os.path.join(HERE, "..", "custom_components", "geely_global", "api.py")

spec = importlib.util.spec_from_file_location("geely_api_diag_under_test", API_PATH)
api = importlib.util.module_from_spec(spec)
spec.loader.exec_module(api)


def _make_api():
    return api.GeelyApi(
        app_id="x", app_secret="x", user_id="u", vin="VINTEST00000000",
        cidpsso_token="t", client_id="c", vehicle_series="s",
        vehicle_model="m", device_id="d", cert_path="/dev/null",
        key_path="/dev/null",
    )


def _stub_refresh(client):
    """Make refresh_jwt succeed without any network I/O."""
    client._get_access_code = lambda: "accesscode123"
    client._mtls_send = lambda host, method, path, body, extra_headers=None: (
        200,
        b'{"code":"1000","data":{"accessToken":"jwt","userId":"u","expiresIn":7200}}',
    )


def test_counter_starts_at_zero():
    client = _make_api()
    assert client.jwt_refresh_count == 0, client.jwt_refresh_count
    assert client.last_jwt_refresh_ts == 0.0, client.last_jwt_refresh_ts
    print("PASS test_counter_starts_at_zero")


def test_refresh_increments_counter_and_stamps_time():
    client = _make_api()
    _stub_refresh(client)
    client.refresh_jwt()
    assert client.jwt_refresh_count == 1, client.jwt_refresh_count
    assert client.last_jwt_refresh_ts > 0, client.last_jwt_refresh_ts
    first_ts = client.last_jwt_refresh_ts
    client.refresh_jwt()
    assert client.jwt_refresh_count == 2, client.jwt_refresh_count
    assert client.last_jwt_refresh_ts >= first_ts, client.last_jwt_refresh_ts
    print("PASS test_refresh_increments_counter_and_stamps_time")


if __name__ == "__main__":
    test_counter_starts_at_zero()
    test_refresh_increments_counter_and_stamps_time()
    print("\nAll auth-diagnostics tests passed.")
