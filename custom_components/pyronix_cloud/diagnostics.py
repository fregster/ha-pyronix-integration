# SPDX-FileCopyrightText: 2026 Paul Fryer
# SPDX-License-Identifier: Apache-2.0
"""Build the redacted diagnostics download of a config entry."""

from __future__ import annotations

from collections.abc import Callable, Mapping
import dataclasses
from datetime import datetime
import enum
from typing import TYPE_CHECKING, Any, Final

from homeassistant.components.diagnostics import async_redact_data

from .const import (
    CONF_APP_PASSWORD,
    CONF_APP_USER,
    CONF_CLOUD_URL,
    CONF_DEVICE_ID,
    CONF_PANEL_ID,
    CONF_PIN_HASH,
    CONF_USER_CODE,
)

if TYPE_CHECKING:
    from homeassistant.core import HomeAssistant

    from .coordinator import PyronixConfigEntry

TO_REDACT_ENTRY: Final = {
    CONF_APP_PASSWORD,
    CONF_APP_USER,
    CONF_CLOUD_URL,
    CONF_DEVICE_ID,
    CONF_PANEL_ID,
    CONF_PIN_HASH,
    CONF_USER_CODE,
}
"""Credentials, identifiers and the arm/disarm PIN hash in the entry."""

TO_REDACT_SNAPSHOT: Final = {
    "panel_id",
    "site_name",
    "last_set",
    "last_unset",
}
"""The System ID, the site name and the names of people in the panel state."""


def _dataclass(value: Any) -> dict[str, Any]:  # noqa: ANN401 - any dataclass
    """Return a dataclass instance's fields as a dict of JSON data."""
    return {f.name: plain(getattr(value, f.name)) for f in dataclasses.fields(value)}


_CONVERTERS: Final[tuple[tuple[type, Callable[[Any], Any]], ...]] = (
    (enum.Enum, lambda value: value.name),
    (datetime, lambda value: value.isoformat()),
    (Mapping, lambda value: {str(k): plain(v) for k, v in value.items()}),
    (frozenset, lambda value: sorted(plain(item) for item in value)),
    (tuple, lambda value: [plain(item) for item in value]),
)


def plain(value: object) -> Any:  # noqa: ANN401 - returns any JSON value
    """Return ``value`` as JSON data: dataclasses and mappings become dicts.

    Enums become their names, dates ISO strings, and sets sorted lists.
    """
    if dataclasses.is_dataclass(value) and not isinstance(value, type):
        return _dataclass(value)
    convert = next((c for kind, c in _CONVERTERS if isinstance(value, kind)), None)
    return convert(value) if convert is not None else value


async def async_get_config_entry_diagnostics(
    hass: HomeAssistant,  # noqa: ARG001 - the platform signature HA calls
    entry: PyronixConfigEntry,
) -> dict[str, Any]:
    """Return the entry, the link state and the panel state, all redacted."""
    coordinator = entry.runtime_data
    snapshot = plain(coordinator.data)
    snapshot["user"] = async_redact_data(snapshot["user"], {"name"})
    return {
        "entry": {
            "data": async_redact_data(entry.data, TO_REDACT_ENTRY),
            "options": async_redact_data(entry.options, TO_REDACT_ENTRY),
        },
        "persistent": coordinator.persistent,
        "connection_state": coordinator.connection_state.value,
        "last_update_success": coordinator.last_update_success,
        "snapshot": async_redact_data(snapshot, TO_REDACT_SNAPSHOT),
    }
