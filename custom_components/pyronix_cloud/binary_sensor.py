# SPDX-FileCopyrightText: 2026 Paul Fryer
# SPDX-License-Identifier: Apache-2.0
"""Binary sensor entities that show whether each panel input is open."""

from __future__ import annotations

from typing import TYPE_CHECKING, override

from homeassistant.components.binary_sensor import (  # type: ignore[attr-defined, unused-ignore]  # HA 2026.10 moved the enum to .const (implicit re-export); 2026.2 has no .const
    BinarySensorDeviceClass,
    BinarySensorEntity,
)
from homeassistant.core import callback

from pyronix_cloud import InputState

from .entity import PyronixEntity

if TYPE_CHECKING:
    from homeassistant.core import HomeAssistant
    from homeassistant.helpers.entity_platform import AddConfigEntryEntitiesCallback

    from pyronix_cloud import Input

    from .coordinator import PyronixConfigEntry, PyronixCoordinator

# Read-only: entities only read the coordinator's snapshot.
PARALLEL_UPDATES = 0

_INPUT_STATES = {InputState.OPEN: True, InputState.CLOSED: False}


async def async_setup_entry(
    hass: HomeAssistant,  # noqa: ARG001 - the platform signature HA calls
    entry: PyronixConfigEntry,
    async_add_entities: AddConfigEntryEntitiesCallback,
) -> None:
    """Add a sensor per input now, and for each input that appears later."""
    coordinator = entry.runtime_data
    known: set[int] = set()

    @callback
    def _add_new_inputs() -> None:
        new = sorted(set(coordinator.data.inputs) - known)
        if new:
            known.update(new)
            async_add_entities(InputSensor(coordinator, n) for n in new)

    _add_new_inputs()
    entry.async_on_unload(coordinator.async_add_listener(_add_new_inputs))


def device_class(item: Input) -> BinarySensorDeviceClass | None:
    """Guess an input's device class from its type and name.

    Fire inputs are smoke; names with "PIR" or ending " DT" are motion; names
    with "Door" or "Window" are openings; anything else has no class.
    """
    name = item.name
    if item.input_type == "Fire":
        return BinarySensorDeviceClass.SMOKE
    if "PIR" in name or name.endswith(" DT"):
        return BinarySensorDeviceClass.MOTION
    if "Door" in name or "Window" in name:
        return BinarySensorDeviceClass.OPENING
    return None


class InputSensor(PyronixEntity, BinarySensorEntity):
    """One input (zone): on while open, unknown while in fault."""

    def __init__(self, coordinator: PyronixCoordinator, number: int) -> None:
        """Create the sensor of input ``number``, named as the panel names it."""
        super().__init__(coordinator, f"input_{number}")
        item = coordinator.data.inputs[number]
        self._number = number
        self._attr_name = item.name
        self._attr_device_class = device_class(item)

    @property
    @override
    def is_on(self) -> bool | None:
        """Open: on; closed: off; a problem or an unknown state: unknown."""
        item = self.snapshot.inputs.get(self._number)
        return _INPUT_STATES.get(item.state) if item is not None else None
