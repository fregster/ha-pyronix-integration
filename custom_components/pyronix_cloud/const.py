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

LEVEL_MODES: Final[dict[str, AlarmControlPanelState]] = {
    "A": AlarmControlPanelState.ARMED_AWAY,
    "B": AlarmControlPanelState.ARMED_NIGHT,
    "C": AlarmControlPanelState.ARMED_CUSTOM_BYPASS,
}
"""Home Assistant arm mode of each set level, by area letter."""


class ConnectionState(StrEnum):
    """State of the link to the panel, shown by the diagnostic sensor."""

    IDLE = "idle"
    CONNECTING = "connecting"
    CONNECTED = "connected"
    ERROR = "error"
