# Geely Global for Home Assistant

[![hacs_badge](https://img.shields.io/badge/HACS-Custom-41BDF5.svg)](https://github.com/hacs/integration)
[![License: MIT](https://img.shields.io/badge/License-MIT-yellow.svg)](LICENSE)
[![Validate](https://github.com/nitaybz/geely-global-ha/actions/workflows/validate.yml/badge.svg)](https://github.com/nitaybz/geely-global-ha/actions/workflows/validate.yml)

A custom Home Assistant integration for vehicles using the **Geely Global / Geely International** mobile app. Tested on the **Geely EX5** but should work on most Geely-family EVs that use the same backend.

## What you get

The integration is **capability driven**: at setup it queries the per-VIN feature catalog and only exposes entities for the features your specific trim supports. Climate temperature bounds, available seat positions, and available controls are all detected dynamically.

| Platform | Entities |
|---|---|
| `lock` | Doors lock / unlock |
| `climate` | AC on/off, target temperature, presets `Rapid Warming` / `Rapid Cooling` |
| `switch` | G-Clean, Defrost, Charging, Scheduled Charging, Window Ventilation, Parking Comfort |
| `select` | Seat heat / vent for each seat the vehicle has (driver, passenger, etc.) |
| `cover` | Sunroof, Sunshade, All Windows |
| `button` | Find Car, Unlock Trunk |
| `time` | Scheduled Charging start / end (under Configuration) |
| `sensor` | Battery %, Electric Range, Total Mileage, Interior / Exterior Temperature, Speed, Engine State, Park Brake, Charger Connection, Time To Full Charge, 12V Battery + Voltage, Average Consumption, Trip Meter, Average Speed, 4 x Tire Pressure, Days / Distance to Service |
| `binary_sensor` | Door open (per door), Trunk, Hood, Driver Seatbelt, Charger Plug |
| `device_tracker` | GPS location |

## Installation

### Via HACS (recommended)

1. In Home Assistant, open **HACS** and go to **Integrations**.
2. Click the three-dot menu (top right) and pick **Custom repositories**.
3. Add `https://github.com/nitaybz/geely-global-ha` with category **Integration**.
4. Search for **Geely Global** and install.
5. Restart Home Assistant.

### Manual

1. Copy the `custom_components/geely_intl/` folder into your Home Assistant `config/custom_components/` directory.
2. Restart Home Assistant.

## Setup

1. In Home Assistant, go to **Settings → Devices & Services → Add Integration → Geely Global**.
2. Enter your Geely account email and country code (e.g. `IL`, `GB`, `DE`).
3. A 6-digit code is emailed to you. Paste it.
4. The integration auto-fetches your vehicle list and provisions a per-device mTLS certificate. The cert persists across re-logins.

Multi-vehicle accounts are supported. Re-run the flow to add additional vehicles. Vehicles already configured are filtered out of the picker.

## Polling

Default poll interval is 90 seconds. After every control command the coordinator schedules a refresh roughly 8 seconds later so the new state appears quickly. The coordinator pulls three endpoints on each cycle: vehicle status, vehicle status state, and the scheduled charging schedule (charge-server bizType 6).

## Limitations and caveats

- **Single session per Geely account.** The Geely auth server invalidates any other client session on each new login. If you log in with the iOS app and Home Assistant on the same account, each one will kick the other out. When this happens HA shows a **Reconfigure** prompt: click it, get a fresh OTP, and re-login. The mTLS certificate persists across re-logins so you only do cert provisioning once.
- **AC target temperature is local-only.** The Geely cloud does not expose the AC setpoint as readable state. The integration tracks it in the entity's local cache and persists it across restarts via `RestoreEntity`. Each device (HA, iOS app, Android app) keeps its own copy.
- **Captcha solver.** Login uses GeeTest puzzle. The bundled solver succeeds on most attempts and retries during OTP send.
- **Some features need the car in a specific state.** AC and seat heating commands may be silently ignored by the car if conditions are not met (for example, an empty cabin with the car asleep). The server still returns success because it acknowledges the command. The vehicle status fields will reflect what actually happened on the next poll.
- **Some features need to be enabled inside the vehicle first.** Parking Comfort requires the user to turn it on from the car's center screen before the app can control it.

## Error feedback

When the Geely server rejects a command (rate limit, invalid params, feature unavailable), the integration surfaces the error as a Home Assistant toast notification. The optimistic state is not applied, so the UI never shows a state the car did not reach.

## Disclaimer

This is an unofficial integration. It is not affiliated with, endorsed by, or supported by Geely. Use at your own risk. The Geely API may change at any time and break this integration.

You are responsible for your own compliance with Geely's terms of service. The integration uses your own credentials and your own vehicle's mTLS certificate.

## License

[MIT](LICENSE)
