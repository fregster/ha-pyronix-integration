# SPDX-FileCopyrightText: 2026 Paul Fryer
# SPDX-License-Identifier: Apache-2.0
"""Check config flow input before any connection is made, without echoing secrets."""

from __future__ import annotations

from typing import TYPE_CHECKING, Any, Final
from urllib.parse import urlsplit

from .const import (
    CONF_APP_PASSWORD,
    CONF_APP_USER,
    CONF_PANEL_ID,
    CONF_USER_CODE,
    PANEL_ID_LENGTH,
)

if TYPE_CHECKING:
    from collections.abc import Mapping

MAX_USER_CODE_LENGTH: Final = 8
_LOCAL_HOSTS: Final = frozenset({"localhost", "127.0.0.1"})
# The protocol does no XML escaping, so the library rejects these (PROTOCOL.md §3.3).
_FORBIDDEN_CHARS: Final = frozenset("<>&")


def normalise_system_id(value: str) -> str:
    """Return a System ID as the app stores it: trimmed and upper-cased."""
    return value.strip().upper()


def url_allowed(url: str) -> bool:
    """Accept ``wss://`` for any host, and ``ws://`` only for this machine."""
    try:
        parts = urlsplit(url)
    except ValueError:
        return False
    if parts.scheme == "wss":
        return bool(parts.hostname)
    return parts.scheme == "ws" and parts.hostname in _LOCAL_HOSTS


def user_code_valid(code: str) -> bool:
    """Whether a keypad user code is 1 to 8 ASCII digits."""
    return 0 < len(code) <= MAX_USER_CODE_LENGTH and code.isascii() and code.isdigit()


def field_errors(user_input: Mapping[str, Any]) -> dict[str, str]:
    """Return form errors, by field, for the credential fields present.

    Args:
        user_input: Submitted fields; the System ID must already be normalised.

    Returns:
        Error keys by field name; empty when every present field is valid.
    """
    errors: dict[str, str] = {}
    panel_id = user_input.get(CONF_PANEL_ID)
    if panel_id is not None and len(panel_id) != PANEL_ID_LENGTH:
        errors[CONF_PANEL_ID] = "invalid_system_id"
    for name in (CONF_APP_USER, CONF_APP_PASSWORD):
        value = user_input.get(name)
        if value is not None and _FORBIDDEN_CHARS.intersection(value):
            errors[name] = "invalid_characters"
    code = user_input.get(CONF_USER_CODE)
    if code is not None and not user_code_valid(code):
        errors[CONF_USER_CODE] = "invalid_user_code"
    return errors
