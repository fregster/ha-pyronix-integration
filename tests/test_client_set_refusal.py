# SPDX-FileCopyrightText: 2026 Paul Fryer
# SPDX-License-Identifier: LGPL-3.0-or-later
"""PyronixClient.arm without force: confirmed on setting, refused fast with a reason."""

from __future__ import annotations

import asyncio

import pytest

from pyronix_cloud import commands
from pyronix_cloud.client import Credentials, PyronixClient, Timeouts
from pyronix_cloud.errors import SetRefusedError
from pyronix_cloud.models import AreaStatus
from pyronix_cloud.testing import dump
from pyronix_cloud.testing.fake_cloud import FakeCloud
from pyronix_cloud.testing.fake_panel import FakePanel, PanelBehaviour

CREDENTIALS = Credentials(
    app_user="tester",
    panel_id=dump.PANEL_ID,
    app_password="app-password",  # noqa: S106 - the fake panel's test value
    user_code="1234",
    device_id="device-1",
)
# A long command timeout: a refusal must not wait for it.
SLOW_CONFIRM = Timeouts(connect=1, step=1, dump=1, command=30, close=1)
DOOR_OPEN = dump.area_update(
    0, status=5, text="Set Delayed", faults=["Landing DT Open"]
)


def cloud_with(
    behaviour: PanelBehaviour | None = None, areas: dict[int, int] | None = None
) -> FakeCloud:
    shared = {} if areas is None else areas
    return FakeCloud(panel_factory=lambda: FakePanel(behaviour, areas=shared))


async def open_client(fake: FakeCloud) -> PyronixClient:
    client = PyronixClient(
        CREDENTIALS, fake.connector(), url="memory://", timeouts=SLOW_CONFIRM
    )
    await client.connect()
    return client


def commands_of(fake: FakeCloud) -> list[bytes]:
    panel = fake.sessions[-1].panel
    assert panel is not None
    return panel.received_commands


async def test_plain_arm_is_confirmed_by_setting() -> None:
    fake = cloud_with(PanelBehaviour(set_statuses=(3,)))
    client = await open_client(fake)
    snapshot = await client.arm([0], force=False)
    assert snapshot.areas[0].status is AreaStatus.SETTING
    assert commands_of(fake) == [commands.set_areas([0], force=False)]
    assert commands_of(fake) == [bytes([0x2D, 0x01, 0])]
    await client.close()


@pytest.mark.parametrize("status", [4, 5, 6])
async def test_refusal_after_sending_fails_fast(status: int) -> None:
    fake = cloud_with(PanelBehaviour(set_statuses=(status,)))
    client = await open_client(fake)
    async with asyncio.timeout(5):
        with pytest.raises(SetRefusedError) as raised:
            await client.arm([0], force=False)
    assert raised.value.area.number == 0
    assert raised.value.area.status is AreaStatus(status)
    assert client.connected  # the session stays usable
    await client.close()


async def test_blocked_area_is_refused_before_sending() -> None:
    fake = cloud_with()
    client = await open_client(fake)
    await fake.push(DOOR_OPEN)
    for _ in range(200):
        if client.snapshot.areas[0].faults:
            break
        await asyncio.sleep(0)
    with pytest.raises(SetRefusedError) as raised:
        await client.arm([0], force=False)
    assert raised.value.reason == "Landing DT Open"
    assert commands_of(fake) == []
    await client.close()


async def test_force_arm_skips_the_readiness_check() -> None:
    fake = cloud_with(areas={0: 5})
    client = await open_client(fake)
    assert client.snapshot.areas[0].status is AreaStatus.CAN_OVERRIDE
    snapshot = await client.arm([0], force=True)
    assert snapshot.areas[0].status in {AreaStatus.SETTING, AreaStatus.SET}
    assert commands_of(fake) == [bytes([0x2D, 0x81, 0])]
    await client.close()


async def test_a_stale_cancelled_set_does_not_fail_the_next_arm() -> None:
    fake = cloud_with(areas={0: 6})
    client = await open_client(fake)
    snapshot = await client.arm([0], force=False)
    assert snapshot.areas[0].status in {AreaStatus.SETTING, AreaStatus.SET}
    await client.close()
