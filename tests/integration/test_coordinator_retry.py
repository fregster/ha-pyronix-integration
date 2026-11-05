# SPDX-FileCopyrightText: 2026 Paul Fryer
# SPDX-License-Identifier: Apache-2.0
"""The coordinator's sessions when the cloud refuses with `n` after a goodbye."""

from __future__ import annotations

import asyncio
from datetime import timedelta
from typing import TYPE_CHECKING

from homeassistant.exceptions import HomeAssistantError
from homeassistant.util import dt as dt_util
import pytest
from pytest_homeassistant_custom_component.common import async_fire_time_changed  # type: ignore[import-untyped]  # the HA test plugin ships no py.typed

from custom_components.pyronix_cloud.client_factory import not_polling_retry
from custom_components.pyronix_cloud.const import ConnectionState
from pyronix_cloud import AreaStatus, RetryPolicy, commands
from pyronix_cloud.testing.fake_cloud import CloudBehaviour, FakeCloud
from tests.integration.conftest import (
    BACKOFF_SECONDS,
    PERSISTENT,
    link_state,
    said_goodbye,
    until,
)

if TYPE_CHECKING:
    from homeassistant.core import HomeAssistant
    from pytest_homeassistant_custom_component.common import MockConfigEntry

    from custom_components.pyronix_cloud.coordinator import PyronixCoordinator
    from pyronix_cloud.testing.cloud_session import CloudSession

QUICK = RetryPolicy(retries=3, pause=0.01)
SLOW = RetryPolicy(retries=3, pause=60)
ARMED = {AreaStatus.SETTING, AreaStatus.SET}


def refused(session: CloudSession) -> bool:
    """Whether the cloud refused the session before it reached the panel."""
    return session.panel is None and session.closed_by_client


def commands_sent(cloud: FakeCloud) -> list[bytes]:
    return [
        command
        for session in cloud.sessions
        if session.panel is not None
        for command in session.panel.received_commands
    ]


async def poll(hass: HomeAssistant, coordinator: PyronixCoordinator) -> None:
    """Let the poll timer fire and the poll finish."""
    assert coordinator.update_interval is not None
    if not coordinator._listeners:
        coordinator.async_add_listener(lambda: None)
    later = dt_util.utcnow() + coordinator.update_interval + timedelta(seconds=1)
    async_fire_time_changed(hass, later)
    await hass.async_block_till_done(wait_background_tasks=True)


def test_default_policy() -> None:
    # The fixtures patch the module attribute; this name is the original.
    assert not_polling_retry() == RetryPolicy()


@pytest.mark.parametrize("retry_policy", [QUICK])
@pytest.mark.parametrize("cloud_behaviour", [CloudBehaviour(not_polling_after_end=2)])
async def test_arm_right_after_a_poll_waits_for_the_cloud(
    hass: HomeAssistant, cloud: FakeCloud, loaded: MockConfigEntry
) -> None:
    coordinator = loaded.runtime_data
    await coordinator.async_arm([0])  # the setup refresh has just said goodbye
    assert coordinator.data.areas[0].status in ARMED
    setup, first, second, armed = cloud.sessions
    assert said_goodbye(setup)
    assert refused(first)
    assert refused(second)
    assert said_goodbye(armed)
    assert commands_sent(cloud) == [commands.set_areas((0,), force=True)]
    assert coordinator.connection_state is ConnectionState.IDLE
    await hass.async_block_till_done()


@pytest.mark.parametrize("retry_policy", [QUICK])
@pytest.mark.parametrize("cloud_behaviour", [CloudBehaviour(not_polling_after_end=2)])
async def test_poll_right_after_a_command_waits_for_the_cloud(
    hass: HomeAssistant, cloud: FakeCloud, loaded: MockConfigEntry
) -> None:
    coordinator = loaded.runtime_data
    await coordinator.async_disarm([0])  # already unset: a session, no command
    await poll(hass, coordinator)
    assert coordinator.last_update_success
    assert said_goodbye(cloud.sessions[-1])
    assert sum(refused(s) for s in cloud.sessions) == 4
    assert link_state(coordinator) is ConnectionState.IDLE


@pytest.mark.parametrize("retry_policy", [QUICK])
@pytest.mark.parametrize("cloud_behaviour", [CloudBehaviour(not_polling_after_end=9)])
async def test_command_gives_up_without_sending(
    hass: HomeAssistant, cloud: FakeCloud, loaded: MockConfigEntry
) -> None:
    coordinator = loaded.runtime_data
    with pytest.raises(HomeAssistantError) as raised:
        await coordinator.async_arm([0])
    assert raised.value.translation_key == "command_failed"
    assert raised.value.translation_placeholders == {"error": "PanelOfflineError"}
    assert sum(refused(s) for s in cloud.sessions) == 4
    assert commands_sent(cloud) == []
    assert coordinator.connection_state is ConnectionState.ERROR
    await hass.async_block_till_done()


@pytest.mark.parametrize("retry_policy", [SLOW])
@pytest.mark.parametrize("cloud_behaviour", [CloudBehaviour(not_polling_after_end=9)])
async def test_unload_ends_a_pending_retry(
    hass: HomeAssistant, cloud: FakeCloud, loaded: MockConfigEntry
) -> None:
    coordinator = loaded.runtime_data
    task = hass.async_create_task(coordinator.async_arm([0]))
    await until(lambda: len(cloud.sessions) == 2 and cloud.sessions[1].ended)
    assert await hass.config_entries.async_unload(loaded.entry_id)
    with pytest.raises(HomeAssistantError):
        await asyncio.wait_for(task, 1)
    assert len(cloud.sessions) == 2
    assert commands_sent(cloud) == []
    await hass.async_block_till_done()


@pytest.mark.parametrize("entry_options", [PERSISTENT])
@pytest.mark.parametrize("retry_policy", [QUICK])
@pytest.mark.parametrize("cloud_behaviour", [CloudBehaviour(not_polling_after_end=2)])
async def test_live_reconnect_waits_for_the_cloud(
    hass: HomeAssistant, cloud: FakeCloud, loaded: MockConfigEntry
) -> None:
    coordinator = loaded.runtime_data
    await cloud.drop()
    await until(lambda: not coordinator.last_update_success)
    later = dt_util.utcnow() + timedelta(seconds=BACKOFF_SECONDS + 1)
    async_fire_time_changed(hass, later)
    await hass.async_block_till_done(wait_background_tasks=True)
    _first, *refusals, live = cloud.sessions
    assert len(refusals) == 2
    assert all(refused(s) for s in refusals)
    assert live.relaying
    assert link_state(coordinator) is ConnectionState.CONNECTED
