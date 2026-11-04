# AGENTS.md

Guidance for AI coding agents (and humans) working in this repository.

## What this is

This repository is an unofficial Home Assistant integration for Pyronix alarm panels. It connects
through the Pyronix Cloud WebSocket relay. The protocol was reverse-engineered from the
HomeControl2 Android app and is specified in [PROTOCOL.md](PROTOCOL.md). **PROTOCOL.md is the
source of truth.** When code and spec disagree, find out why, then fix whichever is wrong and
record it.

This repo is its own git repository, kept inside a parent folder alongside other repositories. Do not touch sibling repositories.

## Safety rules (non-negotiable)

This software talks to a **real intruder alarm**. Mistakes can set off sirens, leave a home
unprotected, or lock out a user.

1. **No live state-changing commands without explicit approval in the current conversation.**
   This covers set/unset areas (`0x2D`/`0x2F`), output operate (`0x31`), input omit (`0x34`) and
   take-photo (`G`). Read-only actions (connect, login, heartbeat, log request `0x35`) are fine
   once the user has supplied credentials.
2. **Never make a live connection to `pyronixcloud.com` unless the user asks you to.** Repeated
   failed logins may lock the panel or trigger tamper/alerts. If a run fails, stop and report.
   Do not retry in a loop.
3. **Secrets:** `.env` holds the panel app password and keypad user code.
   - Never print, log, commit or paste these values.
   - Redact them in logs (`PanelPwd`, `<z>…</z>`, the login payload `",code\n"`).
   - HA diagnostics must use `async_redact_data`.
4. **Never commit** `apk/`, `decompiled/`, `.env`, `probe/.device_id`, packet captures, or logs
   that contain credentials or raw session keys.
5. Do not try to bypass `com.pairip`, Play integrity, or the cloud's subscription checks.

## Layout

| Path | Purpose |
|---|---|
| `PROTOCOL.md` | Protocol spec, with **Observed / Inferred / Unknown** evidence labels |
| `probe/` | Stand-alone, read-only protocol test script and offline tests |
| `src/pyronix_cloud/` | *(planned)* Async client library. **Must not import `homeassistant`.** |
| `custom_components/pyronix_cloud/` | *(planned)* HA integration, a thin layer over the library |
| `tests/` | *(planned)* pytest suites for the library and the integration |
| `decompiled/sources/` | jadx output, for reference only (gitignored). Main files: `com/pyronix/kotlinlibhomecontrol/{HomeControl,PanelEncrypt,PanelEncryptKt}.java` |

## Architecture (target)

```
custom_components/pyronix_cloud
  config_flow.py      user step: device name, system ID, App password, user code; validates by connecting
  coordinator.py      DataUpdateCoordinator: polls by short sessions (default) or takes pushes (persistent mode)
  alarm_control_panel.py / binary_sensor.py / switch.py / sensor.py
  diagnostics.py      redacted
        │
        ▼
pyronix_cloud (library)
  cloud.py            WebSocket relay layer: <p> messages, <e> replies, heartbeats (§3)
  frames.py           frame build/parse, seq numbers, NACK handling, transport encoding (§4.1–4.3)
  crypto.py           fixedKey, ECDH, AES-CFB-128 with ECB-derived IV, check16 (§4.4–4.7)
  session.py          state machine: cloud stages → handshake → data mode; reconnect + back-off
  commands.py         opcode builders (§5)
  models.py           dataclasses for panel/area/input/output/user/fault/log (§6)
  testing/fake_panel  in-process fake cloud + panel for tests
```

**Design principles**

- **asyncio only.** Use `aiohttp` for the WebSocket; HA already ships it, and the HA session can be
  passed in. No blocking I/O on the event loop.
- **Two connection modes; on-demand is the default.** The panel allows only one remote session at a
  time, so a permanent connection locks out the HomeControl2 app (PROTOCOL.md §11, §13 Q11).
  - *On-demand (default):* short sessions (connect → state dump → goodbye `E` → close) on a poll
    interval and for each arm/disarm. Keep each session to a few seconds.
  - *Persistent (opt-in "live updates" option):* one long-lived connection with pushed updates,
    and reconnect with exponential back-off. The options flow must warn that this blocks the app.
  Both modes share the same session code; the coordinator only chooses when to connect.
- **Always say goodbye.** Every exit path, including cancellation and HA shutdown, sends `E` and
  closes the WebSocket.
- **Pure functions for the protocol** (framing, crypto, message builders and parsers), so they can
  be unit-tested without a network.
- **The library has no knowledge of HA.** Typed models and a small client API:
  `connect()`, `arm(areas, force=True)`, `disarm(areas)`, `set_output()`, `omit()`, `close()`.
- `cryptography`: import CFB from `cryptography.hazmat.decrepit.ciphers.modes` when available
  (`modes.CFB` is deprecated).
- Follow HA integration quality guidelines: config flow, unique IDs (the panel ID plus the
  area/input/output number), `has_entity_name`, translations in `strings.json`, and no YAML
  config.

## Workflow

```bash
uv sync                                   # create .venv from uv.lock (Python >= 3.13)
uv run pre-commit install                 # once per clone
uv run pre-commit run --all-files         # every gate below, as CI runs them
uv run pytest                             # library tests + coverage gate
uv run pytest probe --no-cov -o addopts=""   # probe's offline tests, no network
uv run python probe/pyronix_probe.py --dry-run
```

## Quality gates (all enforced in pre-commit and CI; a red gate blocks the merge)

| Gate | Tool | Rule |
|---|---|---|
| Lint | `ruff check` | `select = ["ALL"]`; every ignore in `pyproject.toml` carries a reason |
| Format | `ruff format` | line length **88**, Google-style docstrings on public API |
| Complexity | ruff `C901`, `PLR09xx` | complexity ≤ 10, branches ≤ 12, returns ≤ 6, args ≤ 5, statements ≤ 50 |
| Types | `mypy --strict` | plus `warn_unreachable` and extra error codes; library ships `py.typed` |
| Tests | `pytest`, `pytest-cov`, `hypothesis` | branch coverage ≥ 95 % for the library; 100 % for `config_flow.py` |
| Secrets | `gitleaks`, `detect-private-key` | no codes, System IDs or keys in git history |
| Licences | `reuse lint` | every file has SPDX info (header or `REUSE.toml`) |
| Spelling | `codespell` | |

- **No silencing without a reason.** `# noqa: CODE` and `# type: ignore[code]` need the rule
  code and a short comment saying why. Prefer fixing the design: table-driven parsers and small
  functions keep within the complexity limits.
- **Untrusted input is parsed at the boundary.** Panel JSON and frames become typed dataclasses
  in one place; nothing downstream sees raw `dict[str, Any]`. Frame parsing gets hypothesis
  property tests.
- **`probe/` is exempt from formatting and most lint rules.** It is a frozen research tool;
  port its logic into `src/` properly instead of polishing it.
- **Licensing:** `src/`, `tests/`, `probe/` and docs are **LGPL-3.0-or-later** (GPL text in
  `COPYING`); `custom_components/` and `hacs.json` are **Apache-2.0**, so the integration can
  move to HA core unchanged. New source files start with the two SPDX header lines.
- **Distribution:** HACS first. Keep the HA core route open: library publishable to PyPI,
  integration written to the Platinum level of the HA Integration Quality Scale.

- Write or extend offline tests **before** changing protocol code. Port Java logic line for line
  in tests when you need to prove equivalence (as was done for CFB).
- When a live capture answers an open question in PROTOCOL.md §13, update PROTOCOL.md:
  - Change the label from Inferred/Unknown to Observed.
  - Add the date.
  - Strip any secrets from the excerpts you include.
- Reference decompiled code as `path/File.java:line`, relative to `decompiled/sources/`.
- Commit only when the user asks. Use small, focused commits.

## Open questions to keep in mind

See PROTOCOL.md §13. The ones most likely to bite:
- Whether the cloud validates `PushId`/`DeviceId`.
- The `<e>` separator character.
- Whether the panel pushes changes on its own after the initial dump.
