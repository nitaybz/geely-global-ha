"""Self-contained Geely TSP / mTLS API client.

Bundles the proven HMAC-SHA1 7-field signer + raw-socket mTLS helper from
poc/geely_mtls.py so this integration has no external poc/ dependency at
runtime.

All public methods are sync - HA wraps them with async_add_executor_job.
"""
from __future__ import annotations

import base64
import hashlib
import hmac
import json
import logging
import os
import socket
import ssl
import time
import uuid
from typing import Any
from urllib.parse import parse_qsl, quote, urlparse

from .const import DEFAULT_REGION, region_config

_LOGGER = logging.getLogger(__name__)


def region_from_login(login_data: dict, vin: str | None = None) -> str:
    """Derive the vehicle's telematics region from a cidpsso login response.

    Order of preference:
      1. the `tspInfo` entry whose `vin` matches (per-vehicle region),
      2. any `tspInfo[].serviceRegion`,
      3. `edgeInfo.code` (the account's primary vehicle region),
      4. DEFAULT_REGION.

    Deliberately ignores `masterInfo.code`, which is the *identity* center
    (e.g. EU) and can differ from where the vehicle actually lives.
    """
    tsp = login_data.get("tspInfo") or []
    if vin:
        for t in tsp:
            if t.get("vin") == vin and t.get("serviceRegion"):
                return t["serviceRegion"]
    for t in tsp:
        if t.get("serviceRegion"):
            return t["serviceRegion"]
    edge = login_data.get("edgeInfo") or {}
    return edge.get("code") or DEFAULT_REGION


class GeelyAuthError(Exception):
    """Raised when the Geely server rejects our credentials.

    Caused by: cidpsso token revoked (e.g. iPhone re-login kicked us out,
    or token aged out), or apis.ecloudeu JWT permanently invalid. The
    coordinator should catch this and surface a re-auth flow."""


class GeelyControlError(Exception):
    """Raised when the Geely server rejects a control command.

    Common causes:
      - code "8070" 'The last request has not yet been executed'
        (rate-limit; previous command still pending)
      - code "failure" 'Operation failed' (wrong serviceId/params for
        this trim)
      - code 1404/1405 (feature unavailable / vehicle in wrong state)
    Entities should catch this and re-raise as HomeAssistantError so
    the user sees a toast notification.
    """

    def __init__(self, code: Any, message: str | None) -> None:
        self.code = code
        self.message = message or f"Geely server returned code={code!r}"
        super().__init__(self.message)


# Error codes the Geely gateway returns when our session is invalid.
# Distilled from observed responses + poc/geely_client.py.
_AUTH_FAILURE_CODES: set = {
    # cidpsso/cidpcar token rejected
    60000000, 60000001, 60000110,
    "60000000", "60000001", "60000110",
    # apis.ecloudeu JWT invalid / expired beyond auto-refresh
    1402, "1402",
}

# Codes we treat as "command accepted by the server".
_CONTROL_SUCCESS_CODES: set = {1000, "1000"}


def _is_auth_failure(resp: dict) -> bool:
    return resp.get("code") in _AUTH_FAILURE_CODES


def _check_control_resp(resp: dict) -> dict:
    """Raise GeelyControlError if the response is not a success.

    Note: a successful response (`code=1000, success=True`) means the
    GATEWAY accepted the command - not that the car physically executed
    it. Status-field diff is the only way to verify execution. But this
    check at least catches obvious failures (wrong params, rate limit,
    unsupported feature).
    """
    if _is_auth_failure(resp):
        raise GeelyAuthError(f"control rejected: {resp.get('code')}")
    code = resp.get("code")
    if code in _CONTROL_SUCCESS_CODES and resp.get("success") in (True, "true"):
        return resp
    raise GeelyControlError(code, resp.get("message"))


# ---------- HMAC signer (proven bit-exact against iOS framework) ----------

def _percent_encode_value(s: str) -> str:
    enc = quote(s, safe="!*'();@&=+$?#[]")
    return enc.replace("/", "%2F").replace(":", "%3A").replace(",", "%2C")


def _build_sign_string(*, method, path, query, accept, nonce, sig_version,
                       timestamp_ms, body) -> str:
    accept = accept or "application/json;responseformat=3"
    sh = {
        "x-api-signature-nonce": nonce,
        "x-api-signature-version": sig_version,
    }
    canonical_headers = "".join(f"{k}:{sh[k]}\n" for k in sorted(sh.keys()))
    qis = sorted(parse_qsl(query, keep_blank_values=True), key=lambda kv: kv[0])
    canonical_query = ""
    for k, v in qis:
        canonical_query += f"{k}={_percent_encode_value(v)}&"
    if len(canonical_query) >= 2:
        canonical_query = canonical_query[:-1]
    md5_b64 = base64.b64encode(hashlib.md5(body).digest()).decode()
    return "\n".join([accept, canonical_headers, canonical_query, md5_b64,
                      f"{timestamp_ms}", method.upper(), path])


def _make_nonce() -> str:
    """Mimic Android's nonce format: 3hex-12hex 7alnum 13ts."""
    import random, string
    a = ''.join(random.choices('0123456789abcdef', k=3))
    b = ''.join(random.choices('0123456789abcdef', k=12))
    c = ''.join(random.choices(string.ascii_uppercase + string.digits, k=7))
    return f"{a}-{b}{c}{int(time.time()*1000)}"


# ---------- Raw-socket mTLS sender ----------

def _parse_chunked(body: bytes) -> bytes:
    out = b''
    pos = 0
    while pos < len(body):
        end = body.find(b'\r\n', pos)
        if end < 0:
            break
        try:
            sz = int(body[pos:end], 16)
        except ValueError:
            break
        if sz == 0:
            break
        pos = end + 2
        out += body[pos:pos + sz]
        pos += sz + 2
    return out


def _legacy_ctx() -> ssl.SSLContext:
    ctx = ssl.create_default_context()
    ctx.options |= 0x4   # OP_LEGACY_SERVER_CONNECT (m-lcmsam-eu.geely.com)
    ctx.check_hostname = False
    ctx.verify_mode = ssl.CERT_NONE
    return ctx


# ---------- API client ----------

class GeelyApi:
    """Holds the long-lived cidpsso token + per-vehicle cert/key, plus a
    rotating JWT for apis.ecloudeu.com calls."""

    def __init__(
        self,
        *,
        region: str,
        user_id: str,
        vin: str,
        cidpsso_token: str,
        client_id: str,
        vehicle_series: str,
        vehicle_model: str,
        device_id: str,
        cert_path: str,
        key_path: str,
    ) -> None:
        cfg = region_config(region)
        self.region = region
        self.app_id = cfg["app_id"]
        self.app_secret = cfg["app_secret"]
        self.cert_host = cfg["cert_host"]
        self.control_host = cfg["control_host"]
        self.app_host = cfg["app_host"]
        self.user_id = user_id
        self.vin = vin
        self.cidpsso_token = cidpsso_token
        self.client_id = client_id
        self.vehicle_series = vehicle_series
        self.vehicle_model = vehicle_model
        self.device_id = device_id
        self.cert_path = cert_path
        self.key_path = key_path
        # JWT cache
        self._jwt: str | None = None
        self._jwt_uid: str | None = None
        self._jwt_exp: int = 0   # unix ms

    # ---- low-level helpers ----

    def _sign_headers(self, method: str, url: str, body: bytes) -> dict:
        p = urlparse(url)
        nonce = _make_nonce()
        ts_ms = int(time.time() * 1000)
        accept = "application/json;responseformat=3"
        ss = _build_sign_string(
            method=method, path=p.path, query=p.query, accept=accept,
            nonce=nonce, sig_version="1.0", timestamp_ms=ts_ms, body=body,
        )
        sig = base64.b64encode(
            hmac.new(self.app_secret.encode(), ss.encode(), hashlib.sha1).digest()
        ).decode()
        return {
            "X-APP-ID": self.app_id,
            "Accept": accept,
            "X-AGENT-TYPE": "android",
            "X-DEVICE-TYPE": "mobile",
            "X-OPERATOR-CODE": "geely",
            "X-DEVICE-IDENTIFIER": self.device_id,
            "X-ENV-TYPE": "production",
            "X-VERSION": "geelyNew",
            "X-TIMEZONE": "UTC",
            "Accept-Language": "en_US",
            "Content-Type": "application/json; charset=utf-8",
            "X-api-signature-version": "1.0",
            "X-api-signature-nonce": nonce,
            "X-timestamp": str(ts_ms),
            "X-signature": sig,
            "user-agent": "okhttp/4.11.0",
        }

    def _mtls_send(self, host: str, method: str, path: str, body: bytes,
                   extra_headers: dict | None = None) -> tuple[int, bytes]:
        ctx = _legacy_ctx()
        ctx.load_cert_chain(self.cert_path, self.key_path)

        headers = self._sign_headers(method, f"https://{host}{path}", body)
        headers["Host"] = host
        headers["connection"] = "close"
        headers["content-length"] = str(len(body))
        if extra_headers:
            headers.update(extra_headers)
        head_lines = [f"{method} {path} HTTP/1.1"] + [f"{k}: {v}" for k, v in headers.items()]
        req_bytes = ("\r\n".join(head_lines) + "\r\n\r\n").encode() + body

        sock = socket.create_connection((host, 443), timeout=30)
        try:
            ssock = ctx.wrap_socket(sock, server_hostname=host)
            try:
                ssock.send(req_bytes)
                data = b''
                while True:
                    c = ssock.recv(4096)
                    if not c:
                        break
                    data += c
                    if len(data) > 200_000:
                        break
            finally:
                ssock.close()
        except Exception:
            sock.close()
            raise
        head_part, _, body_part = data.partition(b'\r\n\r\n')
        head_str = head_part.decode('utf-8', errors='replace')
        status_line = head_str.split('\r\n', 1)[0]
        try:
            status = int(status_line.split(' ')[1])
        except Exception:
            status = 0
        if 'chunked' in head_str.lower():
            body_part = _parse_chunked(body_part)
        return status, body_part

    # ---- high-level operations ----

    def _get_access_code(self) -> str:
        """1-time accessCode from cidpsso (used to fetch apis JWT)."""
        import urllib.request
        ctx = _legacy_ctx()
        body = json.dumps({"state": str(uuid.uuid4())}).encode()
        req = urllib.request.Request(
            f"https://{self.app_host}/cidpsso/oauth2/v1/getCode",
            data=body, method="POST",
            headers={
                "token": self.cidpsso_token,
                "user-agent": "okhttp/4.11.0",
                "content-type": "application/json; charset=utf-8",
            },
        )
        with urllib.request.urlopen(req, context=ctx, timeout=15) as resp:
            j = json.loads(resp.read())
        if _is_auth_failure(j):
            raise GeelyAuthError(f"cidpsso token rejected: {j}")
        if j.get("code") != 10000000:
            raise RuntimeError(f"getCode failed: {j}")
        return j["data"]["accessCode"]

    def refresh_jwt(self) -> dict:
        """Exchange a cidpsso accessCode for an apis.ecloudeu.com JWT."""
        ac = self._get_access_code()
        body = json.dumps({"authCode": ac}).encode()
        status, resp = self._mtls_send(
            self.control_host, "POST",
            "/auth/account/session/secure?identity_type=geelyos",
            body,
        )
        j = json.loads(resp)
        if _is_auth_failure(j):
            raise GeelyAuthError(f"session/secure rejected our auth: {j}")
        if j.get("code") not in (1000, "1000"):
            raise RuntimeError(f"session/secure failed: {j}")
        d = j["data"]
        self._jwt = d["accessToken"]
        self._jwt_uid = d["userId"]
        self._jwt_exp = int(time.time()) + int(d.get("expiresIn", 7200))
        return d

    def _ensure_jwt(self) -> str:
        if not self._jwt or time.time() > self._jwt_exp - 60:
            self.refresh_jwt()
        return self._jwt   # type: ignore[return-value]

    def _headers_with_jwt(self) -> dict:
        return {
            "Authorization": self._ensure_jwt(),
            "X-CLIENT-ID": self.client_id,
            "X-VEHICLE-SERIES": self.vehicle_series,
            "X-VEHICLE-MODEL": self.vehicle_model,
            "X-Vehicle-IDENTIFIER": self.vin,
        }

    # ---- READ ----

    def _authed_apis_call(self, method: str, path: str, body: bytes) -> dict:
        """Call apis.ecloudeu.com with JWT. On code 1402 (JWT invalidated -
        most commonly because another client like the iOS app just logged
        in), auto-refresh the JWT once and retry. Only escalates to
        GeelyAuthError when the cidpsso token itself has been revoked."""
        status, resp = self._mtls_send(
            self.control_host, method, path, body,
            extra_headers=self._headers_with_jwt(),
        )
        j = json.loads(resp)
        if j.get("code") in {1402, "1402"}:
            _LOGGER.info("JWT invalidated mid-call (likely another client "
                         "logged in); refreshing and retrying once")
            self._jwt = None
            self._jwt_exp = 0
            try:
                self.refresh_jwt()
            except GeelyAuthError:
                # cidpsso token also dead - needs reauth
                raise
            status, resp = self._mtls_send(
                self.control_host, method, path, body,
                extra_headers=self._headers_with_jwt(),
            )
            j = json.loads(resp)
        if _is_auth_failure(j):
            raise GeelyAuthError(f"{method} {path} auth-rejected: {j}")
        return j

    def vehicle_status(self) -> dict:
        """GET full vehicle status with the same query the Geely app uses on
        map-view open: `?userId=&latest=&target=`. The empty `latest=` and
        `target=` flags signal the cloud to return the most recently uploaded
        snapshot (incl. fresh GPS if the car just pushed it). Without those
        flags the gateway serves an older cached snapshot for the position
        field. AVD-Frida confirmed (2026-05-03)."""
        path = (f"/remote-control/vehicle/status/{self.vin}"
                f"?userId={self.user_id}&latest=&target=")
        return self._authed_apis_call("GET", path, b"")

    def request_position_refresh(self) -> dict:
        """Fire PAI/operation:4/pai:1 — the Geely app fires this every time the
        map view opens to wake the car and request a fresh GPS upload. After
        the cloud ACKs (code=1000), wait a few seconds then re-fetch
        vehicle_status with `?...&latest=&target=` to read the new position.
        AVD-Frida confirmed (2026-05-03)."""
        body = {
            "command": "start",
            "creator": "tc",
            "latest": True,
            "serviceId": "PAI",
            "serviceParameters": [
                {"key": "operation", "value": "4"},
                {"key": "pai", "value": "1"},
            ],
            "timestamp": str(int(time.time() * 1000)),
            "userId": str(self.user_id),
        }
        path = f"/remote-control/vehicle/telematics/{self.vin}"
        status, resp = self._mtls_send(
            self.control_host, "PUT", path, json.dumps(body).encode(),
            extra_headers=self._headers_with_jwt(),
        )
        return json.loads(resp)

    def vehicle_status_state(self) -> dict:
        path = f"/remote-control/vehicle/status/state/{self.vin}"
        status, resp = self._mtls_send(
            self.control_host, "GET", path, b"",
            extra_headers=self._headers_with_jwt(),
        )
        return json.loads(resp)

    def charging_reservation(self) -> dict:
        path = f"/remote-control/charging/reservation/{self.vin}"
        status, resp = self._mtls_send(
            self.control_host, "GET", path, b"",
            extra_headers=self._headers_with_jwt(),
        )
        return json.loads(resp)

    def charge_server_get(self, biz_type: str) -> dict:
        """GET /charge-server/ecarx_charge_set/{VIN}?bizType=N.

        Reads schedules for charge-server features:
          - bizType=4 → Parking Comfort schedule
          - bizType=6 → Scheduled Charging (rbcStartTime/rbcEndTime/rbcTarget/bcCycleActive)
          - bizType=7 → Rapid (write-only; GET returns nothing useful)
        Returns the full response dict ({"code":"1000","data":{...}}).
        """
        path = f"/charge-server/ecarx_charge_set/{self.vin}?bizType={biz_type}"
        status, resp = self._mtls_send(
            self.control_host, "GET", path, b"",
            extra_headers=self._headers_with_jwt(),
        )
        return json.loads(resp)

    def scheduled_charging_set(self, *, command: str, start_time: str,
                                end_time: str, rbc_target: str = "2",
                                rbc: str = "2", charge_model: str = "0") -> dict:
        """Set scheduled charging. command="start" enables, "stop" disables.

        Body shape for bizType=6 (charge-server). The charge-model write key
        is `chargeModel` - NOT `rbcModel`. `rbcModel` is only the read-only
        echo the GET returns; sending it as the write key puts the server in
        a branch that rejects a populated window with
        `illegal request parameter: rbcStartTime must be empty`. With
        `chargeModel` present, `rbcStartTime`/`rbcEndTime` are the *writable*
        schedule window and must be populated (sending them empty then fails
        with `rbcEndTime is missing`). The same body shape serves both
        start (enable + arm at the window) and stop (disable); `command`
        selects the forwarded operation (1/0). Verified live 2026-05-31:
        start -> op=1 + forwarded rbc.startTime, stop -> op=0.
        """
        body = {
            "bizType": "6",
            "command": command,
            "chargeModel": charge_model,
            "endTime": "",
            "pin": self.vin,
            "rbc": rbc,
            "rbcEndTime": end_time,
            "rbcStartTime": start_time,
            "rbcTarget": rbc_target,
            "scheduledTime": "",
            "sessionId": "",
            "vin": self.vin,
        }
        body_bytes = json.dumps(body, separators=(",", ":")).encode()
        path = f"/charge-server/ecarx_charge_set/{self.vin}"
        status, resp = self._mtls_send(
            self.control_host, "POST", path, body_bytes,
            extra_headers=self._headers_with_jwt(),
        )
        j = json.loads(resp)
        return _check_control_resp(j)

    # ---- WRITE (control) ----

    def control(self, service_id: str, parameters: list[dict] | None = None,
                command: str = "start", duration: int = 0) -> dict:
        """Fire a control command via PUT /remote-control/vehicle/telematics/{VIN}.

        `duration` is the value put into operationScheduling.duration. The
        AVD-captured Geely app uses 90 for seat features, 180 for AC,
        6 for G-clean, and 0 for stop commands. Default 0 (legacy behaviour).
        """
        body_dict = {
            "command": command,
            "creator": "tc",
            "operationScheduling": {
                "duration": duration, "interval": 0, "occurs": 1, "recurrentOperation": False,
            },
            "serviceId": service_id,
            "serviceParameters": parameters or [],
            "timestamp": str(int(time.time() * 1000)),
            "userId": self._jwt_uid or self.user_id,
        }
        body = json.dumps(body_dict, separators=(",", ":")).encode()
        path = f"/remote-control/vehicle/telematics/{self.vin}"
        status, resp = self._mtls_send(
            self.control_host, "PUT", path, body,
            extra_headers=self._headers_with_jwt(),
        )
        j = json.loads(resp)
        return _check_control_resp(j)

    # ---- Compound rapid warm/cool (different endpoint) ----

    def rapid_climate(self, *, ac: bool, temp: str, heat_seats: list[str] | None,
                      vent_seats: list[str] | None, vlt: bool,
                      duration: str = "180", vlt_duration: str = "60",
                      vlt_pos: str = "12") -> dict:
        """Fire compound climate command via POST /charge-server/ecarx_charge_set.

        Captured from the Android app's "rapid warming" / "rapid cooling"
        buttons (bizType=7). Bundles AC + seat heat OR vent + window vent
        in a single shot.

        seats: numeric Zeekr-style positions - 11=driver, 19=passenger.
        """
        body: dict = {
            "ac": "true" if ac else "false",
            "bizType": "7",
            "command": "immediately",
            "duration": duration,
            "paa": "0",
            "temp": temp,
            "timestamp": str(int(time.time() * 1000)),
            "vlt": "true" if vlt else "false",
            "vltDuration": vlt_duration,
            "vltPos": vlt_pos,
        }
        if heat_seats:
            body["heat"] = [{"level": "3", "pos": p} for p in heat_seats]
        if vent_seats:
            body["ventilation"] = [{"level": "3", "pos": p} for p in vent_seats]
        body_bytes = json.dumps(body, separators=(",", ":")).encode()
        path = f"/charge-server/ecarx_charge_set/{self.vin}"
        status, resp = self._mtls_send(
            self.control_host, "POST", path, body_bytes,
            extra_headers=self._headers_with_jwt(),
        )
        j = json.loads(resp)
        return _check_control_resp(j)

    # ---- Capability discovery ----

    def fetch_capabilities(self) -> list[dict]:
        """Fetch the per-vehicle feature catalog. Returns the data.list array.

        Used at coordinator setup to decide which entities to expose. The
        catalog returns one entry per `functionId` (e.g. `remote_climate_control_2`,
        `combined_climate_control`, `remote_purification`, `temperature_2`,
        plus battery/door/etc. status fields). Each entry has `valueEnable`,
        `paramsJson`, `valueRange`, `valueEnum`. See docs/AVD_CAPTURE_GUIDE.md
        for the full schema.
        """
        path = (
            f"/geelyTCAccess/tcservices/capability/{self.vin}"
            "?pageSize=2000&pageIndex=1&vehicleType=0&sortField=&direction="
        )
        status, resp = self._mtls_send(
            self.control_host, "GET", path, b"",
            extra_headers=self._headers_with_jwt(),
        )
        try:
            j = json.loads(resp)
            return (j.get("data") or {}).get("list", []) or []
        except Exception:
            return []


# ---------- Cert provisioning (one-time during config_flow) ----------

def _sign_cert_request(app_id: str, app_secret: str,
                                    method: str, url: str, body: bytes) -> dict:
    """Standalone signer for /auth/cert/* (single-auth, no mTLS)."""
    p = urlparse(url)
    nonce = _make_nonce()
    ts_ms = int(time.time() * 1000)
    ss = _build_sign_string(
        method=method, path=p.path, query=p.query, accept="application/json;responseformat=3",
        nonce=nonce, sig_version="1.0", timestamp_ms=ts_ms, body=body,
    )
    sig = base64.b64encode(
        hmac.new(app_secret.encode(), ss.encode(), hashlib.sha1).digest()
    ).decode()
    return {
        "X-APP-ID": app_id,
        "Accept": "application/json;responseformat=3",
        "X-AGENT-TYPE": "android",
        "X-DEVICE-TYPE": "mobile",
        "X-OPERATOR-CODE": "geely",
        "X-ENV-TYPE": "production",
        "X-VERSION": "geelyNew",
        "Content-Type": "application/json; charset=utf-8",
        "X-api-signature-version": "1.0",
        "X-api-signature-nonce": nonce,
        "X-timestamp": str(ts_ms),
        "X-signature": sig,
        "user-agent": "okhttp/4.11.0",
    }


def provision_user_cert(*, app_id: str, app_secret: str, cert_host: str,
                         user_id: str, cidpsso_token: str, cert_out_path: str,
                         key_out_path: str) -> tuple[str, str]:
    """Generate EC P-256 keypair + CSR, send through /auth/cert/info + /file,
    save signed cert + key. Returns (cert_path, key_path)."""
    from cryptography.hazmat.primitives.asymmetric import ec
    from cryptography.hazmat.primitives import hashes, serialization
    from cryptography.x509.oid import NameOID
    from cryptography import x509
    import urllib.request

    # 1. Generate keypair
    priv = ec.generate_private_key(ec.SECP256R1())
    cn_short = hashlib.sha256(user_id.encode()).hexdigest()[:8]
    csr = (
        x509.CertificateSigningRequestBuilder()
        .subject_name(x509.Name([
            x509.NameAttribute(NameOID.COUNTRY_NAME, "CN"),
            x509.NameAttribute(NameOID.STATE_OR_PROVINCE_NAME, "ZheJiang"),
            x509.NameAttribute(NameOID.LOCALITY_NAME, "Hangzhou"),
            x509.NameAttribute(NameOID.ORGANIZATION_NAME, "ECARX"),
            x509.NameAttribute(NameOID.ORGANIZATIONAL_UNIT_NAME, "CloudDept"),
            x509.NameAttribute(NameOID.COMMON_NAME, cn_short),
        ]))
        .sign(priv, hashes.SHA256())
    )
    csr_pem = csr.public_bytes(serialization.Encoding.PEM).decode()

    # 2. POST /auth/cert/info → checkCode
    body = json.dumps({"checkValue": user_id}, separators=(',', ':')).encode()
    headers = _sign_cert_request(
        app_id, app_secret, "POST",
        f"https://{cert_host}/auth/cert/info", body)
    ctx = _legacy_ctx()
    req = urllib.request.Request(
        f"https://{cert_host}/auth/cert/info",
        data=body, method="POST", headers=headers)
    with urllib.request.urlopen(req, context=ctx, timeout=20) as resp:
        j = json.loads(resp.read())
    if j.get("code") != 1000:
        raise RuntimeError(f"cert/info failed: {j}")
    check_code = j["data"]["checkCode"]

    # 3. POST /auth/cert/file → signed cert
    device_for_cert = hashlib.sha256(f"{user_id}_home_assistant_ha".encode()).hexdigest()
    body = json.dumps({
        "csr": csr_pem,
        "identityType": "geelyos",
        "accessToken": cidpsso_token,
        "deviceId": device_for_cert,
        "checkCode": check_code,
    }, separators=(',', ':')).encode()
    headers = _sign_cert_request(
        app_id, app_secret, "POST",
        f"https://{cert_host}/auth/cert/file", body)
    req = urllib.request.Request(
        f"https://{cert_host}/auth/cert/file",
        data=body, method="POST", headers=headers)
    with urllib.request.urlopen(req, context=ctx, timeout=30) as resp:
        j = json.loads(resp.read())
    if j.get("code") != 1000:
        raise RuntimeError(f"cert/file failed: {j}")
    cert_pem = j["data"]["cert"]

    # 4. Save to disk
    os.makedirs(os.path.dirname(cert_out_path), exist_ok=True)
    open(cert_out_path, "w").write(cert_pem)
    open(key_out_path, "w").write(priv.private_bytes(
        encoding=serialization.Encoding.PEM,
        format=serialization.PrivateFormat.PKCS8,
        encryption_algorithm=serialization.NoEncryption(),
    ).decode())
    return cert_out_path, key_out_path


# ---------- cidpsso login (for config_flow) ----------

# The login/OTP host is global and region-independent. The per-region app_host
# (cidpcar vehicle list) is resolved from the region config instead.
LOGIN_HOST = "https://access-app-global.geely.com"


def _ios_headers(token: str | None = None, user_id: str | None = None,
                 country_code: str = "IL", *,
                 idfa: str | None = None, idfv: str | None = None) -> dict:
    """Mimic the Geely iOS app's headers verbatim - required for both cidpsso
    and cidpcar gateway calls.

    `idfa` and `idfv` should be passed from the per-install fingerprint so
    HA's session is distinguishable from the user's iPhone/Android session.
    Only used as a fallback when omitted (mostly during initial setup).
    """
    import secrets as _secrets
    rt = int(time.time() * 1000)
    h = {
        "Content-Type":  "application/json",
        "User-Agent":    "geely/1.9.8 (iPhone; iOS 26.3.1; Scale/3.00)",
        "version":       "1.9.8",
        "devicename":    "Home Assistant",
        "model":         "iPhone",
        "system-flag":   "1",
        "systemversion": "26.3.1",
        "countrycode":   country_code,
        "accept-language": "en-GB",
        "lang":          "en-GB",
        "accept":        "*/*",
        "devicehardwareidfa": (idfa or str(uuid.uuid4()).upper()),
        "devicehardwareidfv": (idfv or str(uuid.uuid4()).upper()),
        "requesttime":   str(rt),
        "requestid":     f"{rt}{_secrets.token_hex(6)}",
    }
    if token:
        h["token"] = token
    if user_id:
        h["userid"] = user_id
    return h


def make_install_fingerprint() -> tuple[str, str]:
    """Generate a (idfa, idfv) pair for this HA install. Persist these in
    the ConfigEntry data and pass them to every cidpsso/cidpcar call so the
    server treats HA as a distinct device from the user's phone."""
    return (str(uuid.uuid4()).upper(), str(uuid.uuid4()).upper())


def _legacy_session():
    """`requests` session with OpenSSL legacy-renegotiation enabled - Geely's
    gateway needs it on Python 3.12+."""
    import requests
    from urllib3.util.ssl_ import create_urllib3_context

    class _Adapter(requests.adapters.HTTPAdapter):
        def init_poolmanager(self, *a, **kw):
            ctx = create_urllib3_context()
            ctx.options |= 0x4
            kw["ssl_context"] = ctx
            return super().init_poolmanager(*a, **kw)

    s = requests.Session()
    s.mount("https://", _Adapter())
    return s


def cidpsso_send_otp(email: str, country_code: str = "IL", *,
                     max_attempts: int = 5,
                     idfa: str | None = None, idfv: str | None = None) -> dict:
    """Solve the Geely GeeTest captcha + trigger OTP email send.

    The captcha solver is image-based and ~85% accurate, so we retry up to
    `max_attempts` times. Returns the first successful /getCaptcha response,
    or the last response/error encountered.
    """
    from . import geetest_solver

    last_response: dict | None = None
    last_error: str | None = None
    s = _legacy_session()
    headers = _ios_headers(country_code=country_code, idfa=idfa, idfv=idfv)

    for attempt in range(1, max_attempts + 1):
        try:
            captcha = geetest_solver.solve(verbose=False)
        except Exception as e:  # noqa: BLE001
            last_error = f"captcha solve threw: {e}"
            _LOGGER.debug("captcha attempt %d threw: %s", attempt, e)
            continue
        if not (captcha.get("status") == "success"
                and captcha.get("data", {}).get("result") == "success"):
            last_error = f"captcha solve rejected by /verify: {captcha}"
            _LOGGER.debug("captcha attempt %d rejected: %s", attempt, captcha)
            continue
        v = captcha["data"]
        body = {
            "captchaType":   "2",
            "passToken":     v["pass_token"],
            "platform":      "ios-login",
            "lotNumber":     v["lot_number"],
            "captchaOutput": v["captcha_output"],
            "genTime":       str(v["gen_time"]),
            "email":         email,
            "captchaScene":  "101",
        }
        r = s.post(f"{LOGIN_HOST}/cidpsso/captcha/v3/getCaptcha",
                   headers=headers, json=body, timeout=20)
        resp = r.json()
        last_response = resp
        if resp.get("success") or resp.get("code") == 10000000:
            return resp
        _LOGGER.debug("getCaptcha attempt %d server-rejected: %s", attempt, resp)

    if last_response is not None:
        return last_response
    raise RuntimeError(f"captcha solver failed all {max_attempts} attempts: {last_error}")


def cidpsso_login(email: str, otp: str, country_code: str = "IL", *,
                  idfa: str | None = None, idfv: str | None = None) -> dict:
    """Exchange OTP code for cidpsso session token. Returns server response.
    Token is in data.token; userId is data.userId."""
    body = {
        "countryCode":    country_code,
        "account":        email,
        "code":           otp,
        "registerSource": 102,
        "loginType":      3,    # 3 = email-code
        "accountType":    2,    # 2 = email
    }
    s = _legacy_session()
    r = s.post(f"{LOGIN_HOST}/cidpsso/user/v3/login",
               headers=_ios_headers(country_code=country_code,
                                    idfa=idfa, idfv=idfv),
               json=body, timeout=20)
    return r.json()


def list_vehicles(cidpsso_token: str, user_id: str | None = None,
                  country_code: str = "IL", *, app_host: str | None = None,
                  idfa: str | None = None, idfv: str | None = None) -> list[dict]:
    """List vehicles for the logged-in account. Returns the `data` list
    from /cidpcar/vehicleOwner/v2/controlCars. `app_host` is the region's
    cidpcar host; falls back to the default region when omitted."""
    host = app_host or region_config(DEFAULT_REGION)["app_host"]
    s = _legacy_session()
    r = s.get(f"https://{host}/cidpcar/vehicleOwner/v2/controlCars",
              headers=_ios_headers(token=cidpsso_token, user_id=user_id,
                                   country_code=country_code,
                                   idfa=idfa, idfv=idfv),
              timeout=20)
    j = r.json()
    return j.get("data") or []
