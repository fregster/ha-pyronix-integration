# SPDX-FileCopyrightText: 2026 Paul Fryer
# SPDX-License-Identifier: LGPL-3.0-or-later
from __future__ import annotations

import asyncio
from typing import TYPE_CHECKING, override

import pytest

from pyronix_cloud import commands
from pyronix_cloud.cloud import CloudIdentity
from pyronix_cloud.connection import Connection, open_socket
from pyronix_cloud.errors import (
    ConnectionClosedError,
    ConnectionSilentError,
    NotConnectedError,
    ProtocolError,
    PyronixError,
    PyronixTimeoutError,
)
from pyronix_cloud.session import DocumentReceived, ProtocolSession
from pyronix_cloud.settings import Timeouts
from pyronix_cloud.testing import dump
from pyronix_cloud.testing.fake_cloud import FakeCloud
from pyronix_cloud.testing.memory_socket import MemoryWebSocket
from pyronix_cloud.transport import wrap
from pyronix_cloud.websocket import WebSocket

if TYPE_CHECKING:
    from collections.abc import AsyncIterator, Callable

    from pyronix_cloud.connection import SessionEvent
    from pyronix_cloud.testing.fake_panel import FakePanel

IDENTITY = CloudIdentity(
    app_user="tester",
    panel_id=dump.PANEL_ID,
    app_password="app-password",  # noqa: S106 - the fake panel's test value
    device_id="device-1",
)


class Faulty(WebSocket):
    """Delegates to a fake cloud socket, failing on request."""

    def __init__(self, inner: WebSocket) -> None:
        self.inner = inner
        self.send_error: BaseException | None = None
        self.receive_error: BaseException | None = None
        self.close_error: BaseException | None = None
        self.hang_close = False

    @property
    @override
    def closed(self) -> bool:
        return self.inner.closed

    @override
    async def send_text(self, data: str) -> None:
        if self.send_error is not None:
            raise self.send_error
        await self.inner.send_text(data)

    @override
    async def receive(self) -> str | bytes | None:
        message = await self.inner.receive()
        if self.receive_error is not None:
            raise self.receive_error
        return message

    @override
    async def close(self) -> None:
        if self.hang_close:
            await asyncio.Event().wait()
        if self.close_error is not None:
            raise self.close_error
        await self.inner.close()


class Harness:
    def __init__(self, fake: FakeCloud, socket: Faulty, timeouts: Timeouts) -> None:
        self.fake = fake
        self.socket = socket
        self.events: list[SessionEvent] = []
        self.ends: list[PyronixError | None] = []
        self.ended = asyncio.Event()
        self.connection = Connection(
            socket,
            ProtocolSession(IDENTITY, "1234"),
            on_event=self.events.append,
            on_end=self._on_end,
            timeouts=timeouts,
        )

    def _on_end(self, error: PyronixError | None) -> None:
        self.ends.append(error)
        self.ended.set()

    @property
    def panel(self) -> FakePanel:
        panel = self.fake.sessions[0].panel
        assert panel is not None
        return panel

    def documents(self) -> int:
        return sum(isinstance(e, DocumentReceived) for e in self.events)


async def until(predicate: Callable[[], bool]) -> None:
    for _ in range(200):
        if predicate():
            return
        await asyncio.sleep(0)
    pytest.fail("condition not reached")


async def ended(harness: Harness) -> PyronixError | None:
    """Wait in real time (bounded) for the session to end; return its error."""
    async with asyncio.timeout(1):
        await harness.ended.wait()
    return harness.ends[0]


@pytest.fixture
async def harness(request: pytest.FixtureRequest) -> AsyncIterator[Harness]:
    timeouts = getattr(request, "param", Timeouts(close=1))
    async with FakeCloud() as fake:
        socket = Faulty(await fake.connector()("memory://"))
        h = Harness(fake, socket, timeouts)
        await h.connection.start()
        await until(lambda: h.documents() == len(dump.login_dump()))
        yield h
        socket.hang_close = False
        socket.close_error = None
        await h.connection.close()
        await socket.inner.close()


async def test_commands_after_close_are_refused(harness: Harness) -> None:
    await harness.connection.close()
    assert harness.panel.goodbye_received
    with pytest.raises(NotConnectedError, match="closing"):
        await harness.connection.send_command(commands.heartbeat())
    assert harness.ends == []  # close() is not an unexpected end


async def test_unexpected_reader_error_ends_the_session(
    harness: Harness, caplog: pytest.LogCaptureFixture
) -> None:
    harness.socket.receive_error = RuntimeError("boom")
    await harness.fake.heartbeat()
    await until(lambda: bool(harness.ends))
    error = harness.ends[0]
    assert isinstance(error, ProtocolError)
    assert isinstance(error.__cause__, RuntimeError)
    assert "Unexpected error" in caplog.text
    assert harness.panel.goodbye_received


async def test_receive_failure_ends_the_session(harness: Harness) -> None:
    harness.socket.receive_error = OSError("reset")
    await harness.fake.heartbeat()
    await until(lambda: bool(harness.ends))
    assert isinstance(harness.ends[0], ConnectionClosedError)
    assert harness.panel.goodbye_received


async def test_send_failure(harness: Harness, caplog: pytest.LogCaptureFixture) -> None:
    harness.socket.send_error = OSError("broken pipe")
    with pytest.raises(ConnectionClosedError, match="sending failed"):
        await harness.connection.send_command(commands.heartbeat())
    caplog.set_level("DEBUG", logger="pyronix_cloud.connection")
    await harness.connection.close()
    assert "Could not send the goodbye" in caplog.text
    assert harness.fake.sessions[0].closed_by_client


async def test_socket_close_failure_is_logged(
    harness: Harness, caplog: pytest.LogCaptureFixture
) -> None:
    caplog.set_level("DEBUG", logger="pyronix_cloud.connection")
    harness.socket.close_error = OSError("gone")
    await harness.connection.close()
    assert harness.panel.goodbye_received
    assert "Closing the WebSocket failed: OSError" in caplog.text


@pytest.mark.parametrize("harness", [Timeouts(close=0.02)], indirect=True)
async def test_close_is_bounded(
    harness: Harness, caplog: pytest.LogCaptureFixture
) -> None:
    caplog.set_level("DEBUG", logger="pyronix_cloud.connection")
    harness.socket.hang_close = True
    await harness.connection.close()
    assert harness.panel.goodbye_received
    assert "Goodbye not finished" in caplog.text


async def test_cancelled_close_still_says_goodbye(harness: Harness) -> None:
    task = asyncio.create_task(harness.connection.close())
    await asyncio.sleep(0)
    task.cancel()
    with pytest.raises(asyncio.CancelledError):
        await task
    await until(lambda: harness.panel.goodbye_received)
    await harness.connection.close()
    assert harness.fake.sessions[0].closed_by_client


async def test_open_socket_timeout() -> None:
    async def connector(_url: str) -> WebSocket:
        await asyncio.Event().wait()
        raise AssertionError  # pragma: no cover - never reached

    with pytest.raises(PyronixTimeoutError):
        await open_socket(connector, "memory://", 0.01)


async def test_panel_goodbye_ends_cleanly(harness: Harness) -> None:
    inner = harness.socket.inner
    assert isinstance(inner, MemoryWebSocket)
    inner.deliver([wrap(harness.panel._send("e", b""))])
    await until(lambda: bool(harness.ends))
    assert harness.ends == [None]
    assert harness.fake.sessions[0].closed_by_client


async def test_server_drop_ends_the_session(harness: Harness) -> None:
    await harness.fake.drop()
    await until(lambda: bool(harness.ends))
    assert isinstance(harness.ends[0], ConnectionClosedError)
    assert not harness.fake.sessions[0].closed_by_client


@pytest.mark.parametrize("harness", [Timeouts(close=1, silence=0.05)], indirect=True)
async def test_silence_ends_the_session(harness: Harness) -> None:
    error = await ended(harness)
    assert isinstance(error, ConnectionSilentError)
    assert isinstance(error, ConnectionClosedError)
    assert "no message" in str(error)
    assert harness.panel.goodbye_received
    assert harness.fake.sessions[0].closed_by_client


@pytest.mark.parametrize("harness", [Timeouts(close=1, silence=0.1)], indirect=True)
async def test_cloud_heartbeats_keep_the_session_alive(harness: Harness) -> None:
    for _ in range(12):  # 0.24 s in all, more than twice the silence window
        await asyncio.sleep(0.02)
        await harness.fake.heartbeat()
    assert harness.ends == []
    assert isinstance(await ended(harness), ConnectionSilentError)
