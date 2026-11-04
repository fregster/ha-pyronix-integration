# SPDX-FileCopyrightText: 2026 Paul Fryer
# SPDX-License-Identifier: LGPL-3.0-or-later
from __future__ import annotations

from pyronix_cloud.testing.cloud_session import (
    CloudBehaviour,
    CloudSession,
    SessionSlot,
)
from pyronix_cloud.testing.fake_panel import FakePanel
from pyronix_cloud.testing.memory_socket import MemoryWebSocket


def socket(closed: list[MemoryWebSocket]) -> MemoryWebSocket:
    session = CloudSession(CloudBehaviour(), FakePanel, SessionSlot(), "u")
    return MemoryWebSocket(session, closed.append)


async def test_client_close_discards_queued_messages() -> None:
    closed: list[MemoryWebSocket] = []
    ws = socket(closed)
    ws.deliver(["one", "two"])
    assert await ws.receive() == "one"
    await ws.close()
    await ws.close()
    ws.drop()
    ws.deliver(["late"])
    assert await ws.receive() is None
    assert await ws.receive() is None
    assert closed == [ws]
    assert ws.session.closed_by_client


async def test_drop_delivers_queued_messages_first() -> None:
    closed: list[MemoryWebSocket] = []
    ws = socket(closed)
    ws.deliver(["one"])
    ws.drop()
    ws.drop()
    assert await ws.receive() == "one"
    assert await ws.receive() is None
    assert ws.closed
    assert closed == [ws]
    assert not ws.session.closed_by_client
