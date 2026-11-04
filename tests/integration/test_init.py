# SPDX-FileCopyrightText: 2026 Paul Fryer
# SPDX-License-Identifier: Apache-2.0
from __future__ import annotations

from typing import TYPE_CHECKING
from unittest.mock import patch

from homeassistant.config_entries import SOURCE_REAUTH, ConfigEntryState
from homeassistant.const import EVENT_HOMEASSISTANT_STOP
import pytest

from custom_components.pyronix_cloud import PLATFORMS
from custom_components.pyronix_cloud.client_factory import (
    connector_for,
    session_timeouts,
)
from custom_components.pyronix_cloud.const import (
    CONF_MODE,
    CONF_SCAN_INTERVAL,
    ConnectionState,
)
from custom_components.pyronix_cloud.coordinator import PyronixCoordinator
from pyronix_cloud import Timeouts
from pyronix_cloud.testing.fake_cloud import CloudBehaviour, FakeCloud
from tests.integration.conftest import (
    PERSISTENT,
    entry_state,
    link_state,
    said_goodbye,
)

if TYPE_CHECKING:
    from homeassistant.core import HomeAssistant
    from pytest_homeassistant_custom_component.common import MockConfigEntry  # type: ignore[import-untyped]  # the HA test plugin ships no py.typed


async def test_setup_and_unload(
    hass: HomeAssistant, cloud: FakeCloud, entry: MockConfigEntry
) -> None:
    assert await hass.config_entries.async_setup(entry.entry_id)
    await hass.async_block_till_done()
    assert entry.state is ConfigEntryState.LOADED
    coordinator = entry.runtime_data
    assert isinstance(coordinator, PyronixCoordinator)
    assert coordinator.data.dump_complete
    assert not coordinator.persistent
    assert coordinator.connection_state is ConnectionState.IDLE
    assert sorted(PLATFORMS) == PLATFORMS
    assert await hass.config_entries.async_unload(entry.entry_id)
    assert entry_state(entry) is ConfigEntryState.NOT_LOADED
    assert cloud.sessions
    assert all(said_goodbye(s) for s in cloud.sessions)


@pytest.mark.parametrize("entry_options", [PERSISTENT])
async def test_unload_closes_the_live_session(
    hass: HomeAssistant, cloud: FakeCloud, loaded: MockConfigEntry
) -> None:
    session = cloud.sessions[0]
    coordinator = loaded.runtime_data
    assert session.relaying
    assert coordinator.connection_state is ConnectionState.CONNECTED
    assert await hass.config_entries.async_unload(loaded.entry_id)
    assert said_goodbye(session)
    assert link_state(coordinator) is ConnectionState.IDLE


@pytest.mark.parametrize("entry_options", [PERSISTENT])
async def test_home_assistant_stop_closes_the_live_session(
    hass: HomeAssistant, cloud: FakeCloud, loaded: MockConfigEntry
) -> None:
    hass.bus.async_fire(EVENT_HOMEASSISTANT_STOP)
    await hass.async_block_till_done()
    assert said_goodbye(cloud.sessions[0])
    assert not loaded.runtime_data.client.connected


@pytest.mark.parametrize("entry_options", [PERSISTENT])
async def test_options_change_reloads_and_closes(
    hass: HomeAssistant, cloud: FakeCloud, loaded: MockConfigEntry
) -> None:
    first = cloud.sessions[0]
    form = await hass.config_entries.options.async_init(loaded.entry_id)
    await hass.config_entries.options.async_configure(
        form["flow_id"], {CONF_MODE: "on_demand", CONF_SCAN_INTERVAL: 10}
    )
    await hass.async_block_till_done()
    assert said_goodbye(first)
    assert loaded.state is ConfigEntryState.LOADED
    assert not loaded.runtime_data.persistent
    assert all(said_goodbye(s) for s in cloud.sessions)


@pytest.mark.parametrize(
    ("cloud_behaviour", "state"),
    [
        (CloudBehaviour(error_letter="a"), ConfigEntryState.SETUP_ERROR),
        (CloudBehaviour(error_letter="n"), ConfigEntryState.SETUP_RETRY),
    ],
)
@pytest.mark.parametrize("entry_options", [{}, PERSISTENT])
async def test_setup_failures(
    hass: HomeAssistant,
    cloud: FakeCloud,
    entry: MockConfigEntry,
    state: ConfigEntryState,
) -> None:
    assert not await hass.config_entries.async_setup(entry.entry_id)
    await hass.async_block_till_done()
    assert entry.state is state
    reauth = [
        flow
        for flow in hass.config_entries.flow.async_progress()
        if flow["context"]["source"] == SOURCE_REAUTH
    ]
    assert bool(reauth) is (state is ConfigEntryState.SETUP_ERROR)
    assert all(s.closed_by_client for s in cloud.sessions)
    await hass.config_entries.async_unload(entry.entry_id)


async def test_failed_platform_unload_keeps_the_entry(
    hass: HomeAssistant, loaded: MockConfigEntry
) -> None:
    with patch.object(
        hass.config_entries, "async_unload_platforms", return_value=False
    ):
        assert not await hass.config_entries.async_unload(loaded.entry_id)
    assert loaded.state is ConfigEntryState.FAILED_UNLOAD


async def test_real_connector_and_timeouts(hass: HomeAssistant) -> None:
    # The fixtures patch the module attributes; these names are the originals,
    # which build a connector without opening a socket.
    assert callable(connector_for(hass))
    assert session_timeouts() == Timeouts()
