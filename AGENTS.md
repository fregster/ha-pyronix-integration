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
4. **Never commit** `apk/`, `decompiled/`, `reference/`, `.env`, `probe/.device_id`, packet
   captures, or logs that contain credentials or raw session keys.
5. Do not try to bypass `com.pairip`, Play integrity, or the cloud's subscription checks.

## Layout

| Path | Purpose |
|---|---|
| `PROTOCOL.md` | Protocol spec, with **Observed / Inferred / Unknown** evidence labels |
| `probe/` | Stand-alone, read-only protocol test script and offline tests |
| `src/pyronix_cloud/` | Async client library. **Must not import `homeassistant`.** |
| `custom_components/pyronix_cloud/` | HA integration, a thin layer over the library (Apache-2.0) |
| `tests/` | Library tests (LGPL); `tests/integration/` holds the HA tests (Apache-2.0, see REUSE.toml) |
| `decompiled/sources/` | jadx output, for reference only (gitignored). Main files: `com/pyronix/kotlinlibhomecontrol/{HomeControl,PanelEncrypt,PanelEncryptKt}.java` |
| `reference/` | Third-party downloads (vendor software such as InSite, manuals, firmware), one subfolder per item (gitignored) |

## Architecture

```
custom_components/pyronix_cloud
  config_flow.py      user/reauth/reconfigure/options; validates with one short read-only session
  client_factory.py   builds PyronixClient from the entry (HA's aiohttp session injected)
  coordinator.py      DataUpdateCoordinator: on-demand short sessions, one lock per panel
  live.py             persistent mode: one open session, pushes, reconnect with back-off
  alarm_control_panel.py / binary_sensor.py / sensor.py / diagnostics.py (redacted)
        │
        ▼
pyronix_cloud (library)                       sans-IO unless marked
  client.py           PyronixClient: connect / arm / disarm / close, listeners      (async)
  connection.py       drives a ProtocolSession over a WebSocket, guarantees goodbye (async)
  panel_feed.py       session events → snapshots; waits with deadlines             (async)
  websocket.py        WebSocket Protocol + aiohttp adapter                          (async)
  session.py          cloud stages → handshake → login → data mode (§3, §7)
    cloud_stages.py / panel_stages.py / session_events.py
  link.py             encrypted panel link: handshake, seqs, NACKs (§4)
    link_codec.py / link_nacks.py / link_events.py / announce.py
  cloud.py            <p> requests, <e> replies, heartbeat rule (§3.3–3.4)
  transport.py        (base64) wrapping and frame reassembly across messages (§3.2, §4.2)
  frames.py           frame layout, check16, seq acceptance (§4.1, §4.3)
  crypto.py           fixedKey, ECDH, AES-CFB-128 with ECB-derived IV (§4.4–4.7)
  commands.py         opcode payloads (§5)
  documents.py        untrusted JSON → typed documents (§6)  models.py  dataclasses/enums
  panel_state.py      merge + de-duplicate documents into PanelSnapshot
  area_rules.py / settings.py / backoff.py / errors.py
  testing/            FakeCloud + FakePanel (in-memory or loopback), canned live-shaped dump
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
- `cryptography`: CFB comes from `cryptography.hazmat.decrepit.ciphers.modes` when available
  (`modes.CFB` is deprecated). HA 2026.2 ships cryptography 46, which lacks it, so `crypto.py`
  resolves the class at runtime; keep that working.
- Follow HA integration quality guidelines: config flow, unique IDs (the panel ID plus the
  area/input/output number), `has_entity_name`, translations in `strings.json`, and no YAML
  config.

## Workflow

```bash
uv sync                                   # create .venv from uv.lock (Python >= 3.13)
uv run pre-commit install                 # once per clone
uv run pre-commit run --all-files         # every gate below, as CI runs them
uv run pytest                             # library + integration tests, coverage gate
uv run coverage report --include='custom_components/pyronix_cloud/config_flow.py' --fail-under=100

# Python 3.13 leg (HA 2026.2.3, the last HA release on 3.13; CI runs it too):
UV_PROJECT_ENVIRONMENT=.venv-313 uv sync --frozen --python 3.13
.venv-313/bin/python -m pytest && .venv-313/bin/python -m mypy

# Fake cloud for a dev Home Assistant (System ID TESTPANL; credentials printed):
uv run python -m pyronix_cloud.testing --port 8765
# Push the working tree to the local test HA OS host and restart it (PYRONIX_HA_HOST).
# A restart reloads the configured entry, which logs in to the real panel.
tools/deploy_test_ha.sh            # --no-restart to copy only
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
| File size | `tools/check_module_size.py` | line budgets per path; see *File focus* below |

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

## File focus (one file, one job)

Each module has **one responsibility**, and you can say what it is in one sentence. This keeps
files easy to review, test and hand to an agent with limited context.

- **The module docstring's first line states that responsibility**, e.g. `"""AES-CFB-128
  encryption and decryption for panel frames."""`. If the sentence needs "and" to join two
  unrelated jobs, the module needs splitting.
- **Read the docstring before adding code.** If the new code does not fit the stated job, put it
  in the module whose job it does fit, or create a new focused module. Never widen a docstring to
  make room.
- **Line budgets** (`[tool.module-size]` in `pyproject.toml`, checked by
  `tools/check_module_size.py` in pre-commit and CI): **300** lines for `src/` and
  `custom_components/`, **500** for `tests/`, **200** for `tools/`; `probe/` is exempt. Over
  budget means *split by responsibility*. Do not raise the limit, and do not squeeze code to fit
  (fewer blank lines, longer lines, dropped docstrings).
- **Split early.** Once a module passes ~80 % of its budget, plan the split before adding more.
- **No dumping grounds.** No `utils.py`, `helpers.py`, `common.py` or `misc.py`. Name modules
  after what they do (`frames.py`, `crypto.py`).
- **One test module per source module** (`tests/test_<module>.py`), split further by behaviour if
  it grows. Shared fixtures go in `conftest.py`.
- **Agents: keep each change to one concern.** Don't refactor unrelated code in passing; list it
  as a follow-up instead. When you create a module, state its responsibility in the docstring
  first, then write the code.

## Test environment notes

- HA dropped Python 3.13 in 2026.3. The `ha` dependency group pins
  `pytest-homeassistant-custom-component` per Python version (HA 2026.2.3 on 3.13, a current HA on
  3.14). Integration code must use only APIs present in **both**. Bump the pins deliberately.
- The HA pytest plugin loads for **every** test: it blocks sockets (use the in-memory
  `FakeCloud.connector()`; loopback tests need the `socket_enabled` fixture) and fails tests that
  leave tasks or timers behind.
- `tests/conftest.py` gives each test a current event loop on Python 3.13 only, working around
  the old HA plugin's sync fixtures. Remove it with 3.13 support.

## Working practices

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
