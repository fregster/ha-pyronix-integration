# Pyronix Cloud for Home Assistant

> **Status: pre-alpha / research.** The protocol has been worked out from the Android app
> (see [PROTOCOL.md](PROTOCOL.md)). It has **not yet been tested against a real panel**. Do not
> rely on this to protect your property.

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

## Planned features

| HA platform | Source | Notes |
|---|---|---|
| `alarm_control_panel` | One per area (`area` JSON) | Arm away / disarm; arming uses force-omit (configurable) |
| `binary_sensor` | One per input/zone (`input` JSON) | Open/closed, with omitted, problem and areas as attributes |
| `switch` | One per user output (`output` JSON) | "Automated controls" |
| `button` / service | Input omit / un-omit | Off by default |
| `sensor` | Faults, panel info, connection state | Diagnostic |
| Event / logbook | Panel event log | On demand |

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
src/pyronix_cloud/          Async client library, no HA dependency (planned)
custom_components/pyronix_cloud/   Home Assistant integration (planned)
tests/                      Library and integration tests (planned)
apk/, decompiled/           Local-only reverse-engineering inputs (gitignored, never committed)
```

## Roadmap

1. **Probe** (`probe/`). Connect, handshake, log in and dump the panel JSON. Read-only. Answers
   the open questions in PROTOCOL.md §13.
2. **Library** (`pyronix_cloud`). An asyncio client with a persistent connection, reconnect with
   back-off, heartbeats, typed models for each JSON `type`, a command API and a fake panel for
   tests.
3. **Integration.** Config flow, a DataUpdateCoordinator driven by pushed panel data, entities,
   diagnostics with redaction, and translations.
4. **Distribution.** HACS-compatible repository; library optionally published to PyPI.

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
