# SPDX-FileCopyrightText: 2026 Paul Fryer
# SPDX-License-Identifier: Apache-2.0
"""Alarm control panel entities that show and change the panel's set state."""

from __future__ import annotations

from typing import TYPE_CHECKING, Final, override

from homeassistant.components.alarm_control_panel import AlarmControlPanelEntity
from homeassistant.components.alarm_control_panel.const import (
    AlarmControlPanelEntityFeature,
    AlarmControlPanelState,
    CodeFormat,
)
from homeassistant.exceptions import ServiceValidationError

from pyronix_cloud import AreaStatus, SetType

from .access import AccessPolicy, async_check_access
from .const import CONF_ALWAYS_FORCE_ARM, DOMAIN, FORCE_ARM_MODES, LEVEL_MODES
from .entity import PyronixEntity

if TYPE_CHECKING:
    from collections.abc import Iterable, Mapping

    from homeassistant.core import HomeAssistant
    from homeassistant.helpers.entity_platform import AddConfigEntryEntitiesCallback

    from pyronix_cloud import Area, PanelSnapshot

    from .coordinator import PyronixConfigEntry, PyronixCoordinator

# Commands share the coordinator's session lock; one at a time keeps it simple.
PARALLEL_UPDATES = 1

_MODE_FEATURES: Final[dict[AlarmControlPanelState, AlarmControlPanelEntityFeature]] = {
    AlarmControlPanelState.ARMED_AWAY: AlarmControlPanelEntityFeature.ARM_AWAY,
    AlarmControlPanelState.ARMED_NIGHT: AlarmControlPanelEntityFeature.ARM_NIGHT,
    AlarmControlPanelState.ARMED_CUSTOM_BYPASS: (
        AlarmControlPanelEntityFeature.ARM_CUSTOM_BYPASS
    ),
}
_NO_FEATURES: Final = AlarmControlPanelEntityFeature(0)


async def async_setup_entry(
    hass: HomeAssistant,  # noqa: ARG001 - the platform signature HA calls
    entry: PyronixConfigEntry,
    async_add_entities: AddConfigEntryEntitiesCallback,
) -> None:
    """Add one entity for a level-set panel, or one per in-use area otherwise."""
    coordinator = entry.runtime_data
    snapshot = coordinator.data
    if snapshot.set_type is SetType.LEVEL:
        async_add_entities([LevelAlarm(coordinator)])
        return
    async_add_entities(
        AreaAlarm(coordinator, area)
        for area in sorted(snapshot.areas.values(), key=lambda a: a.number)
        if area.in_use
    )


def alarm_state(
    areas: Iterable[Area], modes: Mapping[str, AlarmControlPanelState]
) -> AlarmControlPanelState | None:
    """Return the Home Assistant state of a group of areas, from ``Area.status``.

    Args:
        areas: The areas, in priority order for the armed mode.
        modes: Armed mode by area letter; a set area not listed is "away".

    Returns:
        Triggered if any area is in alarm, else the mode of the first set area,
        else arming if any is setting, else disarmed; None with no areas.
    """
    group = list(areas)
    if not group:
        return None
    statuses = {area.status for area in group}
    if AreaStatus.ALARMED in statuses:
        return AlarmControlPanelState.TRIGGERED
    for area in group:
        if area.status is AreaStatus.SET:
            return modes.get(area.letter, AlarmControlPanelState.ARMED_AWAY)
    if AreaStatus.SETTING in statuses:
        return AlarmControlPanelState.ARMING
    return AlarmControlPanelState.DISARMED


def level_areas(snapshot: PanelSnapshot) -> dict[str, Area]:
    """Return the in-use level areas by letter, in A, B, C order."""
    by_letter = {area.letter: area for area in snapshot.areas.values()}
    return {
        letter: by_letter[letter]
        for letter in LEVEL_MODES
        if letter in by_letter and by_letter[letter].in_use
    }


def settable(snapshot: PanelSnapshot) -> frozenset[int]:
    """Return the area numbers the logged-in user may set."""
    user = snapshot.user
    return user.set_areas if user is not None else frozenset()


class _PanelAlarm(PyronixEntity, AlarmControlPanelEntity):
    """Common behaviour: the access check, and arming with or without force.

    The panel's user code is in the entry; the code asked for here is the
    access option's PIN, never the keypad code.
    """

    def __init__(self, coordinator: PyronixCoordinator, key: str) -> None:
        """Read the access policy and arm options; a change reloads the entry."""
        super().__init__(coordinator, key)
        options = coordinator.config_entry.options
        self._policy = AccessPolicy.from_options(options)
        self._always_force = bool(options.get(CONF_ALWAYS_FORCE_ARM, False))
        self._attr_code_arm_required = self._policy.code_required
        self._attr_code_format = (
            CodeFormat.NUMBER if self._policy.code_required else None
        )

    async def _async_authorise(self, code: str | None) -> None:
        """Raise unless the calling user or code may arm and disarm."""
        context = self._context
        await async_check_access(
            self.hass,
            self._policy,
            code=code,
            user_id=context.user_id if context is not None else None,
        )

    async def _async_arm(
        self, number: int | None, code: str | None, *, force: bool | None = None
    ) -> None:
        """Set one area, forced if asked or if the always-force option is on.

        Raises:
            ServiceValidationError: Access denied, or the mode is not available.
            HomeAssistantError: The panel refused or did not set the area.
        """
        await self._async_authorise(code)
        if number is None:
            raise ServiceValidationError(
                translation_domain=DOMAIN, translation_key="mode_not_supported"
            )
        force = self._always_force if force is None else force
        await self.coordinator.async_arm([number], force=force)

    async def _async_disarm(self, numbers: list[int], code: str | None) -> None:
        """Unset areas (none to unset sends nothing) after the access check."""
        await self._async_authorise(code)
        if numbers:
            await self.coordinator.async_disarm(numbers)


class LevelAlarm(_PanelAlarm):
    """The whole panel on a level-set panel: level A away, B night, C bypass."""

    _attr_name = None

    def __init__(self, coordinator: PyronixCoordinator) -> None:
        """Create the panel's single alarm entity."""
        super().__init__(coordinator, "alarm")

    @property
    @override
    def alarm_state(self) -> AlarmControlPanelState | None:
        """The state of the level areas."""
        return alarm_state(level_areas(self.snapshot).values(), LEVEL_MODES)

    @property
    @override
    def supported_features(self) -> AlarmControlPanelEntityFeature:
        """The modes whose level exists, is in use and the user may set."""
        allowed = settable(self.snapshot)
        features = _NO_FEATURES
        for letter, area in level_areas(self.snapshot).items():
            if area.number in allowed:
                features |= _MODE_FEATURES[LEVEL_MODES[letter]]
        return features

    def _level(self, mode: AlarmControlPanelState) -> int | None:
        """Return the area number of the level for ``mode``, if it can be set."""
        if not self.supported_features & _MODE_FEATURES[mode]:
            return None
        letter = next(k for k, v in LEVEL_MODES.items() if v is mode)
        return level_areas(self.snapshot)[letter].number

    @override
    async def async_alarm_arm_away(self, code: str | None = None) -> None:
        """Set level A."""
        await self._async_arm(self._level(AlarmControlPanelState.ARMED_AWAY), code)

    @override
    async def async_alarm_arm_night(self, code: str | None = None) -> None:
        """Set level B."""
        await self._async_arm(self._level(AlarmControlPanelState.ARMED_NIGHT), code)

    @override
    async def async_alarm_arm_custom_bypass(self, code: str | None = None) -> None:
        """Set level C."""
        level = self._level(AlarmControlPanelState.ARMED_CUSTOM_BYPASS)
        await self._async_arm(level, code)

    async def async_force_arm(
        self, code: str | None = None, mode: str = "away"
    ) -> None:
        """Force-set the level of ``mode`` (away A, night B, custom_bypass C)."""
        level = self._level(FORCE_ARM_MODES[mode])
        await self._async_arm(level, self.code_or_default_code(code), force=True)

    @override
    async def async_alarm_disarm(self, code: str | None = None) -> None:
        """Unset every level that is not unset already.

        Raises:
            HomeAssistantError: The user may not unset a level, or the panel
                did not confirm.
        """
        numbers = [
            area.number
            for area in level_areas(self.snapshot).values()
            if area.status is not AreaStatus.UNSET
        ]
        await self._async_disarm(numbers, code)


class AreaAlarm(_PanelAlarm):
    """One area of an area-set panel: set ("away") and unset."""

    def __init__(self, coordinator: PyronixCoordinator, area: Area) -> None:
        """Create the entity of ``area``, named as the panel names it."""
        super().__init__(coordinator, f"area_{area.number}")
        self._number = area.number
        self._attr_name = area.name

    @property
    @override
    def alarm_state(self) -> AlarmControlPanelState | None:
        """The state of this area."""
        area = self.snapshot.areas.get(self._number)
        return alarm_state([area] if area is not None else [], {})

    @property
    @override
    def supported_features(self) -> AlarmControlPanelEntityFeature:
        """Arm away when the user may set this area."""
        if self._number in settable(self.snapshot):
            return AlarmControlPanelEntityFeature.ARM_AWAY
        return _NO_FEATURES

    @override
    async def async_alarm_arm_away(self, code: str | None = None) -> None:
        """Set this area."""
        await self._async_arm(self._number, code)

    async def async_force_arm(
        self,
        code: str | None = None,
        mode: str = "away",  # noqa: ARG002 - levels only; one area has one mode
    ) -> None:
        """Force-set this area; ``mode`` does not apply to a single area."""
        await self._async_arm(self._number, self.code_or_default_code(code), force=True)

    @override
    async def async_alarm_disarm(self, code: str | None = None) -> None:
        """Unset this area."""
        await self._async_disarm([self._number], code)
