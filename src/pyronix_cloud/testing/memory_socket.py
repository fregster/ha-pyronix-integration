# SPDX-FileCopyrightText: 2026 Paul Fryer
# SPDX-License-Identifier: LGPL-3.0-or-later
"""In-memory WebSocket that connects a client directly to a fake cloud session.

No sockets, tasks or timers are involved: a message the client sends is handled
at once by the :class:`~pyronix_cloud.testing.cloud_session.CloudSession`, and
the replies wait in a queue until the client receives them.
"""

from __future__ import annotations

import asyncio
from typing import TYPE_CHECKING, override

from pyronix_cloud.errors import ConnectionClosedError
from pyronix_cloud.websocket import WebSocket

if TYPE_CHECKING:
    from collections.abc import Callable, Iterable

    from pyronix_cloud.testing.cloud_session import CloudSession


class MemoryWebSocket(WebSocket):
    """The client end of an in-memory connection to the fake cloud."""

    def __init__(
        self, session: CloudSession, on_close: Callable[[MemoryWebSocket], None]
    ) -> None:
        """Attach to ``session``.

        Args:
            session: The fake cloud session that handles what the client sends.
            on_close: Called once when either side closes the socket.
        """
        self.session = session
        self._on_close = on_close
        self._inbox: asyncio.Queue[str | None] = asyncio.Queue()
        self._closed = False

    @property
    @override
    def closed(self) -> bool:
        return self._closed

    @override
    async def send_text(self, data: str) -> None:
        if self._closed:
            msg = "the fake cloud WebSocket is closed"
            raise ConnectionClosedError(msg)
        self.deliver(self.session.receive(data))

    @override
    async def receive(self) -> str | bytes | None:
        message = await self._inbox.get()
        if message is None:
            self._inbox.put_nowait(None)  # every later receive also sees the close
        return message

    @override
    async def close(self) -> None:
        if self._closed:
            return
        while not self._inbox.empty():
            self._inbox.get_nowait()
        self._shut(by_client=True)

    def deliver(self, messages: Iterable[str]) -> None:
        """Queue messages from the cloud for the client (dropped once closed).

        Args:
            messages: Text messages, in order.
        """
        if not self._closed:
            for message in messages:
                self._inbox.put_nowait(message)

    def drop(self) -> None:
        """Close from the server side; queued messages are still delivered first."""
        if not self._closed:
            self._shut(by_client=False)

    def _shut(self, *, by_client: bool) -> None:
        """Close, end the session and wake any waiting receiver."""
        self._closed = True
        self.session.end(by_client=by_client)
        self._inbox.put_nowait(None)
        self._on_close(self)
