# Pyronix Cloud for Home Assistant

[![CI](https://github.com/fregster/ha-pyronix-integration/actions/workflows/ci.yml/badge.svg)](https://github.com/fregster/ha-pyronix-integration/actions/workflows/ci.yml)
[![GitHub release](https://img.shields.io/github/v/release/fregster/ha-pyronix-integration?include_prereleases)](https://github.com/fregster/ha-pyronix-integration/releases)

[![Open your Home Assistant instance and open this repository in HACS.](https://my.home-assistant.io/badges/hacs_repository.svg)](https://my.home-assistant.io/redirect/hacs_repository/?owner=fregster&repository=ha-pyronix-integration&category=integration)

> **Status: pre-alpha.** The protocol was worked out from the Android app (see
> [PROTOCOL.md](PROTOCOL.md)). Read-only sessions (handshake, login, state dump, pushed updates)
> have been tested against a real Euro 46. The library and integration are tested offline against
> a fake panel; **arming and disarming have not been tested on a real panel yet.** Do not rely on
> this to protect your property.

An unofficial Home Assistant custom integration for Pyronix intruder alarm panels (Enforcer,
Euro 46). It uses the same Pyronix Cloud relay as the official **HomeControl2.0** app.

Not affiliated with or endorsed by Pyronix or Hikvision.

## How it works

The panel has no local API that the app uses. Everything goes through the Pyronix Cloud:

```
Home Assistant ──WSS──▶ wss://app.pyronixcloud.com ──(panel uplink)──▶ Panel
        └────────── end-to-end session: ECDH P-256 + AES-128-CFB ──────────┘
```

1. **Cloud relay.** An XML-like handshake identifies the panel and opens a relay.
2. **Encrypted panel session.** Binary frames are protected with keys derived from an ECDH
   exchange and the panel's app password.
3. **Application layer.** The client sends one-byte opcodes (arm, disarm, omit, outputs, log). The
   panel replies with JSON documents (panel, areas, inputs, outputs, user, faults, log).

The full specification is in [PROTOCOL.md](PROTOCOL.md).

## Features (first version)

| HA platform | What it shows |
|---|---|
| `alarm_control_panel` | One entity per panel on level-set panels: level A → *away*, B → *night*, C → *custom bypass*. State comes from the panel's area status codes. Arming uses force-omit, as the app does. Area-set panels get one entity per area. |
| `binary_sensor` | One per input (zone): open/closed. |
| `sensor` | Diagnostic: connection state and fault count. |
| Diagnostics | Download with all credentials and identifiers redacted. |

**Connection modes** (Settings → Devices & services → Pyronix Cloud → Configure):

- **On demand (default).** Every few minutes (default 2) and for each arm or disarm, Home
  Assistant opens a short session, reads the panel state, says goodbye and disconnects. The panel
  allows **one remote session at a time**, so the HomeControl2 app is blocked only for those few
  seconds.
- **Persistent ("live updates").** Keeps one session open for instant input and area changes, and
  reconnects with back-off. **While it is connected, the HomeControl2 app cannot log in.**

Planned: output switches, input omit, the event log and PIR-camera photos.

## Installation

You need Home Assistant 2026.2 or later.

### HACS (recommended)

[![Open your Home Assistant instance and open this repository in HACS.](https://my.home-assistant.io/badges/hacs_repository.svg)](https://my.home-assistant.io/redirect/hacs_repository/?owner=fregster&repository=ha-pyronix-integration&category=integration)

1. Click the button above, or in HACS add `https://github.com/fregster/ha-pyronix-integration`
   as a custom repository (category *Integration*). Then install **Pyronix Cloud**. HACS
   installs the `pyronix-cloud` library from PyPI.
2. Restart Home Assistant.
3. Add the **Pyronix Cloud** integration and enter the details below.

### Manual installation

1. Download this repository (a [release](https://github.com/fregster/ha-pyronix-integration/releases)
   or a ZIP of `main`) and copy `custom_components/pyronix_cloud` into the `custom_components`
   folder of your Home Assistant configuration directory.
2. Restart Home Assistant. It installs the `pyronix-cloud` library from PyPI on first start.
3. Add the **Pyronix Cloud** integration and enter the details below.

HACS tells you about new releases. With a manual install you have to check for them yourself.

## What you need

The same details HomeControl2 uses. Each one is listed with its label in the app:

- **Device name** (`AppUser`). This is a free-text name for the client, shown in the app under
  Settings > User Name. It defaults to the phone model and is *not* your e-mail.
- **System ID** (`PanelId`, 8 characters). You entered it when you added the panel, and the app shows it as "Panel ID:".
- **App password** (`PanelPwd`). The login screen asks for this. Your installer set it, and it is *not* your keypad code.
- **User code** (`UserCode`). The login screen asks for this too. It is your keypad code.
- An active Pyronix Cloud subscription for the panel

## Repository layout

```
PROTOCOL.md                 Reverse-engineered protocol specification
probe/                      Stand-alone read-only protocol test script (step 1)
src/pyronix_cloud/          Async client library, no HA dependency (published as pyronix-cloud)
src/pyronix_cloud/testing/  Fake cloud relay + panel for offline tests and dev instances
custom_components/pyronix_cloud/   Home Assistant integration
tests/                      Library tests; tests/integration/ holds the HA tests
apk/, decompiled/, reference/  Local-only reverse-engineering inputs (gitignored, never committed)
```

## Roadmap

1. ~~Probe~~: done; read-only live sessions answered most of PROTOCOL.md §13.
2. ~~Library~~ and ~~integration~~: first version done, tested offline.
3. Confirm a real set/unset sequence on a live panel (§13 Q12), then outputs, omit and the log.
4. Publish `pyronix-cloud` to PyPI, list in HACS, submit brand images; later, HA core.

## Developing against the fake panel

```bash
uv run python -m pyronix_cloud.testing --port 8765
```

This serves a fake cloud relay and panel on `ws://127.0.0.1:8765` and prints its test
credentials. In the config flow, open **Advanced** and set the cloud relay URL to it.

## Trying the probe

```bash
cp .env.example .env        # then fill in your panel details
# see probe/README.md for install and run instructions
```

`.env` is gitignored. Never commit credentials, captures or logs that contain them.

## Contributing / development

See [AGENTS.md](AGENTS.md) for conventions, safety rules and the development workflow. These apply
to human and AI contributors alike.

## Licence

- The `pyronix_cloud` library, the probe and the documentation are licensed under the
  [GNU LGPL v3.0 or later](LICENSES/LGPL-3.0-or-later.txt), which builds on the
  [GNU GPL v3.0](COPYING).
- The Home Assistant integration (`custom_components/`) is licensed under the
  [Apache License 2.0](LICENSES/Apache-2.0.txt).

Per-file licensing follows the [REUSE](https://reuse.software) specification
(see [REUSE.toml](REUSE.toml)).

## Legal

This project is for interoperability with hardware you own. The APK and decompiled sources are
used locally for analysis and are never redistributed.
