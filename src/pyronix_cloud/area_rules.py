# SPDX-FileCopyrightText: 2026 Paul Fryer
# SPDX-License-Identifier: LGPL-3.0-or-later
"""Decide whether a set/unset command is allowed and when the panel has confirmed it.

Both checks read only a :class:`~pyronix_cloud.panel_state.PanelSnapshot`, so
they are pure functions.
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Final

from pyronix_cloud.errors import NotPermittedError, SetRefusedError
from pyronix_cloud.models import AreaStatus

if TYPE_CHECKING:
    from pyronix_cloud.models import Area
    from pyronix_cloud.panel_state import PanelSnapshot

ARMED: Final = frozenset({AreaStatus.SETTING, AreaStatus.SET, AreaStatus.ALARMED})
"""Statuses that confirm a set command: the area left unset."""

DISARMED: Final = frozenset({AreaStatus.UNSET})
"""Statuses that confirm an unset command."""

NOT_READY: Final = frozenset({AreaStatus.CANNOT_SET, AreaStatus.CAN_OVERRIDE})
"""Statuses in which a set without force is refused before it is sent."""

REFUSED: Final = NOT_READY | {AreaStatus.SET_CANCELLED}
"""Statuses that refuse a set without force once the panel reports them."""

FORCE_REFUSED: Final = frozenset({AreaStatus.CANNOT_SET, AreaStatus.SET_CANCELLED})
"""Statuses that refuse a forced set; the panel overrides CanOverride itself."""


def all_in(
    snapshot: PanelSnapshot, areas: tuple[int, ...], statuses: frozenset[AreaStatus]
) -> bool:
    """Return whether every area is known and has one of ``statuses``.

    Args:
        snapshot: The panel state.
        areas: Area numbers.
        statuses: The accepted statuses.
    """
    known = snapshot.areas
    return all(n in known and known[n].status in statuses for n in areas)


def check_permitted(
    snapshot: PanelSnapshot, areas: tuple[int, ...], *, set_areas: bool
) -> None:
    """Raise unless the logged-in user may set (or unset) every area.

    Args:
        snapshot: The panel state, with the ``user`` document.
        areas: Area numbers.
        set_areas: True for a set command, False for unset.

    Raises:
        NotPermittedError: The user may not act on some of the areas (or no
            user is known yet).
    """
    user = snapshot.user
    allowed = frozenset[int]()
    if user is not None:
        allowed = user.set_areas if set_areas else user.unset_areas
    denied = sorted(set(areas) - allowed)
    if denied:
        verb = "set" if set_areas else "unset"
        msg = f"the user may not {verb} areas {denied}"
        raise NotPermittedError(msg)


def check_settable(snapshot: PanelSnapshot, areas: tuple[int, ...]) -> None:
    """Raise if an area is not ready to set without force.

    Args:
        snapshot: The panel state.
        areas: Area numbers; unknown ones are left for the panel to judge.

    Raises:
        SetRefusedError: An area reports CannotSet or CanOverride.
    """
    for number in areas:
        area = snapshot.areas.get(number)
        if area is not None and area.status in NOT_READY:
            raise SetRefusedError(area)


def refusals(*, force: bool) -> frozenset[AreaStatus]:
    """Return the statuses that refuse a set with or without force."""
    return FORCE_REFUSED if force else REFUSED


def refused_area(
    snapshot: PanelSnapshot,
    areas: tuple[int, ...],
    before: PanelSnapshot,
    *,
    force: bool,
) -> Area | None:
    """Return the first area that newly reports a refusal, or None.

    A refusal status already shown in ``before`` (such as a cancelled set from
    an earlier attempt) is not new, so it does not count.

    Args:
        snapshot: The panel state now.
        areas: Area numbers of the set command.
        before: The panel state when the command was sent.
        force: Whether the set was forced (see :func:`refusals`).
    """
    refused = refusals(force=force)
    for number in areas:
        area = snapshot.areas.get(number)
        if area is None or area.status not in refused:
            continue
        old = before.areas.get(number)
        if old is None or old.status is not area.status:
            return area
    return None


def check_not_refused(
    snapshot: PanelSnapshot,
    areas: tuple[int, ...],
    before: PanelSnapshot,
    *,
    force: bool,
) -> None:
    """Raise if an area newly reports a refusal (see :func:`refused_area`).

    Raises:
        SetRefusedError: The panel refused to set an area.
    """
    area = refused_area(snapshot, areas, before, force=force)
    if area is not None:
        raise SetRefusedError(area)
