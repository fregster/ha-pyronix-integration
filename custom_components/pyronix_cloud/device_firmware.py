# SPDX-FileCopyrightText: 2026 Paul Fryer
# SPDX-License-Identifier: Apache-2.0
"""Keep the panel device's firmware version in step with the coordinator's data."""

from __future__ import annotations

from typing import TYPE_CHECKING

from homeassistant.core import CALLBACK_TYPE, callback
from homeassistant.helpers import device_registry as dr

from .const import CONF_PANEL_ID, DOMAIN

if TYPE_CHECKING:
    from homeassistant.core import HomeAssistant

    from .coordinator import PyronixCoordinator


@callback
def async_track_firmware(
    hass: HomeAssistant, coordinator: PyronixCoordinator
) -> CALLBACK_TYPE:
    """Update the device's ``sw_version`` whenever the panel reports new firmware.

    Entities set the version only when they are added, so a firmware upgrade
    seen later (in a poll or a live push) would otherwise never show.

    Args:
        hass: The Home Assistant instance.
        coordinator: The entry's coordinator.

    Returns:
        A callback that stops tracking.
    """
    entry = coordinator.config_entry
    identifier = (DOMAIN, entry.data[CONF_PANEL_ID])

    @callback
    def _on_update() -> None:
        info = coordinator.data.info
        if info is None:
            return
        registry = dr.async_get(hass)
        for device in dr.async_entries_for_config_entry(registry, entry.entry_id):
            if identifier in device.identifiers and device.sw_version != info.firmware:
                registry.async_update_device(device.id, sw_version=info.firmware)

    return coordinator.async_add_listener(_on_update)
