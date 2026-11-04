# SPDX-FileCopyrightText: 2026 Paul Fryer
# SPDX-License-Identifier: Apache-2.0
"""Diagnostic sensor entities for the panel link state and the panel's faults."""

from __future__ import annotations

from typing import TYPE_CHECKING, override

from homeassistant.components.sensor import (
    SensorDeviceClass,
    SensorEntity,
    SensorStateClass,
)
from homeassistant.const import EntityCategory

from .const import ConnectionState
from .entity import PyronixEntity

if TYPE_CHECKING:
    from homeassistant.core import HomeAssistant
    from homeassistant.helpers.entity_platform import AddConfigEntryEntitiesCallback

    from .coordinator import PyronixConfigEntry, PyronixCoordinator

# Read-only: entities only read the coordinator's state.
PARALLEL_UPDATES = 0


async def async_setup_entry(
    hass: HomeAssistant,  # noqa: ARG001 - the platform signature HA calls
    entry: PyronixConfigEntry,
    async_add_entities: AddConfigEntryEntitiesCallback,
) -> None:
    """Add the connection state and faults sensors."""
    coordinator = entry.runtime_data
    async_add_entities([ConnectionStateSensor(coordinator), FaultsSensor(coordinator)])


class ConnectionStateSensor(PyronixEntity, SensorEntity):
    """The state of the link to the panel: idle, connecting, connected or error.

    Disabled by default: in on-demand mode it changes on every poll. It stays
    available when the panel cannot be reached, to show the error.
    """

    _attr_device_class = SensorDeviceClass.ENUM
    _attr_entity_category = EntityCategory.DIAGNOSTIC
    _attr_entity_registry_enabled_default = False
    _attr_translation_key = "connection_state"

    def __init__(self, coordinator: PyronixCoordinator) -> None:
        """Create the sensor."""
        super().__init__(coordinator, "connection_state")
        self._attr_options = [state.value for state in ConnectionState]

    @property
    @override
    def available(self) -> bool:
        """Always available: "error" is the state worth seeing."""
        return True

    @property
    @override
    def native_value(self) -> str:
        """The coordinator's link state."""
        return self.coordinator.connection_state.value


class FaultsSensor(PyronixEntity, SensorEntity):
    """How many faults the panel reports, panel-wide and per area.

    The fault texts are in the diagnostics download, not in attributes.
    """

    _attr_entity_category = EntityCategory.DIAGNOSTIC
    _attr_state_class = SensorStateClass.MEASUREMENT
    _attr_translation_key = "faults"

    def __init__(self, coordinator: PyronixCoordinator) -> None:
        """Create the sensor."""
        super().__init__(coordinator, "faults")

    @property
    @override
    def native_value(self) -> int:
        """Panel faults plus the faults of every area."""
        snapshot = self.snapshot
        area_faults = sum(len(area.faults) for area in snapshot.areas.values())
        return len(snapshot.faults) + area_faults
