# SPDX-FileCopyrightText: 2026 Paul Fryer
# SPDX-License-Identifier: Apache-2.0
"""Base entity tying every Pyronix entity to the coordinator and the panel device."""

from __future__ import annotations

from typing import TYPE_CHECKING

from homeassistant.helpers.device_registry import DeviceInfo
from homeassistant.helpers.update_coordinator import CoordinatorEntity

from .const import CONF_PANEL_ID, DOMAIN
from .coordinator import PyronixCoordinator

if TYPE_CHECKING:
    from pyronix_cloud import PanelSnapshot

MANUFACTURER = "Pyronix"
MODEL = "Euro 46 / Enforcer"


class PyronixEntity(CoordinatorEntity[PyronixCoordinator]):
    """An entity of the panel device, updated by the coordinator.

    Availability follows the coordinator: an entity is unavailable while the
    last poll (or the live session) failed.
    """

    _attr_has_entity_name = True

    def __init__(self, coordinator: PyronixCoordinator, key: str) -> None:
        """Set the unique ID ``<System ID>_<key>`` and the device info.

        Args:
            coordinator: The entry's coordinator; it holds a snapshot already.
            key: What this entity shows, unique within the panel.
        """
        super().__init__(coordinator)
        entry = coordinator.config_entry
        panel_id: str = entry.data[CONF_PANEL_ID]
        info = coordinator.data.info
        self._attr_unique_id = f"{panel_id}_{key}"
        self._attr_device_info = DeviceInfo(
            identifiers={(DOMAIN, panel_id)},
            manufacturer=MANUFACTURER,
            model=MODEL,
            name=entry.title,
            sw_version=info.firmware if info is not None else None,
        )

    @property
    def snapshot(self) -> PanelSnapshot:
        """The latest panel state."""
        return self.coordinator.data
