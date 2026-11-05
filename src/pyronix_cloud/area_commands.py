# SPDX-FileCopyrightText: 2026 Paul Fryer
# SPDX-License-Identifier: LGPL-3.0-or-later
"""Send set/unset commands on a connected session and wait for the panel to confirm.

The checks before sending (permission, readiness) and the confirmation rules
(which area statuses count as accepted or refused) come from
:mod:`pyronix_cloud.area_rules`; this module only orders them around the send.

Once a command may have left the client its outcome is unknown until the panel
reports it, so every failure from then on (timeout, session end, send error)
becomes :class:`~pyronix_cloud.errors.CommandNotConfirmedError`.
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
from pyronix_cloud.errors import (
    CommandNotConfirmedError,
    NotConnectedError,
    PyronixError,
)

if TYPE_CHECKING:
    from collections.abc import Callable

    from pyronix_cloud.connection import Connection
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

    Nothing is sent if every area is already setting, set or in alarm. See
    :meth:`pyronix_cloud.client.PyronixClient.arm` for the behaviour and the
    errors raised.
    """
    payload = commands.set_areas(numbers, force=force)
    before = feed.snapshot
    check_permitted(before, numbers, set_areas=True)
    if all_in(before, numbers, ARMED):
        return before
    if not force:
        check_settable(before, numbers)
    snapshot = await _send_and_wait(
        connection,
        feed,
        confirm_within,
        payload,
        lambda s: (
            refused_area(s, numbers, before, force=force) is not None
            or all_in(s, numbers, ARMED)
        ),
    )
    check_not_refused(snapshot, numbers, before, force=force)
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
    return await _send_and_wait(
        connection,
        feed,
        confirm_within,
        payload,
        lambda s: all_in(s, numbers, DISARMED),
    )


async def _send_and_wait(
    connection: Connection,
    feed: PanelFeed,
    confirm_within: float,
    payload: bytes,
    done: Callable[[PanelSnapshot], bool],
) -> PanelSnapshot:
    """Send ``payload`` and wait until ``done``; see the module docstring.

    Raises:
        NotConnectedError: The session refused the command before sending it.
        CommandNotConfirmedError: The command may have been sent, but no
            outcome arrived; chained to the cause.
    """

    def not_confirmed() -> CommandNotConfirmedError:
        msg = "the panel did not confirm the command; it may have taken effect"
        return CommandNotConfirmedError(msg, feed.snapshot)

    try:
        await connection.send_command(payload)
    except NotConnectedError:
        raise  # rejected before anything was written: definitely not sent
    except Exception as err:
        raise not_confirmed() from err
    try:
        return await feed.wait_until(done, confirm_within, not_confirmed)
    except CommandNotConfirmedError:
        raise
    except PyronixError as err:
        raise not_confirmed() from err
