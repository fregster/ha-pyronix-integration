# SPDX-FileCopyrightText: 2026 Paul Fryer
# SPDX-License-Identifier: Apache-2.0
"""Set up and unload Pyronix Cloud config entries."""

from __future__ import annotations

from typing import TYPE_CHECKING, Final

from homeassistant.const import EVENT_HOMEASSISTANT_STOP, Platform

from .coordinator import PyronixConfigEntry, PyronixCoordinator

if TYPE_CHECKING:
    from homeassistant.core import Event, HomeAssistant

__all__ = ["PyronixConfigEntry", "async_setup_entry", "async_unload_entry"]

PLATFORMS: Final[list[Platform]] = [
    Platform.ALARM_CONTROL_PANEL,
    Platform.BINARY_SENSOR,
    Platform.SENSOR,
]


async def async_setup_entry(hass: HomeAssistant, entry: PyronixConfigEntry) -> bool:
    """Connect to the panel once, then set up the entity platforms.

    Raises:
        ConfigEntryAuthFailed: The panel rejected the stored credentials.
        ConfigEntryNotReady: The panel could not be reached.
    """
    coordinator = PyronixCoordinator(hass, entry)
    await coordinator.async_config_entry_first_refresh()
    entry.runtime_data = coordinator

    async def _on_stop(_event: Event) -> None:
        await coordinator.async_close()

    entry.async_on_unload(
        hass.bus.async_listen_once(EVENT_HOMEASSISTANT_STOP, _on_stop)
    )
    await hass.config_entries.async_forward_entry_setups(entry, PLATFORMS)
    return True


async def async_unload_entry(hass: HomeAssistant, entry: PyronixConfigEntry) -> bool:
    """Unload the platforms, then say goodbye and close the panel session."""
    unloaded = await hass.config_entries.async_unload_platforms(entry, PLATFORMS)
    if unloaded:
        await entry.runtime_data.async_close()
    return unloaded
