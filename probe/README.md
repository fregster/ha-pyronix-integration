# Pyronix Cloud probe

A single-file, **read-only** test of the protocol described in `../PROTOCOL.md`. It connects
to the Pyronix cloud relay, runs the panel handshake, logs in with your user code, and then
prints every JSON document the panel sends. After `--duration` seconds it sends `E` and
closes the socket with code 1001.

The only panel opcodes it can send are heartbeat `0x20` and, with `--request-log N`, the log
request `0x35`. There is no code for arm, disarm, omit or output opcodes, and
`PanelSession.data()` refuses any other opcode.

## Install

```sh
cd /Users/pfrye/git/ha-pyronix-intergration
uv venv .venv && uv pip install -p .venv/bin/python -r probe/requirements.txt
```

## Configure

Fill in `.env` at the repo root (copy it from `.env.example`). It needs `PYRONIX_APP_USER`,
`PYRONIX_PANEL_ID` (the 8-character System ID), `PYRONIX_PANEL_PASSWORD` (the "App password"
from the HomeControl2 login screen), `PYRONIX_USER_CODE` and `PYRONIX_PUSH_ID`. If `PYRONIX_DEVICE_ID` is blank, the probe
generates one and saves it to `probe/.device_id`, which is gitignored. Values already set in
the process environment take precedence over `.env`.

## Run

```sh
.venv/bin/python -m pytest probe/ -q                 # offline tests
.venv/bin/python probe/pyronix_probe.py --dry-run    # print outbound messages, no network
.venv/bin/python probe/pyronix_probe.py -v --duration 60 2>&1 | tee probe/run.log   # live
```

Useful flags:

| Flag | Effect |
|---|---|
| `--request-log 10` | After the first JSON document, send the log request (`0x35`) for 10 entries. |
| `--no-b64-wrap` | Send base64 without Android's 76-column newlines. The app always wraps. |
| `--save-app-pwd` | Send the hashed `<z>` value. The app always sends the plaintext password. |
| `--no-nack` | Never send `C` frames. |
| `--step-timeout` | Seconds to wait at each handshake step. Default 30. |

Exit codes: 0 ok, 1 config, 2 cloud error (`a`/`b`/`i`/`n`/unknown), 3 panel/handshake,
4 timeout, 5 websocket/connect.

In `-v` logs, the panel password, the user code and every `<z>…</z>` value are replaced with
`[REDACTED]`. For a base64 message that contains `<z>`, the log shows only the base64 length,
not the base64 text. **Still review `run.log` before sharing it.**

## What a live run will tell us (PROTOCOL.md §13)

| # | Question | Where to look in the output |
|---|---|---|
| 1 | Does the cloud validate `PushId` / `DeviceId` / `AppUser`? | Whether we get `w`/`j`/`u` with a dummy push ID, or an `a` error |
| 2 | The `<e>` separator and the `w`/`m`/`j` data | `<- cloud reply 'w' sep=',' data=…` lines |
| 3 | `AppUser`: account e-mail or panel user name? | Try each one; `a` (access denied) vs. success |
| 4 | Wire encoding of `SetType` | The printed `area` JSON |
| 5 | Does the panel push status changes by itself? | Run with `--duration 300` and open a door |
| 6 | Use of `p[0]` and of the `b` frame's first byte | `panel announce: … p0=…` and `b[0]=…` |
| 8 | `JSON_ORIG` vs. `JSON_NG_V1` | `json_version=` (`announce_extra[0]`, from `getJsonVersion()`) |
| 10 | Is a subscription needed? | An `i` error |

The run also shows whether the cloud relays panel frames as `(base64)` text or as raw binary
(`RX frame form=b64|raw`), the uplink type (`modem=`), whether the key is compressed, and any
NACKs.
