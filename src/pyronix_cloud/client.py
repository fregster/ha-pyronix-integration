# SPDX-FileCopyrightText: 2026 Paul Fryer
# SPDX-License-Identifier: LGPL-3.0-or-later
"""Async client for one panel session over the cloud relay with arm and disarm commands.

:class:`PyronixClient` opens a WebSocket through an injected
:data:`~pyronix_cloud.websocket.Connector`, runs the protocol session to the end
of the login dump (:mod:`pyronix_cloud.connection`), keeps the panel state
(:mod:`pyronix_cloud.panel_feed`) and sends set/unset commands through
:mod:`pyronix_cloud.area_commands`, which waits for the panel to confirm them.
It never logs the app password, the user code or the login payload.
"""

from __future__ import annotations

from contextlib import suppress
import logging
from typing import TYPE_CHECKING, Self

from pyronix_cloud import commands
from pyronix_cloud.area_commands import arm_areas, disarm_areas
from pyronix_cloud.cloud import LIVE_URL, CloudIdentity
from pyronix_cloud.command_guard import CommandGuard
from pyronix_cloud.connection import Connection, open_socket
from pyronix_cloud.errors import (
    ConnectionClosedError,
    NotConnectedError,
    PyronixError,
)
from pyronix_cloud.not_polling_retry import NotPollingRetry, RetryPolicy
from pyronix_cloud.panel_feed import PanelFeed
from pyronix_cloud.session import ProtocolSession
from pyronix_cloud.settings import Credentials, Timeouts

if TYPE_CHECKING:
    from collections.abc import Callable, Iterable
    from types import TracebackType

    from pyronix_cloud.panel_feed import SnapshotListener
    from pyronix_cloud.panel_state import PanelSnapshot
    from pyronix_cloud.websocket import Connector

__all__ = ["Credentials", "PyronixClient", "RetryPolicy", "Timeouts"]

_LOGGER = logging.getLogger(__name__)

type DisconnectListener = Callable[[BaseException | None], None]


class PyronixClient:
    """One panel session over the Pyronix Cloud relay."""

    def __init__(
        self,
        credentials: Credentials,
        connector: Connector,
        *,
        url: str = LIVE_URL,
        timeouts: Timeouts | None = None,
        retry: RetryPolicy | None = None,
    ) -> None:
        """Prepare the client; nothing is sent until :meth:`connect`.

        Args:
            credentials: The panel identity and secrets.
            connector: Opens the WebSocket (see ``aiohttp_connector``).
            url: The cloud relay URL.
            timeouts: Phase timeouts; :class:`Timeouts` defaults if None.
            retry: Connection retries after cloud reply ``n``;
                :class:`RetryPolicy` defaults if None.

        Raises:
            ValueError: A credential is malformed (the value is not echoed).
        """
        self._user_code = credentials.user_code
        self._identity = CloudIdentity(
            app_user=credentials.app_user,
            panel_id=credentials.panel_id,
            app_password=credentials.app_password,
            device_id=credentials.device_id,
            push_id=credentials.push_id,
        )
        commands.login(credentials.user_code)  # validate the code now, not later
        self._connector = connector
        self._url = url
        self._timeouts = timeouts or Timeouts()
        self._feed = PanelFeed(self._timeouts)
        self._retry = NotPollingRetry(retry or RetryPolicy())
        self._connection: Connection | None = None
        self._connected = False
        self._disconnect_listeners: list[DisconnectListener] = []
        self._command_guard = CommandGuard()

    @property
    def snapshot(self) -> PanelSnapshot:
        """The latest panel state."""
        return self._feed.snapshot

    @property
    def connected(self) -> bool:
        """Whether a session is in data mode with its login dump complete."""
        return self._connected

    def add_listener(self, listener: SnapshotListener) -> Callable[[], None]:
        """Call ``listener`` with each changed snapshot; returns a remover."""
        return self._feed.add_listener(listener)

    def add_disconnect_listener(
        self, listener: DisconnectListener
    ) -> Callable[[], None]:
        """Call ``listener`` when a connected session ends on its own.

        It gets the error, or None after a clean panel goodbye. It is not
        called after :meth:`close`. Returns a remover.
        """
        self._disconnect_listeners.append(listener)

        def remove() -> None:
            with suppress(ValueError):
                self._disconnect_listeners.remove(listener)

        return remove

    async def connect(self) -> PanelSnapshot:
        """Open a session and wait for the panel's login dump.

        A connection the cloud refuses with ``n`` is retried as the retry
        policy says, before any panel command can be sent. On any failure or
        cancellation the goodbye is sent and the socket closed before the
        error propagates; :meth:`close` also ends a pending retry.

        Returns:
            The snapshot once the dump is complete.

        Raises:
            RuntimeError: A session is already open.
            CommunicationError: The cloud relay could not be reached.
            CloudError: The cloud relay refused the session.
            AuthenticationError: The panel rejected the credentials.
            PyronixTimeoutError: A stage or the dump did not finish in time.
            PyronixError: Any other protocol or socket failure.
        """
        return await self._retry.run(self._connect_once)

    async def _connect_once(self) -> PanelSnapshot:
        """Make one connection attempt; see :meth:`connect`."""
        if self._connection is not None:
            msg = "the client already has a session; close it first"
            raise RuntimeError(msg)
        self._feed.restart()
        session = ProtocolSession(self._identity, self._user_code)
        connection = Connection(
            await open_socket(self._connector, self._url, self._timeouts.connect),
            session,
            on_event=self._feed.handle,
            on_end=self._on_end,
            close_timeout=self._timeouts.close,
        )
        self._connection = connection
        try:
            await connection.start()
            await self._feed.wait_for_dump()
        except BaseException:
            self._connection = None
            await connection.close()
            raise
        self._connected = True
        _LOGGER.debug("Connected; login dump complete")
        return self.snapshot

    async def arm(self, areas: Iterable[int], *, force: bool = True) -> PanelSnapshot:
        """Set areas and wait until each is setting, set or in alarm.

        Without ``force`` an area that is not ready is refused before anything
        is sent, and a refusal the panel reports afterwards ends the wait at once.

        Args:
            areas: Area numbers.
            force: Omit open inputs and set anyway (as the app does).

        Returns:
            The snapshot that confirmed the command.

        Raises:
            NotConnectedError: No session is connected.
            ValueError: The area numbers are invalid.
            NotPermittedError: The user may not set one of the areas.
            CommandInProgressError: Another command is running on this client.
            SetRefusedError: Without ``force``, an area cannot be set; the
                error carries the area and its faults.
            CommandNotConfirmedError: The panel did not confirm in time.
        """
        with self._command_guard.hold():
            return await arm_areas(
                self._require_connection(),
                self._feed,
                self._timeouts.command,
                tuple(areas),
                force=force,
            )

    async def disarm(self, areas: Iterable[int]) -> PanelSnapshot:
        """Unset areas and wait until each is unset.

        Args:
            areas: Area numbers.

        Returns:
            The snapshot that confirmed the command (at once, without sending,
            if every area is already unset).

        Raises:
            NotConnectedError: No session is connected.
            ValueError: The area numbers are invalid.
            NotPermittedError: The user may not unset one of the areas.
            CommandInProgressError: Another command is running on this client.
            CommandNotConfirmedError: The panel did not confirm in time.
        """
        with self._command_guard.hold():
            return await disarm_areas(
                self._require_connection(),
                self._feed,
                self._timeouts.command,
                tuple(areas),
            )

    async def close(self) -> None:
        """Say goodbye and close the socket.

        Idempotent. The goodbye completes even if the caller is cancelled, and
        takes at most ``Timeouts.close``.
        """
        self._connected = False
        self._retry.stop()
        connection, self._connection = self._connection, None
        if connection is not None:
            await connection.close()
            self._feed.end(ConnectionClosedError("the client closed the session"))

    async def __aenter__(self) -> Self:
        """Connect and return the client."""
        await self.connect()
        return self

    async def __aexit__(
        self,
        exc_type: type[BaseException] | None,
        exc: BaseException | None,
        tb: TracebackType | None,
    ) -> None:
        """Close the session."""
        await self.close()

    def _require_connection(self) -> Connection:
        """Return the connection, or raise if no session is connected."""
        if not self._connected or self._connection is None:
            msg = "the panel session is not connected"
            raise NotConnectedError(msg)
        return self._connection

    def _on_end(self, error: PyronixError | None) -> None:
        """Record that the session ended and tell the disconnect listeners."""
        self._feed.end(error)
        was_connected, self._connected = self._connected, False
        if not was_connected:
            return
        self._connection = None
        if error is None:
            _LOGGER.debug("The panel ended the session")
        else:
            _LOGGER.warning("The panel session ended: %s", error)
        for listener in list(self._disconnect_listeners):
            try:
                listener(error)
            except Exception:
                _LOGGER.exception("Error in a disconnect listener")
