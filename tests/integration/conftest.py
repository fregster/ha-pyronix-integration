# SPDX-FileCopyrightText: 2026 Paul Fryer
# SPDX-License-Identifier: Apache-2.0
"""Fixtures for the integration tests: a fake cloud, short timeouts, config entries."""

from __future__ import annotations

import asyncio
import json
from typing import TYPE_CHECKING, Any
from unittest.mock import patch

from homeassistant.config_entries import ConfigEntryState
import pytest
from pytest_homeassistant_custom_component.common import MockConfigEntry  # type: ignore[import-untyped]  # the HA test plugin ships no py.typed

from custom_components.pyronix_cloud.const import (
    CONF_APP_PASSWORD,
    CONF_APP_USER,
    CONF_DEVICE_ID,
    CONF_MODE,
    CONF_PANEL_ID,
    CONF_USER_CODE,
    DOMAIN,
    MODE_PERSISTENT,
    ConnectionState,
)
from pyronix_cloud import Backoff, Timeouts
from pyronix_cloud.testing import dump
from pyronix_cloud.testing.fake_cloud import CloudBehaviour, FakeCloud
from pyronix_cloud.testing.fake_panel import FakePanel, PanelBehaviour

if TYPE_CHECKING:
    from collections.abc import AsyncIterator, Callable

    from homeassistant.config_entries import ConfigEntry
    from homeassistant.core import HomeAssistant

    from custom_components.pyronix_cloud.coordinator import PyronixCoordinator
    from pyronix_cloud.testing.cloud_session import CloudSession
    from pyronix_cloud.websocket import WebSocket

FAST = Timeouts(connect=1, step=1, dump=1, command=0.2, close=1)
BACKOFF_SECONDS = 5.0
DEVICE_ID = "0123456789abcdef"
ENTRY_DATA: dict[str, Any] = {
    CONF_APP_USER: "Home Assistant",
    CONF_PANEL_ID: dump.PANEL_ID,
    CONF_APP_PASSWORD: "app-password",  # the fake panel's test value
    CONF_USER_CODE: "1234",
    CONF_DEVICE_ID: DEVICE_ID,
}
PERSISTENT = {CONF_MODE: MODE_PERSISTENT, "scan_interval": 2}


class Gate:
    """Lets a test make new connections hang, or fail before reaching the cloud."""

    def __init__(self) -> None:
        self.hang = False
        self.error: Exception | None = None


def said_goodbye(session: CloudSession) -> bool:
    """Whether the client said goodbye and closed the socket itself."""
    panel = session.panel
    return panel is not None and panel.goodbye_received and session.closed_by_client


def dump_with(site_name: str | None) -> list[str]:
    """Return a login dump with a site name, or with no panel document if None."""
    documents = dump.login_dump()
    if site_name is None:
        return documents[1:]
    panel = json.loads(documents[0])
    panel["SiteName"] = site_name
    return [json.dumps(panel), *documents[1:]]


def link_state(coordinator: PyronixCoordinator) -> ConnectionState:
    """Read the link state afresh (a call, so mypy does not narrow it)."""
    return coordinator.connection_state


def entry_state(entry: ConfigEntry) -> ConfigEntryState:
    """Read an entry's state afresh (a call, so mypy does not narrow it)."""
    return entry.state


async def until(predicate: Callable[[], bool]) -> None:
    """Yield to the loop until ``predicate`` holds."""
    for _ in range(500):
        if predicate():
            return
        await asyncio.sleep(0)
    pytest.fail("condition not reached")


@pytest.fixture(autouse=True)
def auto_enable_custom_integrations(enable_custom_integrations: None) -> None:
    """Load custom_components/ in every test."""


@pytest.fixture
def cloud_behaviour() -> CloudBehaviour:
    """How the fake cloud answers; parametrise to change it."""
    return CloudBehaviour()


@pytest.fixture
def panel_behaviour() -> PanelBehaviour:
    """How each fake panel answers; parametrise to change it."""
    return PanelBehaviour()


@pytest.fixture
def gate() -> Gate:
    """Control over new connections."""
    return Gate()


@pytest.fixture(autouse=True)
async def cloud(
    cloud_behaviour: CloudBehaviour, panel_behaviour: PanelBehaviour, gate: Gate
) -> AsyncIterator[FakeCloud]:
    """Route every client to an in-memory fake cloud; no test opens a socket."""
    areas: dict[int, int] = {}

    def panel() -> FakePanel:
        return FakePanel(panel_behaviour, areas=areas)

    async with FakeCloud(cloud_behaviour, panel) as fake:
        inner = fake.connector()

        async def connect(url: str) -> WebSocket:
            if gate.error is not None:
                raise gate.error
            if gate.hang:
                await asyncio.Event().wait()
            return await inner(url)

        with (
            patch(
                "custom_components.pyronix_cloud.client_factory.connector_for",
                return_value=connect,
            ),
            patch(
                "custom_components.pyronix_cloud.client_factory.session_timeouts",
                return_value=FAST,
            ),
            patch(
                "custom_components.pyronix_cloud.live.new_backoff",
                side_effect=lambda: Backoff(
                    initial=BACKOFF_SECONDS, maximum=BACKOFF_SECONDS, jitter=0
                ),
            ),
        ):
            yield fake


@pytest.fixture
def entry_options() -> dict[str, Any]:
    """Options of the mock entry; parametrise for persistent mode."""
    return {}


@pytest.fixture
def entry(hass: HomeAssistant, entry_options: dict[str, Any]) -> MockConfigEntry:
    """A config entry with the fake panel's credentials, added to hass."""
    mock = MockConfigEntry(
        domain=DOMAIN,
        title="Pyronix alarm",
        data=ENTRY_DATA,
        options=entry_options,
        unique_id=dump.PANEL_ID,
    )
    mock.add_to_hass(hass)
    return mock


@pytest.fixture(autouse=True)
async def unload_entries(
    hass: HomeAssistant,
    cloud: FakeCloud,  # noqa: ARG001 - requested so this tears down before the cloud
) -> AsyncIterator[None]:
    """Unload every loaded entry before the fake cloud and hass stop."""
    yield
    for config_entry in hass.config_entries.async_entries(DOMAIN):
        if config_entry.state is ConfigEntryState.LOADED:
            await hass.config_entries.async_unload(config_entry.entry_id)
    await hass.async_block_till_done()


@pytest.fixture
async def loaded(hass: HomeAssistant, entry: MockConfigEntry) -> MockConfigEntry:
    """The mock entry, set up."""
    assert await hass.config_entries.async_setup(entry.entry_id)
    await hass.async_block_till_done()
    assert entry.state is ConfigEntryState.LOADED
    return entry
