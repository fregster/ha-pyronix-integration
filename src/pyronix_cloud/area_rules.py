# SPDX-FileCopyrightText: 2026 Paul Fryer
# SPDX-License-Identifier: LGPL-3.0-or-later
"""Decide whether a set/unset command is allowed and when the panel has confirmed it.

Both checks read only a :class:`~pyronix_cloud.panel_state.PanelSnapshot`, so
they are pure functions.
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Final

from pyronix_cloud.errors import NotPermittedError
from pyronix_cloud.models import AreaStatus

if TYPE_CHECKING:
    from pyronix_cloud.panel_state import PanelSnapshot

ARMED: Final = frozenset({AreaStatus.SETTING, AreaStatus.SET, AreaStatus.ALARMED})
"""Statuses that confirm a set command: the area left unset."""

DISARMED: Final = frozenset({AreaStatus.UNSET})
"""Statuses that confirm an unset command."""


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
