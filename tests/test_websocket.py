# SPDX-FileCopyrightText: 2026 Paul Fryer
# SPDX-License-Identifier: LGPL-3.0-or-later
from __future__ import annotations

from typing import TYPE_CHECKING

import aiohttp
from aiohttp import web
from aiohttp.test_utils import TestServer
import pytest

from pyronix_cloud.errors import CommunicationError
from pyronix_cloud.websocket import (
    CLOSE_CODE_GOING_AWAY,
    WebSocket,
    aiohttp_connector,
)

if TYPE_CHECKING:
    from collections.abc import AsyncIterator

# The only library tests that open real (loopback) sockets: the HA test plugin
# blocks sockets by default, pytest-socket's fixture lifts that for this module.
pytestmark = pytest.mark.usefixtures("socket_enabled")

SEEN = web.AppKey("seen", dict[str, object])


async def _echo(request: web.Request) -> web.WebSocketResponse:
    ws = web.WebSocketResponse()
    await ws.prepare(request)
    request.app[SEEN]["agent"] = request.headers.get("User-Agent")
    async for msg in ws:
        if msg.type is aiohttp.WSMsgType.TEXT:
            if msg.data == "binary":
                await ws.send_bytes(b"\x01\x02")
            elif msg.data == "ping":
                await ws.ping()
                await ws.send_str("after-ping")
            elif msg.data == "bye":
                await ws.close()
            else:
                await ws.send_str(msg.data)
    request.app[SEEN]["close_code"] = ws.close_code
    return ws


def _is_closed(ws: WebSocket) -> bool:
    """Read ``closed`` through a call, so mypy does not narrow it across awaits."""
    return ws.closed


@pytest.fixture
async def server() -> AsyncIterator[TestServer]:
    app = web.Application()
    app[SEEN] = {}
    app.router.add_get("/", _echo)
    test_server = TestServer(app)
    await test_server.start_server()
    yield test_server
    await test_server.close()


@pytest.fixture
async def session() -> AsyncIterator[aiohttp.ClientSession]:
    async with aiohttp.ClientSession() as client_session:
        yield client_session


async def test_text_binary_and_close(
    server: TestServer, session: aiohttp.ClientSession
) -> None:
    connect = aiohttp_connector(session, timeout=5, user_agent="test-agent")
    ws = await connect(str(server.make_url("/")).replace("http", "ws"))
    assert not _is_closed(ws)
    await ws.send_text("hello")
    assert await ws.receive() == "hello"
    await ws.send_text("binary")
    assert await ws.receive() == b"\x01\x02"
    await ws.send_text("ping")
    assert await ws.receive() == "after-ping"
    await ws.close()
    await ws.close()  # idempotent
    assert _is_closed(ws)
    assert server.app[SEEN]["agent"] == "test-agent"


async def test_receive_returns_none_when_server_closes(
    server: TestServer, session: aiohttp.ClientSession
) -> None:
    connect = aiohttp_connector(session, timeout=5)
    ws = await connect(str(server.make_url("/")).replace("http", "ws"))
    await ws.send_text("bye")
    assert await ws.receive() is None


async def test_close_uses_going_away_code(
    server: TestServer, session: aiohttp.ClientSession
) -> None:
    connect = aiohttp_connector(session, timeout=5)
    ws = await connect(str(server.make_url("/")).replace("http", "ws"))
    await ws.close()
    await server.close()
    assert server.app[SEEN]["close_code"] == CLOSE_CODE_GOING_AWAY


async def test_connect_failure_is_wrapped(session: aiohttp.ClientSession) -> None:
    connect = aiohttp_connector(session, timeout=5)
    with pytest.raises(CommunicationError, match="ClientConnectorError"):
        await connect("ws://127.0.0.1:9/")
