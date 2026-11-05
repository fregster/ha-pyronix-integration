# SPDX-FileCopyrightText: 2026 Paul Fryer
# SPDX-License-Identifier: Apache-2.0
"""Keep a persistent panel session open and reconnect with back-off."""

from __future__ import annotations

import asyncio
import logging
from typing import TYPE_CHECKING, Final

from homeassistant.core import callback
from homeassistant.helpers.event import async_call_later

from pyronix_cloud import (
    AccessDeniedError,
    AuthenticationError,
    Backoff,
    ConnectionClosedError,
    PyronixError,
)

from .const import ConnectionState

if TYPE_CHECKING:
    from collections.abc import Callable
    from datetime import datetime

    from pyronix_cloud import PanelSnapshot

    from .coordinator import PyronixCoordinator

_LOGGER = logging.getLogger(__name__)

_AUTH_ERRORS: Final = (AuthenticationError, AccessDeniedError)


def new_backoff() -> Backoff:
    """Return the reconnect back-off: 5 s doubling to 5 min (tests shorten it)."""
    return Backoff()


class LiveSession:
    """The persistent ("live updates") session of one coordinator.

    Pushed snapshots go to the coordinator as they arrive. When the session drops,
    reconnects wait for a growing back-off and stop for good, starting reauth,
    when the panel rejects the credentials.
    """

    def __init__(self, coordinator: PyronixCoordinator) -> None:
        """Listen to the coordinator's client; nothing connects yet.

        Args:
            coordinator: Owns the client, the session lock and the entities' data.
        """
        self._coordinator = coordinator
        self._client = coordinator.client
        self._backoff = new_backoff()
        self._unsub_timer: Callable[[], None] | None = None
        self._task: asyncio.Task[None] | None = None
        self._removers = (
            self._client.add_listener(self._on_snapshot),
            self._client.add_disconnect_listener(self._on_disconnect),
        )

    async def async_connect(self) -> PanelSnapshot:
        """Connect unless already connected, and return the panel state.

        Raises:
            PyronixError: The connection failed; nothing is scheduled.
        """
        coordinator = self._coordinator
        async with coordinator.lock:
            if self._client.connected:
                return self._client.snapshot
            coordinator.set_connection_state(ConnectionState.CONNECTING)
            try:
                snapshot = await self._client.connect()
            except BaseException:
                coordinator.set_connection_state(ConnectionState.ERROR)
                raise
            self._backoff.reset()
            self._cancel_timer()
            coordinator.set_connection_state(ConnectionState.CONNECTED)
            _LOGGER.debug("Live panel session connected")
            return snapshot

    async def async_stop(self) -> None:
        """Stop reconnecting, then say goodbye and close the session. Idempotent."""
        for remove in self._removers:
            remove()
        self._cancel_timer()
        task, self._task = self._task, None
        if task is not None and not task.done():
            task.cancel()
            await asyncio.wait([task])
        await self._client.close()

    def _on_snapshot(self, snapshot: PanelSnapshot) -> None:
        """Publish a pushed change once the login dump is complete."""
        if self._client.connected:
            self._coordinator.async_set_updated_data(snapshot)

    def _on_disconnect(self, error: BaseException | None) -> None:
        """Mark the entities unavailable and schedule a reconnect."""
        if not isinstance(error, PyronixError):
            error = ConnectionClosedError("the panel said goodbye")
        self._coordinator.async_connection_lost(error)
        self._schedule()

    def _schedule(self) -> None:
        """Try again after the next back-off delay."""
        delay = self._backoff.next_delay()
        _LOGGER.info("Reconnecting to the panel in %.0f seconds", delay)
        self._unsub_timer = async_call_later(
            self._coordinator.hass, delay, self._on_timer
        )

    @callback
    def _on_timer(self, _now: datetime) -> None:
        """Start the reconnect attempt in a task tracked by the config entry."""
        self._unsub_timer = None
        coordinator = self._coordinator
        self._task = coordinator.config_entry.async_create_background_task(
            coordinator.hass, self._reconnect(), "pyronix_cloud reconnect"
        )

    async def _reconnect(self) -> None:
        """Make one reconnect attempt; on failure schedule the next or start reauth."""
        coordinator = self._coordinator
        try:
            snapshot = await self.async_connect()
        except _AUTH_ERRORS:
            _LOGGER.warning("The panel rejected the stored credentials; reauth needed")
            coordinator.config_entry.async_start_reauth(coordinator.hass)
            return
        except PyronixError as err:
            _LOGGER.debug("Reconnect failed: %s", type(err).__name__)
            self._schedule()
            return
        coordinator.async_set_updated_data(snapshot)

    def _cancel_timer(self) -> None:
        """Cancel a scheduled reconnect, if any."""
        unsub, self._unsub_timer = self._unsub_timer, None
        if unsub is not None:
            unsub()
