# Geely Global API reference

Maps every Geely Global mobile-app control to the wire-level call. Use this when adding a new feature to the integration or when something stops working after a Geely backend change.

## Endpoint families

The integration uses two HTTP endpoint families on `apis.ecloudeu.com`, both over mTLS with the per-vehicle client certificate provisioned during config flow:

1. `PUT /remote-control/vehicle/telematics/{VIN}` (the classic telematics pipe). Body shape:
   ```json
   {
     "command": "start" | "stop",
     "creator": "tc",
     "operationScheduling": {"duration": <s>, "interval": 0, "occurs": 1, "recurrentOperation": false},
     "serviceId": "<SID>",
     "serviceParameters": [{"key": "<k>", "value": "<v>"}, ...],
     "timestamp": "<ms>",
     "userId": "<uid>"
   }
   ```

2. `POST /charge-server/ecarx_charge_set/{VIN}` (the charge-server pipe). Body is plain JSON; the shape varies per `bizType`:
   - `bizType=4`: Parking Comfort schedule
   - `bizType=6`: Scheduled charging
   - `bizType=7`: Rapid warm / Rapid cool (compound: AC + seats + window vent)

   `GET` on the same path with `?bizType=N` reads the saved schedule.

## Capability discovery

`GET /geelyTCAccess/tcservices/capability/{VIN}?pageSize=2000&pageIndex=1&vehicleType=0`

Returns roughly 65 entries describing what features this specific vehicle supports, with allowed value ranges. The integration uses this at setup to dynamically enable / disable entities and set bounds.

Notable function IDs:
- `remote_climate_control_2`: AC + seat + defrost service params (range, step, seat positions)
- `combined_climate_control`: rapid warm/cool params, may include `steel_wheel_heating: true` for trims that have it
- `remote_purification`: G-Clean
- `temperature_2`: interior / exterior temp read
- `remote_control_*_2`: windows, sunroof, sunshade, lock, unlock, open
- `parking_comfortable_2`: Parking Comfort
- `apt_charging_single_cycle_G2`: scheduled charging

## Status fields

State is read from two endpoints:
- `GET /remote-control/vehicle/status/{VIN}` (large response, climate state lives under `data.vehicleStatus.additionalVehicleStatus.climateStatus`)
- `GET /remote-control/vehicle/status/state/{VIN}` (the `*Active` flags)

Key climate fields:

| Field | Meaning |
|---|---|
| `preClimateActive` | AC pre-conditioning running. The AC indicator. |
| `defrost` | Defrost running ("true" / "false") |
| `airBlowerActive` | Blower fan. On the EX5 this only flips for G-Clean (not for AC or defrost). |
| `interiorTemp` / `exteriorTemp` | Cabin / outside temp |
| `drvHeatSts` / `passHeatingSts` | Driver / passenger seat heat: "0" off, "1" / "2" / "3" on at level |
| `drvVentSts` / `passVentSts` | Driver / passenger seat vent: "1" on, "2" off |
| `*VentDetail` | Level when on, "0" otherwise |
| `steerWhlHeatingSts` | Steering wheel heat (read-only field exists; command unverified) |
| `winStatus*` / `winPos*` | Per-window state and position |
| `sunroofOpenStatus` / `sunroofPos` | Sunroof state |
| `curtainOpenStatus` / `curtainPos` | Sunshade state |

## Not server-readable

**AC target temperature setpoint.** The capability catalog explicitly declares only `temperature_in_car` as the readable temp. The Geely cloud does not store the user's AC setpoint. The mobile apps cache it locally and re-push it on each set. The integration tracks it locally with `RestoreEntity` so it survives Home Assistant restarts.

## Verified service catalog

### Locking (telematics PUT)

| Action | serviceId | command | params |
|---|---|---|---|
| Lock all doors | `RDL_2` | start | `[{door: all}]` |
| Unlock all doors | `RDU_2` | start | `[{door: all}]` |
| Unlock trunk | `RDU_2` | start | `[{target: trunk}]` |

### Find car (telematics PUT)

| Action | serviceId | command | params |
|---|---|---|---|
| Honk + flash | `RHL` | start | `[{rhl: horn-light-flash}]` |

### Climate (telematics PUT, all `RCE_2`)

The master serviceId for all non-rapid climate is `RCE_2`. The first param picks the feature, the rest configure it.

| Action | command | params | duration |
|---|---|---|---|
| AC ON @ temp X | start | `[{rce.conditioner: "1"}, {rce.temp: "<C>"}]` | 180 |
| AC OFF | stop | same params | 0 |
| Set temp X (also turns on AC) | start | `[{rce.conditioner: "1"}, {rce.temp: "<C>"}]` | 180 |
| Defrost ON | start | `[{rce.conditioner: "2"}, {rce.level: "2"}]` | 90 |
| Defrost OFF | stop | same params | 0 |
| Seat heat ON | start | `[{rce.level: "1\|2\|3"}, {rce.heat: "<seat>"}]` | 90 |
| Seat heat OFF | stop | `[{rce.level: "0"}, {rce.heat: "<seat>"}]` | 90 |
| Seat vent ON | start | `[{rce.level: "1\|2\|3"}, {rce.ventilation: "<seat>"}]` | 90 |
| Seat vent OFF | stop | `[{rce.level: "0"}, {rce.ventilation: "<seat>"}]` | 90 |

Seat values: `front-left` (driver), `front-right` (passenger), `rear-left`, `rear-right`. The capability catalog says which exist on the trim (the EX5 has front-left and front-right only).

Temp format: string with one decimal, e.g. `"15.5"`, `"22.0"`, `"28.5"`.

### G-Clean (telematics PUT, `RCC_2`)

| Action | command | params | duration |
|---|---|---|---|
| G-Clean ON | start | `[{rcc.ventilation: "cabin"}]` | 6 |
| G-Clean OFF | stop | same params | 6 |

G-Clean cannot be turned on while AC or defrost is running. Activating either of those auto-stops G-Clean.

### Windows / sunshade / sunroof (telematics PUT, `RWS_2`)

| Action | command | params |
|---|---|---|
| Open all windows | start | `[{target: window}]` |
| Close all windows | stop | `[{target: window}]` |
| Open sun curtain | start | `[{target: sunshade}]` |
| Close sun curtain | stop | `[{target: sunshade}]` |
| Open sunroof | start | `[{target: sunroof}]` |
| Close sunroof | stop | `[{target: sunroof}]` |
| Vent windows | start | `[{target: ventilate}]` |

### Charging (telematics PUT, `RCS`)

| Action | command | params |
|---|---|---|
| Stop charging | stop | `[{operation: "0"}, {rcs.terminate: "1"}]` |
| Restart charging | start | `[{operation: "1"}, {rcs.restart: "1"}]` |

### Rapid warm / cool (charge-server POST, `bizType=7`)

Compound command. Body shape:

```json
{
  "ac": "true",
  "bizType": "7",
  "command": "immediately",
  "duration": "180",
  "heat": [{"level": "3", "pos": "11"}, {"level": "3", "pos": "19"}],
  "paa": "0",
  "temp": "28.5",
  "vlt": "false",
  "vltDuration": "60",
  "vltPos": "12",
  "timestamp": "<ms>"
}
```

For rapid cooling, swap `heat: [...]` for `ventilation: [...]`, set `temp: "15.5"`, `vlt: "true"`.

`pos`: `"11"` is driver, `"19"` is passenger (Zeekr-style numeric IDs in this endpoint, different from the seat-name strings used by `RCE_2`).

### Scheduled charging (charge-server POST, `bizType=6`)

State is readable via `GET ?bizType=6`:

```json
{
  "rbcStartTime": "23:00",
  "rbcEndTime": "07:00",
  "rbcTarget": "2",
  "rbcModel": "",
  "bcCycleActive": "true",
  "bizType": 6,
  "id": 779515
}
```

Set / toggle:

```json
{
  "bizType": "6",
  "command": "start" | "stop",
  "rbc": "2",
  "rbcStartTime": "HH:MM",
  "rbcEndTime": "HH:MM",
  "rbcTarget": "2",
  "rbcModel": "",
  "pin": "<vin>",
  "vin": "<vin>",
  "sessionId": "",
  "scheduledTime": "",
  "endTime": ""
}
```

### Parking Comfort (charge-server POST, `bizType=4`)

`GET ?bizType=4` reads the schedule. The full POST body for set / unset has not been captured because the feature must be enabled inside the vehicle first.

State: `_state.parkComfortState` (1 = on).

### Steering wheel heat (status field exists, command unverified)

The capability catalog declares `steel_wheel_heating: "true"` under `combined_climate_control` and the status field `climateStatus.steerWhlHeatingSts` exists. The mobile-app UI on the EX5 trim does not expose a button. Probe candidates from `geely_global.fire_control` in Developer Tools:
- `RCE_2 / start / [{rce.steel_wheel_heating: "true"}, {rce.level: "3"}]`
- `RCE_2 / start / [{rce.swh: "true"}, {rce.level: "3"}]`
- Adding a `bw` field to a `bizType=7` body
