# SPDX-FileCopyrightText: 2026 Paul Fryer
# SPDX-License-Identifier: Apache-2.0
"""Set up and unload Pyronix Cloud config entries."""

from __future__ import annotations

from typing import TYPE_CHECKING, Final

from homeassistant.const import EVENT_HOMEASSISTANT_STOP, Platform
from homeassistant.helpers import config_validation as cv

from .access_issue import async_delete_access_issue, async_update_access_issue
from .const import DOMAIN
from .coordinator import PyronixConfigEntry, PyronixCoordinator
from .device_firmware import async_track_firmware
from .services import async_setup_services

if TYPE_CHECKING:
    from homeassistant.core import Event, HomeAssistant
    from homeassistant.helpers.typing import ConfigType

__all__ = [
    "PyronixConfigEntry",
    "async_remove_entry",
    "async_setup",
    "async_setup_entry",
    "async_unload_entry",
]

CONFIG_SCHEMA: Final = cv.config_entry_only_config_schema(DOMAIN)

PLATFORMS: Final[list[Platform]] = [
    Platform.ALARM_CONTROL_PANEL,
    Platform.BINARY_SENSOR,
    Platform.SENSOR,
]


async def async_setup(
    hass: HomeAssistant,
    config: ConfigType,  # noqa: ARG001 - the signature HA calls
) -> bool:
    """Register the service actions once, for every entry."""
    async_setup_services(hass)
    return True


async def async_setup_entry(hass: HomeAssistant, entry: PyronixConfigEntry) -> bool:
    """Connect to the panel once, then set up the entity platforms.

    Raises:
        ConfigEntryAuthFailed: The panel rejected the stored credentials.
        ConfigEntryNotReady: The panel could not be reached.
    """
    async_update_access_issue(hass, entry)
    coordinator = PyronixCoordinator(hass, entry)
    await coordinator.async_config_entry_first_refresh()
    entry.runtime_data = coordinator

    async def _on_stop(_event: Event) -> None:
        await coordinator.async_close()

    entry.async_on_unload(
        hass.bus.async_listen_once(EVENT_HOMEASSISTANT_STOP, _on_stop)
    )
    await hass.config_entries.async_forward_entry_setups(entry, PLATFORMS)
    entry.async_on_unload(async_track_firmware(hass, coordinator))
    return True


async def async_unload_entry(hass: HomeAssistant, entry: PyronixConfigEntry) -> bool:
    """Unload the platforms, then say goodbye and close the panel session."""
    unloaded = await hass.config_entries.async_unload_platforms(entry, PLATFORMS)
    if unloaded:
        await entry.runtime_data.async_close()
    return unloaded


async def async_remove_entry(hass: HomeAssistant, entry: PyronixConfigEntry) -> None:
    """Clear the entry's repair issue when it is deleted."""
    async_delete_access_issue(hass, entry)
