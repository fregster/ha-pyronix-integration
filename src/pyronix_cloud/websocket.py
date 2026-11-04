# SPDX-FileCopyrightText: 2026 Paul Fryer
# SPDX-License-Identifier: LGPL-3.0-or-later
"""The minimal WebSocket interface the client depends on, with an aiohttp adapter.

The client talks to a :class:`WebSocket` obtained from a :data:`Connector`, so tests
can swap in the in-memory fake cloud from :mod:`pyronix_cloud.testing` without
opening sockets.
"""

from __future__ import annotations

import asyncio
from typing import TYPE_CHECKING, Protocol, override

import aiohttp

from pyronix_cloud.errors import CommunicationError

if TYPE_CHECKING:
    from collections.abc import Awaitable, Callable

CLOSE_CODE_GOING_AWAY = 1001
CLOSE_REASON = "User disconnect"
# The probe was tested live with OkHttp's agent string (PROTOCOL.md §3.1).
DEFAULT_USER_AGENT = "okhttp/4.12.0"
# Panel documents are a few KiB; anything far larger is not from a real panel.
MAX_MESSAGE_SIZE = 1 << 20


class WebSocket(Protocol):
    """One open WebSocket to the cloud relay."""

    @property
    def closed(self) -> bool:
        """Whether the socket has been closed by either side."""
        ...

    async def send_text(self, data: str) -> None:
        """Send one text message."""
        ...

    async def receive(self) -> str | bytes | None:
        """Return the next text or binary message, or None once the socket closed."""
        ...

    async def close(self) -> None:
        """Close with code 1001 "User disconnect" (PROTOCOL.md §4.8). Idempotent."""
        ...


type Connector = Callable[[str], Awaitable[WebSocket]]


class AiohttpWebSocket(WebSocket):
    """:class:`WebSocket` backed by an aiohttp client WebSocket."""

    def __init__(self, ws: aiohttp.ClientWebSocketResponse) -> None:
        """Wrap an open aiohttp WebSocket."""
        self._ws = ws

    @property
    @override
    def closed(self) -> bool:
        return self._ws.closed

    @override
    async def send_text(self, data: str) -> None:
        await self._ws.send_str(data)

    @override
    async def receive(self) -> str | bytes | None:
        while True:
            msg = await self._ws.receive()
            if msg.type is aiohttp.WSMsgType.TEXT:
                return str(msg.data)
            if msg.type is aiohttp.WSMsgType.BINARY:
                return bytes(msg.data)
            if msg.type in {
                aiohttp.WSMsgType.CLOSE,
                aiohttp.WSMsgType.CLOSING,
                aiohttp.WSMsgType.CLOSED,
                aiohttp.WSMsgType.ERROR,
            }:
                return None
            # PING/PONG are answered by aiohttp itself; keep reading.

    @override
    async def close(self) -> None:
        if not self._ws.closed:
            await self._ws.close(
                code=CLOSE_CODE_GOING_AWAY, message=CLOSE_REASON.encode()
            )


def aiohttp_connector(
    session: aiohttp.ClientSession,
    *,
    timeout: float,
    user_agent: str = DEFAULT_USER_AGENT,
) -> Connector:
    """Return a :data:`Connector` that opens sockets with ``session``.

    Args:
        session: The aiohttp session to use (Home Assistant passes its shared one).
        timeout: Seconds allowed for the TCP, TLS and upgrade handshake.
        user_agent: The ``User-Agent`` header sent with the upgrade request.
    """

    async def connect(url: str) -> WebSocket:
        try:
            async with asyncio.timeout(timeout):
                ws = await session.ws_connect(
                    url,
                    headers={"User-Agent": user_agent},
                    autoping=True,
                    heartbeat=None,
                    max_msg_size=MAX_MESSAGE_SIZE,
                )
        except (aiohttp.ClientError, TimeoutError) as err:
            msg = f"could not open the cloud WebSocket: {type(err).__name__}"
            raise CommunicationError(msg) from err
        return AiohttpWebSocket(ws)

    return connect
