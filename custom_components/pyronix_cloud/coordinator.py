# SPDX-FileCopyrightText: 2026 Paul Fryer
# SPDX-License-Identifier: Apache-2.0
"""Run panel sessions and share the latest panel snapshot with the entities."""

from __future__ import annotations

import asyncio
from contextlib import asynccontextmanager
from datetime import timedelta
import logging
from typing import TYPE_CHECKING, Final, override

from homeassistant.config_entries import ConfigEntry
from homeassistant.exceptions import ConfigEntryAuthFailed, HomeAssistantError
from homeassistant.helpers.update_coordinator import DataUpdateCoordinator, UpdateFailed

from pyronix_cloud import (
    AccessDeniedError,
    AuthenticationError,
    CommandNotConfirmedError,
    NotConnectedError,
    NotPermittedError,
    PanelBusyError,
    PanelSnapshot,
    PyronixError,
    SetRefusedError,
)

from .client_factory import create_client
from .const import (
    CONF_MODE,
    CONF_SCAN_INTERVAL,
    DEFAULT_SCAN_INTERVAL,
    DOMAIN,
    MODE_PERSISTENT,
    ConnectionState,
)
from .live import LiveSession

if TYPE_CHECKING:
    from collections.abc import AsyncIterator, Awaitable, Callable, Iterable

    from homeassistant.core import HomeAssistant

    from pyronix_cloud import PyronixClient

_LOGGER = logging.getLogger(__name__)

AUTH_ERRORS: Final = (AuthenticationError, AccessDeniedError)
"""Errors that mean the stored credentials no longer work."""

_COMMAND_ERRORS: Final[tuple[tuple[type[PyronixError], str], ...]] = (
    (NotPermittedError, "not_permitted"),
    (CommandNotConfirmedError, "command_not_confirmed"),
    (NotConnectedError, "not_connected"),
    (AuthenticationError, "auth_failed"),
    (AccessDeniedError, "auth_failed"),
    (PanelBusyError, "panel_busy"),
)

type PyronixConfigEntry = ConfigEntry[PyronixCoordinator]
type ClientAction = Callable[[PyronixClient], Awaitable[PanelSnapshot]]


def update_failed(err: PyronixError) -> UpdateFailed:
    """Return the translated UpdateFailed for a failed session."""
    return UpdateFailed(
        translation_domain=DOMAIN,
        translation_key="update_failed",
        translation_placeholders={"error": type(err).__name__},
    )


def _command_error(err: PyronixError) -> HomeAssistantError:
    """Return the translated HomeAssistantError for a failed arm or disarm."""
    if isinstance(err, SetRefusedError):
        return HomeAssistantError(
            translation_domain=DOMAIN,
            translation_key="arm_refused",
            translation_placeholders={"area": err.area.name, "reason": err.reason},
        )
    key = next(
        (key for kind, key in _COMMAND_ERRORS if isinstance(err, kind)),
        "command_failed",
    )
    return HomeAssistantError(
        translation_domain=DOMAIN,
        translation_key=key,
        translation_placeholders={"error": type(err).__name__},
    )


class PyronixCoordinator(DataUpdateCoordinator[PanelSnapshot]):
    """Owns the panel client; polls it or keeps it connected, by the entry's mode.

    The panel allows one remote session at a time, so one lock serialises every
    session and command.
    """

    config_entry: PyronixConfigEntry

    def __init__(self, hass: HomeAssistant, entry: PyronixConfigEntry) -> None:
        """Create the client for ``entry``; nothing connects until the first refresh.

        Args:
            hass: The Home Assistant instance.
            entry: The config entry with the credentials and options.
        """
        persistent = entry.options.get(CONF_MODE) == MODE_PERSISTENT
        minutes = entry.options.get(CONF_SCAN_INTERVAL, DEFAULT_SCAN_INTERVAL)
        super().__init__(
            hass,
            _LOGGER,
            config_entry=entry,
            name=DOMAIN,
            update_interval=None if persistent else timedelta(minutes=minutes),
            always_update=False,
        )
        # Sessions follow each other closely (polls, commands, reconnects), and
        # the cloud may refuse one with `n` right after the last goodbye.
        self.client = create_client(hass, entry.data, retry_not_polling=True)
        self.lock = asyncio.Lock()
        self._state = ConnectionState.IDLE
        self._live = LiveSession(self) if persistent else None

    @property
    def persistent(self) -> bool:
        """Whether the session is kept open (the "live updates" option)."""
        return self._live is not None

    @property
    def connection_state(self) -> ConnectionState:
        """The state of the link to the panel."""
        return self._state

    def set_connection_state(self, state: ConnectionState) -> None:
        """Record a new link state and tell the entities if it changed."""
        if state is not self._state:
            self._state = state
            self.async_update_listeners()

    def async_connection_lost(self, error: PyronixError) -> None:
        """Mark the entities unavailable after the live session dropped."""
        self.set_connection_state(ConnectionState.ERROR)
        self.async_set_update_error(update_failed(error))

    async def async_arm(self, areas: Iterable[int], *, force: bool = True) -> None:
        """Set areas and publish the snapshot that confirmed it.

        Args:
            areas: Area numbers.
            force: Omit open inputs and set anyway, as the app does.

        Raises:
            HomeAssistantError: The panel did not set the areas.
        """
        numbers = tuple(areas)
        await self._async_command(lambda client: client.arm(numbers, force=force))

    async def async_disarm(self, areas: Iterable[int]) -> None:
        """Unset areas and publish the snapshot that confirmed it.

        Args:
            areas: Area numbers.

        Raises:
            HomeAssistantError: The panel did not unset the areas.
        """
        numbers = tuple(areas)
        await self._async_command(lambda client: client.disarm(numbers))

    async def async_close(self) -> None:
        """Stop reconnecting, say goodbye and close the session. Idempotent."""
        if self._live is not None:
            await self._live.async_stop()
        else:
            await self.client.close()
        self.set_connection_state(ConnectionState.IDLE)

    @override
    async def async_shutdown(self) -> None:
        """Cancel scheduled polls and close the panel session."""
        await super().async_shutdown()
        await self.async_close()

    @override
    async def _async_update_data(self) -> PanelSnapshot:
        """Return the panel state: a short session, or the live session's state."""
        try:
            if self._live is not None:
                return await self._live.async_connect()
            async with self._session() as client:
                return client.snapshot
        except AUTH_ERRORS as err:
            raise ConfigEntryAuthFailed(
                translation_domain=DOMAIN, translation_key="auth_failed"
            ) from err
        except PyronixError as err:
            raise update_failed(err) from err

    async def _async_command(self, action: ClientAction) -> None:
        """Run a command in a session and publish the snapshot it returns."""
        try:
            if self._live is not None:
                async with self.lock:
                    snapshot = await action(self.client)
            else:
                async with self._session() as client:
                    snapshot = await action(client)
        except PyronixError as err:
            if isinstance(err, AUTH_ERRORS):
                self.config_entry.async_start_reauth(self.hass)
            raise _command_error(err) from err
        self.async_set_updated_data(snapshot)

    @asynccontextmanager
    async def _session(self) -> AsyncIterator[PyronixClient]:
        """Hold the lock for one short session; it always ends with a goodbye."""
        async with self.lock:
            self.set_connection_state(ConnectionState.CONNECTING)
            connected = False
            try:
                async with self.client:
                    connected = True
                    self.set_connection_state(ConnectionState.CONNECTED)
                    yield self.client
            finally:
                state = ConnectionState.IDLE if connected else ConnectionState.ERROR
                self.set_connection_state(state)
