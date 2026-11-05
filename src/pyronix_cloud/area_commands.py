# SPDX-FileCopyrightText: 2026 Paul Fryer
# SPDX-License-Identifier: LGPL-3.0-or-later
"""Send set/unset commands on a connected session and wait for the panel to confirm.

The checks before sending (permission, readiness) and the confirmation rules
(which area statuses count as accepted or refused) come from
:mod:`pyronix_cloud.area_rules`; this module only orders them around the send.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

from pyronix_cloud import commands
from pyronix_cloud.area_rules import (
    ARMED,
    DISARMED,
    all_in,
    check_not_refused,
    check_permitted,
    check_settable,
    refused_area,
)
from pyronix_cloud.errors import CommandNotConfirmedError

if TYPE_CHECKING:
    from pyronix_cloud.connection import Connection
    from pyronix_cloud.models import AreaStatus
    from pyronix_cloud.panel_feed import PanelFeed
    from pyronix_cloud.panel_state import PanelSnapshot


async def arm_areas(
    connection: Connection,
    feed: PanelFeed,
    confirm_within: float,
    numbers: tuple[int, ...],
    *,
    force: bool,
) -> PanelSnapshot:
    """Set ``numbers`` and wait until each is setting, set or in alarm.

    See :meth:`pyronix_cloud.client.PyronixClient.arm` for the behaviour and
    the errors raised.
    """
    payload = commands.set_areas(numbers, force=force)
    before = feed.snapshot
    check_permitted(before, numbers, set_areas=True)
    if not force:
        check_settable(before, numbers)
    await connection.send_command(payload)
    if force:
        return await _confirm(feed, confirm_within, numbers, ARMED)
    snapshot = await feed.wait_until(
        lambda s: (
            refused_area(s, numbers, before) is not None or all_in(s, numbers, ARMED)
        ),
        confirm_within,
        lambda: CommandNotConfirmedError(f"areas {list(numbers)} not confirmed"),
    )
    check_not_refused(snapshot, numbers, before)
    return snapshot


async def disarm_areas(
    connection: Connection,
    feed: PanelFeed,
    confirm_within: float,
    numbers: tuple[int, ...],
) -> PanelSnapshot:
    """Unset ``numbers`` and wait until each is unset.

    Nothing is sent if every area is already unset. See
    :meth:`pyronix_cloud.client.PyronixClient.disarm` for the errors raised.
    """
    payload = commands.unset_areas(numbers)
    check_permitted(feed.snapshot, numbers, set_areas=False)
    if all_in(feed.snapshot, numbers, DISARMED):
        return feed.snapshot
    await connection.send_command(payload)
    return await _confirm(feed, confirm_within, numbers, DISARMED)


async def _confirm(
    feed: PanelFeed,
    confirm_within: float,
    numbers: tuple[int, ...],
    statuses: frozenset[AreaStatus],
) -> PanelSnapshot:
    """Wait until every area in ``numbers`` reaches one of ``statuses``."""
    return await feed.wait_until(
        lambda snapshot: all_in(snapshot, numbers, statuses),
        confirm_within,
        lambda: CommandNotConfirmedError(f"areas {list(numbers)} not confirmed"),
    )
