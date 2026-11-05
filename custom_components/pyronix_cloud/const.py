# SPDX-FileCopyrightText: 2026 Paul Fryer
# SPDX-License-Identifier: Apache-2.0
"""Constants shared by the Pyronix Cloud integration's modules."""

from __future__ import annotations

from enum import StrEnum
from typing import Final

from homeassistant.components.alarm_control_panel.const import AlarmControlPanelState

DOMAIN: Final = "pyronix_cloud"

# Config entry data keys (the app's labels are in strings.json).
CONF_APP_USER: Final = "app_user"
"""Device name: the name the cloud shows for this client (``AppUser``)."""
CONF_PANEL_ID: Final = "panel_id"
"""System ID: the panel's 8-character ID (``PanelId``); also the unique ID."""
CONF_APP_PASSWORD: Final = "app_password"  # noqa: S105 - a key name, not a secret
"""App password: keys the panel encryption (``PanelPwd``)."""
CONF_USER_CODE: Final = "user_code"
"""User code: the keypad code sent after the encrypted session is up."""
CONF_DEVICE_ID: Final = "device_id"
"""Random ID of this client, generated once when the entry is created."""
CONF_CLOUD_URL: Final = "cloud_url"
"""Cloud relay URL; stored only when it is not the live relay."""
CONF_ADVANCED: Final = "advanced"
"""The user step's collapsed section that holds the relay URL (never stored)."""
CONF_LIVE_UPDATES: Final = "live_updates"
"""User step tick box that starts the entry in persistent mode (never stored)."""

DEFAULT_APP_USER: Final = "Home Assistant"
PANEL_ID_LENGTH: Final = 8
DEVICE_ID_BYTES: Final = 8

# Options.
CONF_MODE: Final = "mode"
MODE_ON_DEMAND: Final = "on_demand"
MODE_PERSISTENT: Final = "persistent"
CONF_SCAN_INTERVAL: Final = "scan_interval"
"""Minutes between on-demand polls."""
DEFAULT_SCAN_INTERVAL: Final = 2
MIN_SCAN_INTERVAL: Final = 1
MAX_SCAN_INTERVAL: Final = 60

# Who may arm and disarm (options; chosen during setup and in the options flow).
CONF_ARM_ACCESS: Final = "arm_access"
"""Which check guards arm and disarm; an entry without it allows anyone."""
CONF_PIN: Final = "pin"
"""Form field for a new PIN; only its salted hash is stored."""
CONF_PIN_HASH: Final = "pin_hash"
"""Salted scrypt hash of the PIN (see pin_hash.py)."""
CONF_USERS: Final = "users"
"""IDs of the Home Assistant users who may arm and disarm."""
CONF_ALLOW_AUTOMATIONS: Final = "allow_automations"
"""In users mode, also allow calls with no user (automations and scripts)."""
CONF_ALWAYS_FORCE_ARM: Final = "always_force_arm"
"""Make the normal arm actions force-arm (omit open inputs)."""
PIN_MIN_LENGTH: Final = 4
PIN_MAX_LENGTH: Final = 8

SERVICE_FORCE_ARM: Final = "force_arm"
ATTR_MODE: Final = "mode"

LEVEL_MODES: Final[dict[str, AlarmControlPanelState]] = {
    "A": AlarmControlPanelState.ARMED_AWAY,
    "B": AlarmControlPanelState.ARMED_NIGHT,
    "C": AlarmControlPanelState.ARMED_CUSTOM_BYPASS,
}
"""Home Assistant arm mode of each set level, by area letter."""

FORCE_ARM_MODES: Final[dict[str, AlarmControlPanelState]] = {
    "away": AlarmControlPanelState.ARMED_AWAY,
    "night": AlarmControlPanelState.ARMED_NIGHT,
    "custom_bypass": AlarmControlPanelState.ARMED_CUSTOM_BYPASS,
}
"""The ``mode`` field of the force_arm action and the arm mode it stands for."""


class ArmAccess(StrEnum):
    """Who may arm and disarm the panel from Home Assistant."""

    PIN = "pin"
    USERS = "users"
    ANYONE = "anyone"


class ConnectionState(StrEnum):
    """State of the link to the panel, shown by the diagnostic sensor."""

    IDLE = "idle"
    CONNECTING = "connecting"
    CONNECTED = "connected"
    ERROR = "error"
