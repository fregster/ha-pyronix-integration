# SPDX-FileCopyrightText: 2026 Paul Fryer
# SPDX-License-Identifier: LGPL-3.0-or-later
"""Set/unset commands: unknown outcomes, forced-set refusals and already-set areas."""

from __future__ import annotations

import asyncio
from typing import TYPE_CHECKING

import pytest

from pyronix_cloud import commands
from pyronix_cloud.client import Credentials, PyronixClient, Timeouts
from pyronix_cloud.connection import Connection
from pyronix_cloud.errors import (
    CommandNotConfirmedError,
    ConnectionClosedError,
    NotConnectedError,
    PyronixError,
    PyronixTimeoutError,
    SetRefusedError,
)
from pyronix_cloud.models import AreaStatus
from pyronix_cloud.not_polling_retry import NO_RETRY
from pyronix_cloud.testing import dump
from pyronix_cloud.testing.fake_cloud import FakeCloud
from pyronix_cloud.testing.fake_panel import FakePanel, PanelBehaviour

if TYPE_CHECKING:
    from collections.abc import Callable, Coroutine

    from pyronix_cloud.panel_state import PanelSnapshot

CREDENTIALS = Credentials(
    app_user="tester",
    panel_id=dump.PANEL_ID,
    app_password="app-password",  # noqa: S106 - the fake panel's test value
    user_code="1234",
    device_id="device-1",
)
FAST = Timeouts(connect=1, step=1, dump=1, command=0.1, close=1)
# A long command timeout: a refusal or a dropped session must not wait for it.
SLOW = Timeouts(connect=1, step=1, dump=1, command=30, close=1)

type Command = Callable[[PyronixClient], Coroutine[None, None, PanelSnapshot]]


async def arm_0(client: PyronixClient) -> PanelSnapshot:
    return await client.arm([0])


async def disarm_0(client: PyronixClient) -> PanelSnapshot:
    return await client.disarm([0])


async def open_client(
    behaviour: PanelBehaviour | None = None,
    *,
    areas: dict[int, int] | None = None,
    timeouts: Timeouts = SLOW,
) -> tuple[FakeCloud, PyronixClient]:
    shared = {} if areas is None else areas
    fake = FakeCloud(panel_factory=lambda: FakePanel(behaviour, areas=shared))
    client = PyronixClient(
        CREDENTIALS,
        fake.connector(),
        url="memory://",
        timeouts=timeouts,
        retry=NO_RETRY,
    )
    await client.connect()
    return fake, client


def received(fake: FakeCloud) -> list[bytes]:
    panel = fake.sessions[-1].panel
    assert panel is not None
    return panel.received_commands


async def until_received(fake: FakeCloud) -> None:
    for _ in range(200):
        if received(fake):
            return
        await asyncio.sleep(0)
    pytest.fail("the panel received no command")


# --- the outcome is unknown once the command may have been sent -------------


@pytest.mark.parametrize("command", [arm_0, disarm_0], ids=["arm", "disarm"])
async def test_a_drop_after_sending_is_not_confirmed(command: Command) -> None:
    areas = {0: 1} if command is disarm_0 else {}
    fake, client = await open_client(
        PanelBehaviour(confirm_commands=False), areas=areas
    )
    task = asyncio.create_task(command(client))
    await until_received(fake)
    await fake.drop()
    async with asyncio.timeout(5):
        with pytest.raises(CommandNotConfirmedError) as raised:
            await task
    assert isinstance(raised.value.__cause__, PyronixError)
    assert raised.value.snapshot is not None
    assert raised.value.snapshot.areas[0].number == 0
    await client.close()


async def test_closing_while_waiting_is_not_confirmed() -> None:
    fake, client = await open_client(PanelBehaviour(confirm_commands=False))
    task = asyncio.create_task(client.arm([0]))
    await until_received(fake)
    await client.close()
    with pytest.raises(CommandNotConfirmedError) as raised:
        await task
    assert isinstance(raised.value.__cause__, ConnectionClosedError)


async def test_a_timeout_is_not_confirmed_and_not_a_transient_timeout() -> None:
    _, client = await open_client(PanelBehaviour(confirm_commands=False), timeouts=FAST)
    with pytest.raises(CommandNotConfirmedError) as raised:
        await client.arm([0])
    assert isinstance(raised.value.__cause__, TimeoutError)
    assert not isinstance(raised.value, PyronixTimeoutError)
    assert raised.value.snapshot is client.snapshot
    assert client.connected
    await client.close()


@pytest.mark.parametrize(
    "error", [ConnectionClosedError("sending failed"), RuntimeError("transport")]
)
async def test_a_send_failure_is_not_confirmed(
    monkeypatch: pytest.MonkeyPatch, error: Exception
) -> None:
    fake, client = await open_client(PanelBehaviour(confirm_commands=False))
    send = Connection.send_command

    async def send_then_fail(self: Connection, payload: bytes) -> None:
        await send(self, payload)
        raise error

    monkeypatch.setattr(Connection, "send_command", send_then_fail)
    with pytest.raises(CommandNotConfirmedError) as raised:
        await client.arm([0])
    assert raised.value.__cause__ is error
    assert received(fake) == [commands.set_areas([0])]
    await client.close()


async def test_a_command_refused_before_sending_stays_not_connected(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    fake, client = await open_client()

    async def refuse(self: Connection, payload: bytes) -> None:
        del self, payload
        msg = "the panel session is closing"
        raise NotConnectedError(msg)

    monkeypatch.setattr(Connection, "send_command", refuse)
    with pytest.raises(NotConnectedError):
        await client.arm([0])
    assert received(fake) == []
    await client.close()


async def test_cancellation_propagates_unchanged() -> None:
    fake, client = await open_client(PanelBehaviour(confirm_commands=False))
    task = asyncio.create_task(client.arm([0]))
    await until_received(fake)
    task.cancel()
    with pytest.raises(asyncio.CancelledError):
        await task
    await client.close()


# --- a forced set ends at once when the panel refuses it ----------------------


@pytest.mark.parametrize("status", [4, 6])
async def test_a_forced_set_fails_fast_on_a_refusal(status: int) -> None:
    _, client = await open_client(PanelBehaviour(set_statuses=(status,)))
    async with asyncio.timeout(5):
        with pytest.raises(SetRefusedError) as raised:
            await client.arm([0], force=True)
    assert raised.value.area.status is AreaStatus(status)
    assert client.connected
    await client.close()


async def test_a_forced_set_overrides_can_override() -> None:
    _, client = await open_client(PanelBehaviour(set_statuses=(5, 3)))
    snapshot = await client.arm([0], force=True)
    assert snapshot.areas[0].status is AreaStatus.SETTING
    await client.close()


async def test_a_forced_set_ignores_a_stale_cancelled_set() -> None:
    _, client = await open_client(areas={0: 6})
    snapshot = await client.arm([0], force=True)
    assert snapshot.areas[0].status in {AreaStatus.SETTING, AreaStatus.SET}
    await client.close()


# --- areas that are already set ---------------------------------------------


@pytest.mark.parametrize("force", [True, False])
async def test_arm_of_set_areas_sends_nothing(*, force: bool) -> None:
    fake, client = await open_client(areas={0: 1, 1: 3})
    snapshot = await client.arm([0, 1], force=force)
    assert snapshot is client.snapshot
    assert received(fake) == []
    await client.close()


async def test_arm_sends_when_one_area_is_unset() -> None:
    fake, client = await open_client(areas={0: 1})
    await client.arm([0, 1])
    assert received(fake) == [commands.set_areas([0, 1])]
    await client.close()
