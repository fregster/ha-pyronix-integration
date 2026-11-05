# SPDX-FileCopyrightText: 2026 Paul Fryer
# SPDX-License-Identifier: LGPL-3.0-or-later
"""In-memory and local-socket fake of the Pyronix Cloud relay hosting a fake panel.

Tests connect through :meth:`FakeCloud.connector`, which needs no sockets. A
development Home Assistant can connect over loopback after :meth:`FakeCloud.serve`.
By default every session talks to a fresh :class:`FakePanel` with the default
:class:`PanelBehaviour`, and area states set by commands carry over between
sessions, as on a real panel.
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Protocol, Self

import aiohttp
from aiohttp import web

from pyronix_cloud.testing.cloud_session import (
    CloudBehaviour,
    CloudSession,
    SessionSlot,
)
from pyronix_cloud.testing.fake_panel import FakePanel, PanelBehaviour
from pyronix_cloud.testing.memory_socket import MemoryWebSocket

if TYPE_CHECKING:
    from collections.abc import Callable, Iterable
    from types import TracebackType

    from pyronix_cloud.websocket import Connector, WebSocket

__all__ = ["CloudBehaviour", "CloudSession", "FakeCloud"]


class _Endpoint(Protocol):
    """The server side of one open client connection."""

    session: CloudSession

    async def send_all(self, messages: Iterable[str]) -> None:
        """Send text messages to the client."""

    async def drop(self) -> None:
        """Close the connection from the server side."""


class _MemoryEndpoint:
    """Server side of an in-memory connection."""

    def __init__(self, socket: MemoryWebSocket) -> None:
        self.session = socket.session
        self._socket = socket

    async def send_all(self, messages: Iterable[str]) -> None:
        self._socket.deliver(messages)

    async def drop(self) -> None:
        self._socket.drop()


class _ServedEndpoint:
    """Server side of a connection to the local aiohttp server."""

    def __init__(self, ws: web.WebSocketResponse, session: CloudSession) -> None:
        self.session = session
        self._ws = ws

    async def send_all(self, messages: Iterable[str]) -> None:
        for message in messages:
            if self._ws.closed:
                return
            await self._ws.send_str(message)

    async def drop(self) -> None:
        self.session.end(by_client=False)
        await self._ws.close()


def _shared_state_panels() -> Callable[[], FakePanel]:
    """Return a factory of default panels that share one area state."""
    areas: dict[int, int] = {}
    return lambda: FakePanel(PanelBehaviour(), areas=areas)


class FakeCloud:
    """A fake cloud relay; each client session gets its own fake panel."""

    def __init__(
        self,
        behaviour: CloudBehaviour | None = None,
        panel_factory: Callable[[], FakePanel] | None = None,
    ) -> None:
        """Create the relay. Nothing runs until a client connects.

        Args:
            behaviour: How the relay answers and delivers.
            panel_factory: Makes the panel of each session; by default panels
                with :class:`PanelBehaviour` defaults and shared area state.
        """
        self.behaviour = behaviour or CloudBehaviour()
        self.sessions: list[CloudSession] = []
        self._factory = panel_factory or _shared_state_panels()
        self._slot = SessionSlot()
        self._endpoints: list[_Endpoint] = []
        self._runner: web.AppRunner | None = None

    @property
    def panels(self) -> list[FakePanel]:
        """The panel of every session that reached one, in session order."""
        return [s.panel for s in self.sessions if s.panel is not None]

    def connector(self) -> Connector:
        """Return a connector that opens in-memory sockets to this relay."""

        async def connect(url: str) -> WebSocket:
            socket = MemoryWebSocket(self._new_session(url), self._forget_socket)
            self._endpoints.append(_MemoryEndpoint(socket))
            return socket

        return connect

    async def push(self, text: str) -> None:
        """Push a document from the panel of every logged-in session."""
        for endpoint in list(self._endpoints):
            await endpoint.send_all(endpoint.session.push(text))

    async def heartbeat(self) -> None:
        """Send the cloud heartbeat ``" "`` to every session in panel mode."""
        for endpoint in list(self._endpoints):
            await endpoint.send_all(endpoint.session.heartbeat())

    async def drop(self) -> None:
        """Close every open connection from the server side."""
        for endpoint in list(self._endpoints):
            await endpoint.drop()

    async def serve(self, host: str = "127.0.0.1", port: int = 0) -> str:
        """Serve the relay over WebSocket on a local port.

        Args:
            host: The address to listen on.
            port: The port; 0 picks a free one.

        Returns:
            The ``ws://`` URL to connect to.

        Raises:
            RuntimeError: The relay is already serving.
        """
        if self._runner is not None:
            msg = "the fake cloud is already serving"
            raise RuntimeError(msg)
        app = web.Application()
        app.router.add_get("/", self._handle)
        self._runner = web.AppRunner(app)
        await self._runner.setup()
        await web.TCPSite(self._runner, host, port).start()
        bound_port = self._runner.addresses[0][1]
        return f"ws://{host}:{bound_port}"

    async def stop(self) -> None:
        """Close every connection and stop serving. Idempotent."""
        await self.drop()
        runner, self._runner = self._runner, None
        if runner is not None:
            await runner.cleanup()

    async def __aenter__(self) -> Self:
        """Return the relay; :meth:`stop` runs on exit."""
        return self

    async def __aexit__(
        self,
        exc_type: type[BaseException] | None,
        exc: BaseException | None,
        tb: TracebackType | None,
    ) -> None:
        """Stop the relay."""
        await self.stop()

    def _new_session(self, url: str) -> CloudSession:
        """Create and record a session."""
        session = CloudSession(self.behaviour, self._factory, self._slot, url)
        self.sessions.append(session)
        return session

    def _forget_socket(self, socket: MemoryWebSocket) -> None:
        """Drop the endpoint of a closed in-memory socket."""
        self._endpoints = [
            e for e in self._endpoints if e.session is not socket.session
        ]

    async def _handle(self, request: web.Request) -> web.WebSocketResponse:
        """Run one served WebSocket connection."""
        ws = web.WebSocketResponse()
        await ws.prepare(request)
        endpoint = _ServedEndpoint(ws, self._new_session(str(request.url)))
        self._endpoints.append(endpoint)
        try:
            async for message in ws:
                if message.type is aiohttp.WSMsgType.TEXT:
                    await endpoint.send_all(endpoint.session.receive(message.data))
        finally:
            self._endpoints.remove(endpoint)
            endpoint.session.end(by_client=True)
        return ws
