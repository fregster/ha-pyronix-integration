# SPDX-FileCopyrightText: 2026 Paul Fryer
# SPDX-License-Identifier: LGPL-3.0-or-later
"""The client sends a state-changing command only for an explicit arm or disarm."""

from __future__ import annotations

from dataclasses import replace
from typing import TYPE_CHECKING

import pytest

from pyronix_cloud import commands
from pyronix_cloud.client import Credentials, PyronixClient, Timeouts
from pyronix_cloud.errors import AuthenticationError
from pyronix_cloud.testing import dump
from pyronix_cloud.testing.command_audit import state_changes, state_commands
from pyronix_cloud.testing.fake_cloud import FakeCloud
from pyronix_cloud.testing.fake_panel import FakePanel, PanelBehaviour
from pyronix_cloud.testing.panel_documents import SET

if TYPE_CHECKING:
    from collections.abc import Awaitable, Callable

CREDENTIALS = Credentials(
    app_user="tester",
    panel_id=dump.PANEL_ID,
    app_password=PanelBehaviour().app_password,
    user_code=PanelBehaviour().user_code,
    device_id="device-1",
)
FAST = Timeouts(connect=1, step=1, dump=1, command=0.05, close=1)

type Action = Callable[[PyronixClient], Awaitable[object]]


def cloud_with_set(areas: tuple[int, ...] = ()) -> FakeCloud:
    """A fake cloud whose panels start with ``areas`` set."""
    state = dict.fromkeys(areas, SET)
    return FakeCloud(panel_factory=lambda: FakePanel(areas=state))


def client_for(
    cloud: FakeCloud, credentials: Credentials = CREDENTIALS
) -> PyronixClient:
    return PyronixClient(credentials, cloud.connector(), url="memory://", timeouts=FAST)


async def test_connect_snapshot_close_is_read_only() -> None:
    async with cloud_with_set() as cloud:
        client = client_for(cloud)
        snapshot = await client.connect()
        assert snapshot.dump_complete
        await cloud.heartbeat()  # the client answers with a panel heartbeat
        await client.close()
        (panel,) = cloud.panels
    assert panel.logged_in
    assert panel.received_commands == [commands.heartbeat()]
    assert panel.received_frame_types == ["A", "B", "D", "D", "E"]
    assert state_changes(cloud.panels) == []


async def test_failed_login_is_read_only() -> None:
    wrong = replace(CREDENTIALS, user_code="0000")
    async with cloud_with_set() as cloud:
        with pytest.raises(AuthenticationError):
            await client_for(cloud, wrong).connect()
    assert cloud.panels
    assert state_changes(cloud.panels) == []


def arm(*areas: int, force: bool = True) -> Action:
    return lambda client: client.arm(areas, force=force)


def disarm(*areas: int) -> Action:
    return lambda client: client.disarm(areas)


@pytest.mark.parametrize(
    ("preset", "action", "expected"),
    [
        ((), arm(0), commands.set_areas([0])),
        ((), arm(2, 0), commands.set_areas([0, 2])),
        ((), arm(1, force=False), commands.set_areas([1], force=False)),
        ((0, 1, 2), disarm(1), commands.unset_areas([1])),
        ((0, 1, 2), disarm(2, 0), commands.unset_areas([0, 2])),
        ((1,), disarm(0, 1), commands.unset_areas([0, 1])),
    ],
    ids=["arm 0", "arm 0 2", "arm 1 unforced", "disarm 1", "disarm 0 2", "disarm 0 1"],
)
async def test_arm_and_disarm_send_exactly_the_requested_command(
    preset: tuple[int, ...], action: Action, expected: bytes
) -> None:
    async with cloud_with_set(preset) as cloud, client_for(cloud) as client:
        await action(client)
    assert state_commands(cloud.panels) == [expected]
    assert state_changes(cloud.panels) == [f"command 0x{expected[0]:02X}"]
