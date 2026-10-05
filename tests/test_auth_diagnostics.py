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
import sys
import types

HERE = os.path.dirname(os.path.abspath(__file__))
_PKG_DIR = os.path.join(HERE, "..", "custom_components", "geely_global")


def _load_api():
    """Load api.py standalone. It is HA-free but imports `.const`, so we
    register a minimal `geely_global` package (without running __init__.py,
    which pulls in Home Assistant) and load const + api under it."""
    pkg = "geely_global"
    if pkg not in sys.modules:
        m = types.ModuleType(pkg)
        m.__path__ = [_PKG_DIR]
        sys.modules[pkg] = m

    def _mod(name):
        spec = importlib.util.spec_from_file_location(
            f"{pkg}.{name}", os.path.join(_PKG_DIR, name + ".py"))
        mod = importlib.util.module_from_spec(spec)
        sys.modules[spec.name] = mod
        spec.loader.exec_module(mod)
        return mod

    _mod("const")
    return _mod("api")


api = _load_api()


def _make_api():
    return api.GeelyApi(
        region="EU", user_id="u", vin="VINTEST00000000",
        cidpsso_token="t", client_id="c", vehicle_series="s",
        vehicle_model="m", device_id="d", cert_path="/dev/null",
        key_path="/dev/null",
    )


def _stub_refresh(client):
    """Make refresh_jwt succeed without any network I/O. Mirrors the real
    session/secure response, which includes a refreshToken."""
    client._get_access_code = lambda: "accesscode123"
    client._mtls_send = lambda host, method, path, body, extra_headers=None: (
        200,
        b'{"code":"1000","data":{"accessToken":"jwt","userId":"u",'
        b'"expiresIn":7200,"refreshToken":"rtok","idToken":"idt","tcToken":"tct"}}',
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


def test_refresh_jwt_captures_refresh_token():
    """The renewal credentials from session/secure must be kept, not discarded
    - the refresh_token is what a future cidpsso-independent renewal needs."""
    client = _make_api()
    _stub_refresh(client)
    assert client.refresh_token is None, client.refresh_token
    client.refresh_jwt()
    assert client.refresh_token == "rtok", client.refresh_token
    assert client.id_token == "idt", client.id_token
    assert client.tc_token == "tct", client.tc_token
    print("PASS test_refresh_jwt_captures_refresh_token")


def test_token_refresh_gated_off_by_default():
    """refresh_session_via_token must be inert while the endpoint shape is
    unknown, so the integration behaves exactly as before."""
    client = _make_api()
    client.refresh_token = "rtok"
    # Must not touch the network when the feature gate is off.
    def _boom(*a, **k):
        raise AssertionError("refresh_session_via_token hit the network while gated off")
    client._mtls_send = _boom
    assert client.refresh_session_via_token() is False
    print("PASS test_token_refresh_gated_off_by_default")


if __name__ == "__main__":
    test_counter_starts_at_zero()
    test_refresh_increments_counter_and_stamps_time()
    test_refresh_jwt_captures_refresh_token()
    test_token_refresh_gated_off_by_default()
    print("\nAll auth-diagnostics tests passed.")
