# SPDX-FileCopyrightText: 2026 Paul Fryer
# SPDX-License-Identifier: LGPL-3.0-or-later
from __future__ import annotations

import asyncio

import pytest

from pyronix_cloud import RetryPolicy, commands
from pyronix_cloud.client import Credentials, PyronixClient, Timeouts
from pyronix_cloud.errors import PanelBusyError, PanelOfflineError
from pyronix_cloud.not_polling_retry import NO_RETRY, NotPollingRetry
from pyronix_cloud.testing import dump
from pyronix_cloud.testing.fake_cloud import CloudBehaviour, FakeCloud

CREDENTIALS = Credentials(
    app_user="tester",
    panel_id=dump.PANEL_ID,
    app_password="app-password",  # noqa: S106 - the fake panel's test value
    user_code="1234",
    device_id="device-1",
)
FAST = Timeouts(connect=1, step=1, dump=1, command=0.05, close=1)
QUICK = RetryPolicy(retries=3, pause=0.01)


class Attempts:
    """An attempt that fails with ``error`` a number of times, then succeeds."""

    def __init__(self, failures: int, error: Exception | None = None) -> None:
        self.failures = failures
        self.error = error or PanelOfflineError("not polling")
        self.calls = 0
        self.called = asyncio.Event()

    async def __call__(self) -> str:
        self.calls += 1
        self.called.set()
        if self.calls <= self.failures:
            raise self.error
        return "connected"


def test_policy_defaults_span_the_reported_gap() -> None:
    policy = RetryPolicy()
    assert policy.retries * policy.pause >= 35  # aiopyronix saw ~35 s of `n`
    assert policy.retries * policy.pause <= 45
    assert NO_RETRY.retries == 0


@pytest.mark.parametrize(
    "policy", [RetryPolicy(retries=-1), RetryPolicy(retries=1, pause=0)]
)
def test_invalid_policy(policy: RetryPolicy) -> None:
    with pytest.raises(ValueError, match="retry"):
        NotPollingRetry(policy)


async def test_retries_until_the_cloud_accepts() -> None:
    attempt = Attempts(failures=3)
    assert await NotPollingRetry(QUICK).run(attempt) == "connected"
    assert attempt.calls == 4


async def test_gives_up_after_the_retries() -> None:
    attempt = Attempts(failures=10)
    with pytest.raises(PanelOfflineError):
        await NotPollingRetry(QUICK).run(attempt)
    assert attempt.calls == 4


async def test_no_retry_policy_fails_at_once() -> None:
    attempt = Attempts(failures=1)
    with pytest.raises(PanelOfflineError):
        await NotPollingRetry(NO_RETRY).run(attempt)
    assert attempt.calls == 1


async def test_other_errors_are_not_retried() -> None:
    attempt = Attempts(failures=1, error=PanelBusyError("busy"))
    with pytest.raises(PanelBusyError):
        await NotPollingRetry(QUICK).run(attempt)
    assert attempt.calls == 1


async def test_stop_ends_a_pending_pause() -> None:
    attempt = Attempts(failures=10)
    retry = NotPollingRetry(RetryPolicy(retries=3, pause=60))
    task = asyncio.create_task(retry.run(attempt))
    await attempt.called.wait()
    retry.stop()
    with pytest.raises(PanelOfflineError):
        await asyncio.wait_for(task, 1)
    assert attempt.calls == 1


async def test_cancel_during_a_pause() -> None:
    attempt = Attempts(failures=10)
    task = asyncio.create_task(
        NotPollingRetry(RetryPolicy(retries=3, pause=60)).run(attempt)
    )
    await attempt.called.wait()
    task.cancel()
    with pytest.raises(asyncio.CancelledError):
        await task


async def test_client_connects_once_the_cloud_accepts_again() -> None:
    async with FakeCloud(CloudBehaviour(not_polling_after_end=2)) as fake:
        client = PyronixClient(
            CREDENTIALS, fake.connector(), timeouts=FAST, retry=QUICK
        )
        async with client:
            pass
        async with client:  # straight after the goodbye: two `n`, then accepted
            await client.arm([0])
        first, *refused, armed = fake.sessions
        assert len(refused) == 2
        assert all(s.closed_by_client and s.panel is None for s in refused)
        assert first.panel is not None
        assert armed.panel is not None
        assert armed.panel.received_commands == [commands.set_areas((0,), force=True)]


async def test_client_gives_up_with_panel_offline() -> None:
    async with FakeCloud(CloudBehaviour(not_polling_after_end=9)) as fake:
        client = PyronixClient(
            CREDENTIALS, fake.connector(), timeouts=FAST, retry=QUICK
        )
        async with client:
            pass
        with pytest.raises(PanelOfflineError):
            await client.connect()
        assert len(fake.sessions) == 5
        assert all(s.closed_by_client for s in fake.sessions)


async def test_client_close_stops_the_retry() -> None:
    async with FakeCloud(CloudBehaviour(not_polling_after_end=9)) as fake:
        client = PyronixClient(
            CREDENTIALS,
            fake.connector(),
            timeouts=FAST,
            retry=RetryPolicy(retries=3, pause=60),
        )
        async with client:
            pass
        task = asyncio.create_task(client.connect())
        # FakeCloud has no event for a session ending, so poll its state.
        while len(fake.sessions) < 2 or not fake.sessions[1].ended:  # noqa: ASYNC110
            await asyncio.sleep(0)
        await client.close()
        with pytest.raises(PanelOfflineError):
            await asyncio.wait_for(task, 1)
        assert len(fake.sessions) == 2
