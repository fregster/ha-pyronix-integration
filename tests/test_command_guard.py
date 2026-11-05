# SPDX-FileCopyrightText: 2026 Paul Fryer
# SPDX-License-Identifier: LGPL-3.0-or-later
"""One command at a time per client: a second is refused at once, never queued."""

from __future__ import annotations

import asyncio

import pytest

from pyronix_cloud import commands
from pyronix_cloud.client import Credentials, PyronixClient, Timeouts
from pyronix_cloud.command_guard import CommandGuard
from pyronix_cloud.errors import (
    CommandInProgressError,
    CommandNotConfirmedError,
    NotPermittedError,
    PyronixError,
)
from pyronix_cloud.models import AreaStatus
from pyronix_cloud.not_polling_retry import NO_RETRY
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
TIMEOUTS = Timeouts(connect=1, step=1, dump=1, command=0.2, close=1)


def is_busy(guard: CommandGuard) -> bool:
    return guard.busy  # a call, so mypy does not narrow the property


def test_guard_refuses_a_second_holder() -> None:
    guard = CommandGuard()
    assert not is_busy(guard)
    with guard.hold():
        assert is_busy(guard)
        with pytest.raises(CommandInProgressError), guard.hold():
            pytest.fail("a second holder got in")  # pragma: no cover
        assert is_busy(guard)  # the refused holder did not release the first
    assert not is_busy(guard)


def test_guard_is_released_on_error() -> None:
    guard = CommandGuard()
    with pytest.raises(ValueError, match="boom"), guard.hold():
        raise ValueError("boom")  # noqa: EM101 - a throwaway test error
    assert not is_busy(guard)


def test_command_in_progress_is_a_library_error() -> None:
    assert issubclass(CommandInProgressError, PyronixError)


async def open_client(behaviour: PanelBehaviour) -> tuple[FakeCloud, PyronixClient]:
    fake = FakeCloud(panel_factory=lambda: FakePanel(behaviour))
    client = PyronixClient(
        CREDENTIALS,
        fake.connector(),
        url="memory://",
        timeouts=TIMEOUTS,
        retry=NO_RETRY,
    )
    await client.connect()
    return fake, client


def received(fake: FakeCloud) -> list[bytes]:
    panel = fake.sessions[-1].panel
    assert panel is not None
    return panel.received_commands


async def test_a_second_command_is_refused_while_the_first_runs() -> None:
    fake, client = await open_client(PanelBehaviour())
    first = asyncio.create_task(client.arm([0]))
    await asyncio.sleep(0)  # the first command takes the guard
    with pytest.raises(CommandInProgressError):
        await client.arm([1])
    with pytest.raises(CommandInProgressError):
        await client.disarm([0])
    snapshot = await first
    assert snapshot.areas[0].status in {AreaStatus.SETTING, AreaStatus.SET}
    assert received(fake) == [commands.set_areas([0])]
    await client.disarm([0])  # released once the first completed
    assert received(fake)[-1] == commands.unset_areas([0])
    await client.close()


async def test_the_guard_is_released_after_a_failure() -> None:
    fake, client = await open_client(PanelBehaviour(confirm_commands=False))
    with pytest.raises(CommandNotConfirmedError):
        await client.arm([0])
    with pytest.raises(NotPermittedError):
        await client.arm([3])
    with pytest.raises(CommandNotConfirmedError):
        await client.arm([0])
    assert len(received(fake)) == 2
    await client.close()


async def test_the_guard_is_released_after_cancellation() -> None:
    fake, client = await open_client(PanelBehaviour(confirm_commands=False))
    task = asyncio.create_task(client.arm([0]))
    for _ in range(200):
        if received(fake):
            break
        await asyncio.sleep(0)
    task.cancel()
    with pytest.raises(asyncio.CancelledError):
        await task
    with pytest.raises(CommandNotConfirmedError):
        await client.arm([0])  # the guard is free again
    await client.close()
