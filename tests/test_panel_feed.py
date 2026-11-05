# SPDX-FileCopyrightText: 2026 Paul Fryer
# SPDX-License-Identifier: LGPL-3.0-or-later
from __future__ import annotations

import asyncio
from typing import TYPE_CHECKING

import pytest

from pyronix_cloud.errors import (
    AuthenticationError,
    ConnectionClosedError,
    PyronixError,
    PyronixTimeoutError,
)
from pyronix_cloud.panel_feed import PanelFeed
from pyronix_cloud.session import (
    DocumentReceived,
    PanelHeartbeat,
    Stage,
    StageChanged,
)
from pyronix_cloud.settings import Timeouts
from pyronix_cloud.testing import dump

if TYPE_CHECKING:
    from pyronix_cloud.panel_state import PanelSnapshot

FAST = Timeouts(step=0.02, dump=0.02)


def timeout() -> PyronixError:
    return PyronixTimeoutError("late")


def feed_dump(feed: PanelFeed) -> None:
    for text in dump.login_dump():
        feed.handle(DocumentReceived(text))


async def test_documents_build_the_snapshot_and_notify_on_change() -> None:
    feed = PanelFeed(FAST)
    seen: list[PanelSnapshot] = []
    remove = feed.add_listener(seen.append)
    feed_dump(feed)
    assert feed.snapshot.dump_complete
    count = len(seen)
    feed_dump(feed)  # identical re-send: no change, no call
    assert len(seen) == count
    remove()
    remove()
    feed.handle(DocumentReceived(dump.input_update(7, open_=True)))
    assert len(seen) == count


async def test_bad_document_is_logged_and_skipped(
    caplog: pytest.LogCaptureFixture,
) -> None:
    feed = PanelFeed(FAST)
    feed.handle(DocumentReceived("{nope"))
    assert "did not parse" in caplog.text
    assert not feed.snapshot.areas


async def test_failing_listener_is_logged(caplog: pytest.LogCaptureFixture) -> None:
    feed = PanelFeed(FAST)

    def broken(_snapshot: PanelSnapshot) -> None:
        raise RuntimeError

    feed.add_listener(broken)
    feed.handle(DocumentReceived(dump.login_dump()[0]))
    assert "snapshot listener" in caplog.text


async def test_wait_for_dump_completes_when_the_user_document_arrives() -> None:
    feed = PanelFeed(Timeouts(step=1, dump=1))
    feed.restart()
    task = asyncio.create_task(feed.wait_for_dump())
    await asyncio.sleep(0)
    feed.handle(StageChanged(Stage.READY))
    feed.handle(PanelHeartbeat())
    feed_dump(feed)
    await task


@pytest.mark.parametrize("stage", [Stage.SUBSCRIBING, Stage.READY])
async def test_wait_for_dump_times_out_per_stage(stage: Stage) -> None:
    feed = PanelFeed(FAST)
    feed.restart()
    feed.handle(StageChanged(stage))
    with pytest.raises(PyronixTimeoutError, match=stage.name):
        await feed.wait_for_dump()


async def test_end_wakes_waiters_with_the_error() -> None:
    feed = PanelFeed(Timeouts(step=1))
    feed.restart()
    task = asyncio.create_task(feed.wait_for_dump())
    await asyncio.sleep(0)
    feed.end(AuthenticationError("no"))
    with pytest.raises(AuthenticationError):
        await task


async def test_clean_end_raises_connection_closed() -> None:
    feed = PanelFeed(FAST)
    feed.end(None)
    with pytest.raises(ConnectionClosedError):
        await feed.wait_until(lambda _s: False, 1, timeout)
    feed.restart()
    with pytest.raises(PyronixTimeoutError, match="late"):
        await feed.wait_until(lambda _s: False, 0.01, timeout)


async def test_wait_until_returns_the_matching_snapshot() -> None:
    feed = PanelFeed(FAST)
    feed_dump(feed)
    snapshot = await feed.wait_until(lambda s: s.dump_complete, 1, timeout)
    assert snapshot is feed.snapshot
