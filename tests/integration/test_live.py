# SPDX-FileCopyrightText: 2026 Paul Fryer
# SPDX-License-Identifier: Apache-2.0
from __future__ import annotations

from datetime import timedelta
from typing import TYPE_CHECKING, Any

from homeassistant.config_entries import SOURCE_REAUTH, ConfigEntryState
from homeassistant.exceptions import HomeAssistantError
from homeassistant.util import dt as dt_util
import pytest
from pytest_homeassistant_custom_component.common import async_fire_time_changed  # type: ignore[import-untyped]  # the HA test plugin ships no py.typed

from custom_components.pyronix_cloud.const import ConnectionState
from custom_components.pyronix_cloud.live import new_backoff
from pyronix_cloud import AreaStatus, InputState
from pyronix_cloud.testing import dump
from pyronix_cloud.testing.fake_cloud import CloudBehaviour, FakeCloud
from tests.integration.conftest import (
    BACKOFF_SECONDS,
    PERSISTENT,
    Gate,
    link_state,
    said_goodbye,
    until,
)

if TYPE_CHECKING:
    from homeassistant.core import HomeAssistant
    from pytest_homeassistant_custom_component.common import MockConfigEntry


@pytest.fixture
def entry_options() -> dict[str, Any]:
    """Every entry in this module uses the persistent mode."""
    return PERSISTENT


def reauth_started(hass: HomeAssistant) -> bool:
    return any(
        flow["context"]["source"] == SOURCE_REAUTH
        for flow in hass.config_entries.flow.async_progress()
    )


async def after_backoff(hass: HomeAssistant) -> None:
    """Let the reconnect timer fire and the attempt run."""
    later = dt_util.utcnow() + timedelta(seconds=BACKOFF_SECONDS + 1)
    async_fire_time_changed(hass, later)
    await hass.async_block_till_done(wait_background_tasks=True)


async def dropped(cloud: FakeCloud, loaded: MockConfigEntry) -> None:
    """Drop the live session from the server and wait for the client to see it."""
    coordinator = loaded.runtime_data
    await cloud.drop()
    await until(lambda: not coordinator.last_update_success)


async def test_connects_and_stays_connected(
    cloud: FakeCloud, loaded: MockConfigEntry
) -> None:
    coordinator = loaded.runtime_data
    assert coordinator.persistent
    assert coordinator.update_interval is None
    assert coordinator.connection_state is ConnectionState.CONNECTED
    assert coordinator.client.connected
    assert len(cloud.sessions) == 1
    assert cloud.sessions[0].relaying
    await coordinator.async_refresh()  # a manual refresh reuses the session
    assert len(cloud.sessions) == 1
    assert coordinator.last_update_success


async def test_pushes_update_the_coordinator(
    cloud: FakeCloud, loaded: MockConfigEntry
) -> None:
    coordinator = loaded.runtime_data
    seen: list[int] = []
    coordinator.async_add_listener(lambda: seen.append(1))
    for text in dump.door_open_sequence():
        await cloud.push(text)
    await until(lambda: coordinator.data.areas[0].status is AreaStatus.CAN_OVERRIDE)
    assert coordinator.data.inputs[7].state is InputState.OPEN
    assert seen


async def test_reconnects_after_a_drop(
    hass: HomeAssistant, cloud: FakeCloud, loaded: MockConfigEntry
) -> None:
    coordinator = loaded.runtime_data
    await dropped(cloud, loaded)
    assert coordinator.connection_state is ConnectionState.ERROR
    assert coordinator.last_exception is not None
    assert len(cloud.sessions) == 1
    cloud.behaviour = CloudBehaviour(error_letter="n")
    await after_backoff(hass)
    assert len(cloud.sessions) == 2
    assert cloud.sessions[1].closed_by_client
    assert coordinator.connection_state is ConnectionState.ERROR
    cloud.behaviour = CloudBehaviour()
    await after_backoff(hass)
    assert len(cloud.sessions) == 3
    assert cloud.sessions[2].relaying
    assert link_state(coordinator) is ConnectionState.CONNECTED
    assert coordinator.last_update_success


async def test_auth_failure_on_reconnect_starts_reauth(
    hass: HomeAssistant, cloud: FakeCloud, loaded: MockConfigEntry
) -> None:
    await dropped(cloud, loaded)
    cloud.behaviour = CloudBehaviour(error_letter="a")
    await after_backoff(hass)
    assert reauth_started(hass)
    await after_backoff(hass)
    assert len(cloud.sessions) == 2  # no retry after the panel said no


async def test_panel_goodbye_schedules_a_reconnect(
    hass: HomeAssistant, cloud: FakeCloud, loaded: MockConfigEntry
) -> None:
    coordinator = loaded.runtime_data
    await coordinator.client.close()
    live = coordinator._live
    assert live is not None
    live._on_disconnect(None)
    assert not coordinator.last_update_success
    await after_backoff(hass)
    assert said_goodbye(cloud.sessions[0])
    assert cloud.sessions[1].relaying


async def test_unload_cancels_a_pending_reconnect(
    hass: HomeAssistant, cloud: FakeCloud, loaded: MockConfigEntry
) -> None:
    await dropped(cloud, loaded)
    assert await hass.config_entries.async_unload(loaded.entry_id)
    await after_backoff(hass)
    assert len(cloud.sessions) == 1
    assert loaded.state is ConfigEntryState.NOT_LOADED


async def test_unload_cancels_a_running_reconnect(
    hass: HomeAssistant, cloud: FakeCloud, loaded: MockConfigEntry, gate: Gate
) -> None:
    coordinator = loaded.runtime_data
    await dropped(cloud, loaded)
    gate.hang = True
    async_fire_time_changed(
        hass, dt_util.utcnow() + timedelta(seconds=BACKOFF_SECONDS + 1)
    )
    await until(lambda: coordinator.connection_state is ConnectionState.CONNECTING)
    assert await hass.config_entries.async_unload(loaded.entry_id)
    assert not coordinator.client.connected
    assert len(cloud.sessions) == 1


async def test_arm_and_disarm_use_the_live_session(
    cloud: FakeCloud, loaded: MockConfigEntry
) -> None:
    coordinator = loaded.runtime_data
    await coordinator.async_arm([1])
    assert coordinator.data.areas[1].status in {AreaStatus.SETTING, AreaStatus.SET}
    await coordinator.async_disarm([1])
    assert coordinator.data.areas[1].status is AreaStatus.UNSET
    assert len(cloud.sessions) == 1
    assert cloud.sessions[0].relaying


async def test_arm_while_disconnected(
    cloud: FakeCloud, loaded: MockConfigEntry
) -> None:
    await dropped(cloud, loaded)
    with pytest.raises(HomeAssistantError) as raised:
        await loaded.runtime_data.async_arm([0])
    assert raised.value.translation_key == "not_connected"


async def test_arm_not_permitted(loaded: MockConfigEntry) -> None:
    with pytest.raises(HomeAssistantError) as raised:
        await loaded.runtime_data.async_disarm([4])
    assert raised.value.translation_key == "not_permitted"
    assert loaded.runtime_data.client.connected


def test_default_backoff() -> None:
    # The fixtures patch the module attribute; this name is the original.
    assert 5.0 <= new_backoff().next_delay() <= 5.5
