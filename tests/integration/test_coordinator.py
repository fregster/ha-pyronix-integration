# SPDX-FileCopyrightText: 2026 Paul Fryer
# SPDX-License-Identifier: Apache-2.0
from __future__ import annotations

import asyncio
from datetime import timedelta
from typing import TYPE_CHECKING

from homeassistant.config_entries import SOURCE_REAUTH
from homeassistant.exceptions import HomeAssistantError
from homeassistant.helpers.update_coordinator import UpdateFailed
from homeassistant.util import dt as dt_util
import pytest
from pytest_homeassistant_custom_component.common import async_fire_time_changed  # type: ignore[import-untyped]  # the HA test plugin ships no py.typed

from custom_components.pyronix_cloud.const import CONF_SCAN_INTERVAL, ConnectionState
from pyronix_cloud import AreaStatus, commands
from pyronix_cloud.testing.fake_cloud import CloudBehaviour, FakeCloud
from pyronix_cloud.testing.fake_panel import PanelBehaviour
from tests.integration.conftest import link_state, said_goodbye

if TYPE_CHECKING:
    from homeassistant.core import HomeAssistant
    from pytest_homeassistant_custom_component.common import MockConfigEntry

    from custom_components.pyronix_cloud.coordinator import PyronixCoordinator

ARMED = {AreaStatus.SETTING, AreaStatus.SET}


def reauth_started(hass: HomeAssistant) -> bool:
    return any(
        flow["context"]["source"] == SOURCE_REAUTH
        for flow in hass.config_entries.flow.async_progress()
    )


async def poll(hass: HomeAssistant, coordinator: PyronixCoordinator) -> None:
    """Let the poll timer fire; it runs only while something listens."""
    assert coordinator.update_interval is not None
    if not coordinator._listeners:
        coordinator.async_add_listener(lambda: None)
    later = dt_util.utcnow() + coordinator.update_interval + timedelta(seconds=1)
    async_fire_time_changed(hass, later)
    await hass.async_block_till_done(wait_background_tasks=True)


async def test_polls_with_short_sessions(
    hass: HomeAssistant, cloud: FakeCloud, loaded: MockConfigEntry
) -> None:
    coordinator = loaded.runtime_data
    assert coordinator.update_interval == timedelta(minutes=2)
    states: list[ConnectionState] = []
    remove = coordinator.async_add_listener(
        lambda: states.append(coordinator.connection_state)
    )
    await poll(hass, coordinator)
    await poll(hass, coordinator)
    remove()
    assert len(cloud.sessions) == 3
    assert all(said_goodbye(s) for s in cloud.sessions)
    assert ConnectionState.CONNECTING in states
    assert ConnectionState.CONNECTED in states
    assert coordinator.connection_state is ConnectionState.IDLE
    assert coordinator.last_update_success


@pytest.mark.parametrize("entry_options", [{CONF_SCAN_INTERVAL: 7}])
async def test_scan_interval_option(loaded: MockConfigEntry) -> None:
    assert loaded.runtime_data.update_interval == timedelta(minutes=7)


async def test_poll_failure_is_update_failed(
    hass: HomeAssistant, cloud: FakeCloud, loaded: MockConfigEntry
) -> None:
    coordinator = loaded.runtime_data
    cloud.behaviour = CloudBehaviour(error_letter="n")
    await poll(hass, coordinator)
    assert not coordinator.last_update_success
    error = coordinator.last_exception
    assert isinstance(error, UpdateFailed)
    assert error.translation_key == "update_failed"
    assert error.translation_placeholders == {"error": "PanelOfflineError"}
    assert coordinator.connection_state is ConnectionState.ERROR
    assert cloud.sessions[-1].closed_by_client
    cloud.behaviour = CloudBehaviour()
    await poll(hass, coordinator)
    assert coordinator.last_update_success
    assert link_state(coordinator) is ConnectionState.IDLE


async def test_poll_auth_failure_starts_reauth(
    hass: HomeAssistant, cloud: FakeCloud, loaded: MockConfigEntry
) -> None:
    cloud.behaviour = CloudBehaviour(error_letter="a")
    await poll(hass, loaded.runtime_data)
    assert reauth_started(hass)


async def test_arm_and_disarm(
    hass: HomeAssistant, cloud: FakeCloud, loaded: MockConfigEntry
) -> None:
    coordinator = loaded.runtime_data
    await coordinator.async_arm([1])
    assert coordinator.data.areas[1].status in ARMED
    arm_session = cloud.sessions[-1]
    assert said_goodbye(arm_session)
    assert arm_session.panel is not None
    assert arm_session.panel.received_commands == [commands.set_areas((1,), force=True)]
    await coordinator.async_disarm([1])
    assert coordinator.data.areas[1].status is AreaStatus.UNSET
    assert said_goodbye(cloud.sessions[-1])
    assert coordinator.connection_state is ConnectionState.IDLE
    await hass.async_block_till_done()


async def test_sessions_never_overlap(
    hass: HomeAssistant, cloud: FakeCloud, loaded: MockConfigEntry
) -> None:
    coordinator = loaded.runtime_data
    await asyncio.gather(
        coordinator.async_refresh(),
        coordinator.async_arm([0]),
        coordinator.async_disarm([0]),
    )
    assert coordinator.last_update_success
    assert all(said_goodbye(s) for s in cloud.sessions)
    assert coordinator.data.areas[0].status is AreaStatus.UNSET
    await hass.async_block_till_done()


@pytest.mark.parametrize(
    ("letter", "key"),
    [("b", "panel_busy"), ("n", "command_failed"), ("a", "auth_failed")],
)
async def test_command_session_errors(
    hass: HomeAssistant,
    cloud: FakeCloud,
    loaded: MockConfigEntry,
    letter: str,
    key: str,
) -> None:
    cloud.behaviour = CloudBehaviour(error_letter=letter)
    with pytest.raises(HomeAssistantError) as raised:
        await loaded.runtime_data.async_arm([0])
    assert raised.value.translation_domain == "pyronix_cloud"
    assert raised.value.translation_key == key
    assert reauth_started(hass) is (letter == "a")
    assert loaded.runtime_data.connection_state is ConnectionState.ERROR


async def test_arm_not_permitted(
    hass: HomeAssistant, cloud: FakeCloud, loaded: MockConfigEntry
) -> None:
    with pytest.raises(HomeAssistantError) as raised:
        await loaded.runtime_data.async_arm([3])
    assert raised.value.translation_key == "not_permitted"
    assert said_goodbye(cloud.sessions[-1])
    assert loaded.runtime_data.connection_state is ConnectionState.IDLE
    await hass.async_block_till_done()


@pytest.mark.parametrize("panel_behaviour", [PanelBehaviour(confirm_commands=False)])
async def test_arm_not_confirmed(
    hass: HomeAssistant, cloud: FakeCloud, loaded: MockConfigEntry
) -> None:
    with pytest.raises(HomeAssistantError) as raised:
        await loaded.runtime_data.async_arm([0])
    assert raised.value.translation_key == "command_not_confirmed"
    assert said_goodbye(cloud.sessions[-1])
    await hass.async_block_till_done()
