# SPDX-FileCopyrightText: 2026 Paul Fryer
# SPDX-License-Identifier: LGPL-3.0-or-later
"""Drive a ProtocolSession over a WebSocket: reader task, sends and goodbye.

A :class:`Connection` owns one open socket and one
:class:`~pyronix_cloud.session.ProtocolSession`. A reader task feeds every
inbound message into the session, sends what the session asks for and hands
the events to a callback. Every way out, whether a reader error, a panel
goodbye, :meth:`Connection.close` or cancellation, sends the session's goodbye
(best effort) and closes the socket (PROTOCOL.md §4.8).
"""

from __future__ import annotations

import asyncio
from functools import partial
import logging
from typing import TYPE_CHECKING, Final

import aiohttp

from pyronix_cloud.errors import (
    ConnectionClosedError,
    NotConnectedError,
    ProtocolError,
    PyronixError,
    PyronixTimeoutError,
)
from pyronix_cloud.session import (
    DocumentReceived,
    PanelHeartbeat,
    SessionEnded,
    StageChanged,
)

if TYPE_CHECKING:
    from collections.abc import Callable

    from pyronix_cloud.session import ProtocolSession, SessionOutput
    from pyronix_cloud.websocket import Connector, WebSocket

_LOGGER = logging.getLogger(__name__)

_SOCKET_ERRORS: Final = (OSError, aiohttp.ClientError)

type SessionEvent = StageChanged | DocumentReceived | PanelHeartbeat | SessionEnded
type EventHandler = Callable[[SessionEvent], None]
type EndHandler = Callable[[PyronixError | None], None]


async def open_socket(connector: Connector, url: str, within: float) -> WebSocket:
    """Open the WebSocket through ``connector`` within ``within`` seconds.

    Raises:
        PyronixTimeoutError: The socket did not open in time.
        CommunicationError: The connector could not reach the relay.
    """
    try:
        async with asyncio.timeout(within):
            return await connector(url)
    except TimeoutError as err:
        msg = "timed out opening the cloud WebSocket"
        raise PyronixTimeoutError(msg) from err


class Connection:
    """One protocol session running over one open WebSocket."""

    def __init__(
        self,
        socket: WebSocket,
        session: ProtocolSession,
        *,
        on_event: EventHandler,
        on_end: EndHandler,
        close_timeout: float,
    ) -> None:
        """Attach to an open socket; nothing is sent until :meth:`start`.

        Args:
            socket: The open WebSocket to the cloud relay.
            session: A session that has not been started.
            on_event: Called with each session event, after the messages the
                session produced for it have been sent.
            on_end: Called once if the session ends on its own: with the error,
                or None after a clean panel goodbye. Not called after
                :meth:`close`.
            close_timeout: Seconds allowed for the goodbye and socket close.
        """
        self._socket = socket
        self._session = session
        self._on_event = on_event
        self._on_end = on_end
        self._close_timeout = close_timeout
        self._lock = asyncio.Lock()
        self._reader: asyncio.Task[None] | None = None
        self._closer: asyncio.Task[None] | None = None
        self._ending = False

    async def start(self) -> None:
        """Send the session's first request and start the reader task.

        Raises:
            ConnectionClosedError: The socket failed while sending.
        """
        await self._exchange(self._session.start)
        self._reader = asyncio.get_running_loop().create_task(
            self._read(), name="pyronix_cloud reader"
        )

    async def send_command(self, payload: bytes) -> None:
        """Send one application command to the panel.

        Args:
            payload: A payload from :mod:`pyronix_cloud.commands`.

        Raises:
            NotConnectedError: The connection is closing or not in data mode.
            ConnectionClosedError: The socket failed while sending.
        """
        if self._closer is not None:
            msg = "the panel session is closing"
            raise NotConnectedError(msg)
        await self._exchange(lambda: self._session.send_command(payload))

    async def close(self) -> None:
        """Say goodbye, close the socket and stop the reader. Idempotent.

        The work runs in its own task, so cancelling the caller does not stop
        the goodbye; it is bounded by ``close_timeout``.
        """
        if self._closer is None:
            self._closer = asyncio.get_running_loop().create_task(
                self._close(), name="pyronix_cloud close"
            )
        await asyncio.shield(self._closer)
        reader = self._reader
        if reader is not None and reader is not asyncio.current_task():
            await asyncio.wait([reader])

    async def _exchange(self, step: Callable[[], SessionOutput]) -> bool:
        """Run one session step, send its output, then dispatch its events.

        The lock keeps frames in the order the session numbered them.

        Returns:
            True if the panel ended the session.
        """
        async with self._lock:
            output = step()
            for message in output.outbound:
                await self._send(message)
        for event in output.events:
            self._on_event(event)
        return any(isinstance(event, SessionEnded) for event in output.events)

    async def _read(self) -> None:
        """Feed inbound messages to the session until it ends or fails."""
        error: PyronixError | None = None
        try:
            while True:
                message = await self._receive()
                if await self._exchange(partial(self._session.receive, message)):
                    break
        except PyronixError as err:
            error = err
        except Exception as err:
            _LOGGER.exception("Unexpected error in the panel session reader")
            error = ProtocolError(f"unexpected {type(err).__name__} in the reader")
            error.__cause__ = err
        self._ending = True
        _LOGGER.debug("Panel session ended: %s", error or "panel goodbye")
        await self.close()
        self._on_end(error)

    async def _send(self, message: str) -> None:
        """Send one text message, mapping socket failures to library errors."""
        try:
            await self._socket.send_text(message)
        except _SOCKET_ERRORS as err:
            msg = f"sending failed: {type(err).__name__}"
            raise ConnectionClosedError(msg) from err

    async def _receive(self) -> str | bytes:
        """Return the next message, or raise once the socket has closed."""
        try:
            message = await self._socket.receive()
        except _SOCKET_ERRORS as err:
            msg = f"receiving failed: {type(err).__name__}"
            raise ConnectionClosedError(msg) from err
        if message is None:
            msg = "the cloud relay closed the WebSocket"
            raise ConnectionClosedError(msg)
        return message

    async def _close(self) -> None:
        """Send the goodbye and close the socket within the close timeout."""
        try:
            async with asyncio.timeout(self._close_timeout):
                await self._goodbye()
        except TimeoutError:
            _LOGGER.debug("Goodbye not finished within %.1f s", self._close_timeout)
        self._stop_reader()

    async def _goodbye(self) -> None:
        """Stop the reader, send the session's goodbye, then close the socket."""
        async with self._lock:
            self._stop_reader()
            for message in self._session.close().outbound:
                try:
                    await self._send(message)
                except PyronixError as err:
                    _LOGGER.debug("Could not send the goodbye: %s", err)
                    break
        try:
            await self._socket.close()
        except _SOCKET_ERRORS as err:
            _LOGGER.debug("Closing the WebSocket failed: %s", type(err).__name__)

    def _stop_reader(self) -> None:
        """Cancel the reader unless it is the task ending the session."""
        if self._reader is not None and not self._ending:
            self._reader.cancel()
