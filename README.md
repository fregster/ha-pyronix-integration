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

## What you can use it for

- **See the alarm at a glance.** Show on a dashboard whether the alarm is set, which level or
  area is set, and whether any input is open, wherever you are.
- **Get told when something happens.** Send a notification when the alarm is set, unset or goes
  off, or when the panel reports a fault.
- **Arm with the rest of your home.** Set the night level at bedtime, or remind yourself (or
  arm) when everyone has left.
- **Use the alarm's sensors.** Door contacts and PIRs become binary sensors. With live updates
  on they react within about a second, so they can drive lights or heating; in the default
  on-demand mode they only update every few minutes.

See [Automation examples](#automation-examples) for ready-made YAML.

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

## Features

| HA platform | What it shows |
|---|---|
| `alarm_control_panel` | One entity per panel on level-set panels: level A → *away*, B → *night*, C → *custom bypass*. State comes from the panel's area status codes. Arming stops with the reason if an input is open; the *Force arm* action omits open inputs, as the app does (see [Arming and force arming](#arming-and-force-arming)). Area-set panels get one entity per area. |
| `binary_sensor` | One per input (zone): open/closed. |
| `sensor` | Diagnostic: connection state (disabled by default) and fault count. |
| Diagnostics | Download with all credentials and identifiers redacted. |

Planned: output switches, input omit, the event log and PIR-camera photos.

## How data is updated

There are two connection modes. You choose one in the options (see
[Configuration options](#configuration-options)).

- **On demand (default).** Every few minutes (default 2) and for each arm or disarm, Home
  Assistant opens a short session, reads the panel state, says goodbye and disconnects. The panel
  allows **one remote session at a time**, so the HomeControl2 app is blocked only for those few
  seconds. Changes show up at the next poll.
- **Persistent ("live updates").** Keeps one session open for instant input and area changes, and
  reconnects with back-off (5 seconds, doubling up to 5 minutes). If nothing at all arrives from
  the cloud for a little over two minutes, the session is treated as dead and reconnects.
  **While it is connected, the HomeControl2 app cannot log in.**

Straight after a session ends, the Pyronix Cloud sometimes reports the panel as "not polling" for
about 35 seconds. Home Assistant then waits and opens the connection again, up to 4 more times, 10
seconds apart. Only the connection is repeated, never a command.

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

Setup also offers a **Live updates** tick box, which starts the entry in persistent mode, and an
**Advanced options** section with the cloud relay URL. Leave the URL alone; it is only for testing
against a fake relay. Home Assistant checks the details with one short, read-only session before
it saves them. The last setup step asks who may arm and disarm (see below).

## Configuration options

Change these under Settings → Devices & services → Pyronix Cloud → **Configure**. Saving reloads
the integration.

| Option | What it does | Values |
|---|---|---|
| **Connection mode** | How Home Assistant talks to the panel (see [How data is updated](#how-data-is-updated)). | *On-demand* (default, recommended) or *Persistent*. Persistent locks out the HomeControl2 app. |
| **Poll interval** | Minutes between polls in on-demand mode. Persistent mode ignores it. | 1 to 60 minutes, default 2. |
| **Who can arm and disarm** | Which check Home Assistant makes before it arms or disarms (see below). | *PIN* (default for new entries), *Selected users* or *Anyone*. |
| **PIN** | The PIN asked for in PIN mode. | 4 to 8 digits. Leave empty to keep the current PIN. |
| **Users** | In users mode, the Home Assistant users who may arm and disarm. | One or more active users. |
| **Allow automations and scripts** | In users mode, also allow automations and scripts, which act without a user. | Off by default. |
| **Always force arm** | Make the normal arm actions omit open inputs and arm anyway, as the app does. | Off by default. |

To change the device name, app password or user code, choose **Reconfigure** from the entry's
menu. The System ID cannot change; add a new entry for another panel.

### Who can arm and disarm

Home Assistant arms and disarms with the keypad user code you entered during setup, so the
integration adds its own check on top. An admin chooses it in the last setup step and can change
it later under Settings → Devices & services → Pyronix Cloud → Configure:

- **PIN (default).** Every arm, disarm and force arm asks for a PIN of 4 to 8 digits. You choose
  it in Home Assistant; it may be the same number as your keypad code, but it is a separate
  setting. Home Assistant stores only a salted hash of it (scrypt), never the PIN itself, and
  diagnostics redact the hash. Automations and scripts must send the PIN as `code`. To change
  the PIN, enter a new one in the options; leave the field empty to keep the current one.
- **Selected users.** Only the Home Assistant users you pick may arm and disarm; no PIN is asked
  for. Automations and scripts act without a user, so they are refused unless you also tick
  **Allow automations and scripts**.
- **Anyone.** No check: every Home Assistant user, automation and script can disarm the alarm.

A refused call fails with a message saying why (wrong PIN, user not allowed, automations not
allowed) and nothing is sent to the panel.

Entries created before this option existed keep working as **Anyone**, so an upgrade breaks no
automations. Home Assistant shows a repair issue for them until an admin saves the options with a
choice.

## Arming and force arming

The normal arm actions (*Arm away*, and on level-set panels *Arm night* and *Arm custom bypass*)
arm **without force**: if an input is open the panel will not set, and the action fails with the
panel's reason, for example "Full Set cannot be armed: Landing DT Open". Home Assistant checks the
area before sending anything, and also stops at once if the panel refuses after the command. While
the exit timer runs the entity shows *arming*.

To arm anyway, use the **Force arm** action (`pyronix_cloud.force_arm`). It omits open inputs
until the next unset, as the HomeControl2 app always does. It takes an optional `code` (the PIN)
and, on level-set panels, a `mode` (`away`, `night` or `custom_bypass`, default `away`); area
entities ignore `mode`.

```yaml
action: pyronix_cloud.force_arm
target:
  entity_id: alarm_control_panel.pyronix_alarm
data:
  mode: night
  code: "4711"
```

To make the normal arm actions behave like the app, turn on **Always force arm** in the options.

A forced set can still be refused: if the panel cancels it or reports that the area cannot be
set, the action fails at once with the panel's reason. Arming an area that is already set (or
setting, or in alarm) sends nothing to the panel.

The integration never repeats an arm or disarm on its own. If a command fails, it reports the
error and leaves the next step to you.

## Automation examples

Entity IDs follow your panel's site name and input names. The examples assume a level-set panel
named "Pyronix alarm" (the name used when the panel has no site name). Check your own IDs under
Settings → Devices & services → Pyronix Cloud. In PIN mode, automations must send the PIN as
`code`; in users mode they need **Allow automations and scripts**.

**Tell me when the alarm goes off.**

```yaml
alias: Alarm going off
triggers:
  - trigger: state
    entity_id: alarm_control_panel.pyronix_alarm
    to: triggered
actions:
  - action: notify.notify
    data:
      title: Alarm
      message: The house alarm is going off.
```

**Remind me when everyone has left and the alarm is not set.**

```yaml
alias: Alarm not set
triggers:
  - trigger: numeric_state
    entity_id: zone.home
    below: 1
    for: "00:10:00"
conditions:
  - condition: state
    entity_id: alarm_control_panel.pyronix_alarm
    state: disarmed
actions:
  - action: notify.notify
    data:
      message: Everyone has left and the alarm is not set.
```

**Set the night level at bedtime.** It fails with the reason if a door or window is open; use
`pyronix_cloud.force_arm` instead to omit open inputs. Keep the PIN in `secrets.yaml`.

```yaml
alias: Night set at bedtime
triggers:
  - trigger: time
    at: "23:30:00"
conditions:
  - condition: state
    entity_id: alarm_control_panel.pyronix_alarm
    state: disarmed
actions:
  - action: alarm_control_panel.alarm_arm_night
    target:
      entity_id: alarm_control_panel.pyronix_alarm
    data:
      code: !secret pyronix_pin
```

**Tell me about panel faults** (for example mains failure or a low battery).

```yaml
alias: Alarm panel fault
triggers:
  - trigger: numeric_state
    entity_id: sensor.pyronix_alarm_faults
    above: 0
actions:
  - action: notify.notify
    data:
      message: >
        The alarm panel reports {{ states('sensor.pyronix_alarm_faults') }} fault(s).
        Check the keypad or the HomeControl2 app.
```

**Turn on the hall light when the hall PIR sees movement** (only useful with live updates on).

```yaml
alias: Hall light on movement
triggers:
  - trigger: state
    entity_id: binary_sensor.pyronix_alarm_hall_pir
    to: "on"
actions:
  - action: light.turn_on
    target:
      entity_id: light.hall
```

## Known limitations

- **One remote session at a time.** The panel lets only one remote client in. In persistent mode
  Home Assistant holds that session, so the HomeControl2 app (yours and everyone else's) cannot
  log in. In on-demand mode the app is blocked for a few seconds at each poll. If the app is in
  use when Home Assistant polls, that poll fails with "panel busy", and the reverse.
- **Cloud only.** Everything goes through the Pyronix Cloud, as the app does. It needs internet
  access at home and at the panel, a working Pyronix Cloud and an active subscription for the
  panel. There is no local connection.
- **On-demand mode is not real time.** A change shows up at the next poll, up to the poll
  interval later. Use persistent mode if you need instant updates, and accept that it locks out
  the app.
- **Not a monitored alarm.** Do not rely on Home Assistant to tell you about a break-in. Keep
  your alarm's own signalling or monitoring.
- **Arming and disarming are not yet tested on a real panel.** Read-only sessions are tested on a
  Euro 46. Other models (Enforcer) and other firmware are untested.
- **One command at a time.** A second arm or disarm while the first is still running fails with
  an error saying the panel is already busy with another command.
- **No automatic retries of commands.** If the panel does not confirm a command, the integration
  does not know whether it worked, and leaves the next step to you.
- **Commands may wait.** Straight after a session, the cloud can refuse a new one for about 35
  seconds (see [How data is updated](#how-data-is-updated)), so an arm or disarm can take that
  much longer.
- **Not every feature yet.** Outputs, input omit, the event log and PIR-camera photos are not
  supported.
- **Guessed sensor types.** An input's device class (motion, door, smoke) is guessed from its type
  and name. Change it in the entity settings if it is wrong.
- **Removed inputs stay.** Inputs that the panel no longer reports are not removed
  automatically. Delete their entities yourself.
- **The cloud sees your app password.** Like the app, the integration sends the panel's app
  password to the Pyronix Cloud inside TLS (see [PROTOCOL.md](PROTOCOL.md) §12).
- **Unofficial.** The protocol was worked out from the app. A Pyronix change to the cloud or the
  app could break the integration without notice.

## Troubleshooting

Many errors appear in two places: as a message when setup or an action fails, and, for a poll,
as "Failed to read the panel state (*ErrorName*)" in the log while the entities are unavailable.
Both are listed below.

### The panel rejected the System ID, app password or user code

Shown during setup (`invalid_auth`), or as a request to re-authenticate the integration
(`auth_failed`, `AuthenticationError` or `AccessDeniedError`).

1. Check the details against the HomeControl2 app. The app password is set by your installer and
   is *not* your keypad code. The System ID is 8 characters.
2. For an existing entry, Home Assistant shows a re-authentication prompt under Settings →
   Devices & services. Enter the correct app password and user code there.
3. Do not try again and again. Repeated failed logins may lock the panel or raise a tamper alert.

### The panel is busy with another remote session

`panel_busy`, or `PanelBusyError` in the log. Another client holds the panel's single remote
session, usually the HomeControl2 app, or another Home Assistant using persistent mode.

1. Close the app (or log out of it) and wait a minute.
2. Try again, or wait for the next poll.
3. If this happens often, use on-demand mode everywhere, or a longer poll interval.

### The panel is not connected to the Pyronix Cloud

`panel_offline`, or `PanelOfflineError` in the log. The cloud says the panel is not polling it.

1. Straight after a session this can be the cloud's ~35-second pause. Home Assistant waits and
   tries again on its own; check whether the next poll works.
2. If it persists, check that the app can reach the panel. If it cannot, check the panel's
   internet module (for example DIGI-WiFi or DIGI-LAN): power, network cable or Wi-Fi, and the
   router.

### The panel's Pyronix Cloud subscription has expired

`subscription_expired`, or `SubscriptionExpiredError` in the log. Renew the subscription through
the app or your installer, then reload the integration.

### Failed to connect to the Pyronix Cloud

`cannot_connect`, or `CommunicationError`, `PyronixTimeoutError`, `ConnectionClosedError` or
`ProtocolError` in the log. Home Assistant could not reach the cloud, or the session broke off.
Check Home Assistant's internet access and DNS, and that outgoing HTTPS (port 443) to
`app.pyronixcloud.com` is allowed. A single failure is usually temporary; the next poll or
reconnect tries again.

### Arming fails: "*area* cannot be armed: *reason*"

`arm_refused`. The panel will not set the area: it is not ready, or it cancelled the set. The
reason is what the panel reports, for example "Landing DT Open".

1. Close the door or window, or fix the fault, and arm again.
2. Or use **Force arm** (or **Always force arm**) to omit open inputs. The panel can still cancel
   a forced set, for example because of a fault that cannot be omitted; the message then gives
   the panel's reason.

### The panel did not confirm the command

`command_not_confirmed`. Home Assistant sent the arm or disarm, but the panel did not report the
change in time, or the connection dropped after the command went out. **The command may still
have reached the panel.**

1. Check the panel's state first: wait for the next poll, or look at the keypad or the app.
2. Only then decide whether to send the command again. Never repeat it blindly, and do not build
   automations that retry it in a loop.

### The panel is already busy with another command

Another arm or disarm is still running. Wait for it to finish, then check the state before you
send another.

### The user code is not allowed to set or unset these areas

`not_permitted`. The keypad user code in the entry does not have rights to those areas. Ask your
installer or master user to give that user the areas, or reconfigure the entry with a user code
that has them. **This arm mode is not available** (`mode_not_supported`) means the level is not
in use, or the user code may not set it.

### The PIN is missing or wrong / user or automations not allowed

`invalid_code`, `user_not_allowed` or `automation_not_allowed`. Home Assistant's own check refused
the call and sent nothing to the panel. Enter the PIN, send it as `code` from automations, or
change [Who can arm and disarm](#who-can-arm-and-disarm) in the options.

### The panel session is not connected

`not_connected`. Persistent mode is reconnecting. Wait for it; the **Connection state**
diagnostic sensor (enable it first) shows when it is back.

### Repair notice "Choose who can arm and disarm"

The entry was created before the access option existed, so anyone can arm and disarm. Open the
options, choose an access rule and save.

### The HomeControl2 app cannot log in

Persistent mode holds the panel's only remote session. Switch to on-demand mode, or accept that
the app cannot be used while Home Assistant is connected.

### Debug logs and diagnostics

To capture a debug log, open Settings → Devices & services → Pyronix Cloud, choose **Enable debug
logging**, reproduce the problem, then choose **Disable debug logging** to download the log. Or
add this to `configuration.yaml` and restart:

```yaml
logger:
  default: warning
  logs:
    custom_components.pyronix_cloud: debug
    pyronix_cloud: debug
```

The integration never logs the app password, the user code or the PIN. Logs can still contain
area and input names, so read them before you share them.

To download diagnostics, open the panel's device page and choose **Download diagnostics**.
Credentials, the System ID, the site name, the PIN hash and the names of people in the panel
state are redacted. Attach the diagnostics and the log to an
[issue](https://github.com/fregster/ha-pyronix-integration/issues).

## Removing the integration

1. Go to Settings → Devices & services → Pyronix Cloud, open the entry's menu (⋮) and choose
   **Delete**. Home Assistant says goodbye to the panel and closes the session. The stored
   credentials, the PIN hash and the entities go with the entry.
2. If you installed with HACS, open HACS, find **Pyronix Cloud**, choose **Remove** from its menu,
   and restart Home Assistant. For a manual install, delete
   `custom_components/pyronix_cloud` from your configuration directory and restart.
3. Edit or delete automations and scripts that use its entities or `pyronix_cloud.force_arm`.

Nothing is left on the panel: the integration changes no panel settings and installs nothing on
it, so the HomeControl2 app works as before. Past sessions stay in the panel's event log under the
device name you chose.

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
4. ~~Publish `pyronix-cloud` to PyPI~~ (0.1.0 is out); list in HACS, submit brand images;
   later, HA core.

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
