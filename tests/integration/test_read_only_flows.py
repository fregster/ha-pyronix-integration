# SPDX-FileCopyrightText: 2026 Paul Fryer
# SPDX-License-Identifier: Apache-2.0
"""No flow sends a state-changing command unless the user asked to arm or disarm."""

from __future__ import annotations

from typing import TYPE_CHECKING

from homeassistant.config_entries import SOURCE_USER, ConfigEntryState
from homeassistant.const import ATTR_ENTITY_ID, EVENT_HOMEASSISTANT_STOP
from homeassistant.data_entry_flow import FlowResultType
import pytest

from custom_components.pyronix_cloud.const import (
    CONF_APP_PASSWORD,
    CONF_APP_USER,
    CONF_MODE,
    CONF_SCAN_INTERVAL,
    CONF_USER_CODE,
    DOMAIN,
    MODE_ON_DEMAND,
    MODE_PERSISTENT,
)
from pyronix_cloud import commands
from pyronix_cloud.testing import dump
from pyronix_cloud.testing.command_audit import state_changes, state_commands
from pyronix_cloud.testing.fake_cloud import CloudBehaviour, FakeCloud
from tests.integration.conftest import ENTRY_DATA, PERSISTENT, until
from tests.integration.test_alarm_control_panel import entity_id
from tests.integration.test_config_flow import USER_INPUT
from tests.integration.test_coordinator import poll
from tests.integration.test_live import after_backoff

if TYPE_CHECKING:
    from collections.abc import Awaitable, Callable

    from homeassistant.core import HomeAssistant
    from pytest_homeassistant_custom_component.common import MockConfigEntry  # type: ignore[import-untyped]  # the HA test plugin ships no py.typed

type Flow = Callable[[HomeAssistant, FakeCloud, MockConfigEntry], Awaitable[None]]

CREDENTIALS = {
    CONF_APP_PASSWORD: ENTRY_DATA[CONF_APP_PASSWORD],
    CONF_USER_CODE: ENTRY_DATA[CONF_USER_CODE],
}
WRONG_PASSWORD = {**CREDENTIALS, CONF_APP_PASSWORD: "wrong-password"}


async def set_up(hass: HomeAssistant, entry: MockConfigEntry) -> None:
    assert await hass.config_entries.async_setup(entry.entry_id)
    await hass.async_block_till_done()
    assert entry.state is ConfigEntryState.LOADED


async def unload(hass: HomeAssistant, entry: MockConfigEntry) -> None:
    assert await hass.config_entries.async_unload(entry.entry_id)
    await hass.async_block_till_done()


async def on_demand_polls(
    hass: HomeAssistant, _cloud: FakeCloud, entry: MockConfigEntry
) -> None:
    await set_up(hass, entry)
    await poll(hass, entry.runtime_data)
    await poll(hass, entry.runtime_data)
    await unload(hass, entry)


async def persistent_reconnects(
    hass: HomeAssistant, cloud: FakeCloud, entry: MockConfigEntry
) -> None:
    hass.config_entries.async_update_entry(entry, options=PERSISTENT)
    await set_up(hass, entry)
    await cloud.heartbeat()
    for text in dump.door_open_sequence():
        await cloud.push(text)
    await cloud.drop()
    await until(lambda: not entry.runtime_data.last_update_success)
    await after_backoff(hass)
    assert cloud.sessions[-1].relaying
    await cloud.heartbeat()
    await unload(hass, entry)


async def home_assistant_stops(
    hass: HomeAssistant, _cloud: FakeCloud, entry: MockConfigEntry
) -> None:
    hass.config_entries.async_update_entry(entry, options=PERSISTENT)
    await set_up(hass, entry)
    hass.bus.async_fire(EVENT_HOMEASSISTANT_STOP)
    await hass.async_block_till_done()


async def user_flow(
    hass: HomeAssistant, cloud: FakeCloud, entry: MockConfigEntry
) -> None:
    await hass.config_entries.async_remove(entry.entry_id)
    flow = await hass.config_entries.flow.async_init(
        DOMAIN, context={"source": SOURCE_USER}
    )
    cloud.behaviour = CloudBehaviour(error_letter="n")
    offline = await hass.config_entries.flow.async_configure(
        flow["flow_id"], USER_INPUT
    )
    assert offline["errors"] == {"base": "panel_offline"}
    cloud.behaviour = CloudBehaviour()
    failed = await hass.config_entries.flow.async_configure(
        flow["flow_id"], {**USER_INPUT, **WRONG_PASSWORD}
    )
    assert failed["errors"] == {"base": "invalid_auth"}
    created = await hass.config_entries.flow.async_configure(
        flow["flow_id"], USER_INPUT
    )
    assert created["type"] is FlowResultType.CREATE_ENTRY
    await hass.async_block_till_done()  # the new entry's first refresh


async def reauth(
    hass: HomeAssistant, _cloud: FakeCloud, entry: MockConfigEntry
) -> None:
    await set_up(hass, entry)
    flow = await entry.start_reauth_flow(hass)
    configure = hass.config_entries.flow.async_configure
    failed = await configure(flow["flow_id"], WRONG_PASSWORD)
    assert failed["errors"] == {"base": "invalid_auth"}
    done = await configure(flow["flow_id"], CREDENTIALS)
    assert done["reason"] == "reauth_successful"
    await hass.async_block_till_done()  # the entry reloads


async def reconfigure(
    hass: HomeAssistant, _cloud: FakeCloud, entry: MockConfigEntry
) -> None:
    await set_up(hass, entry)
    flow = await entry.start_reconfigure_flow(hass)
    changes = {CONF_APP_USER: "Kitchen tablet", **CREDENTIALS}
    done = await hass.config_entries.flow.async_configure(flow["flow_id"], changes)
    assert done["reason"] == "reconfigure_successful"
    await hass.async_block_till_done()  # the entry reloads


async def options_change(
    hass: HomeAssistant, _cloud: FakeCloud, entry: MockConfigEntry
) -> None:
    await set_up(hass, entry)
    for mode in (MODE_PERSISTENT, MODE_ON_DEMAND):
        form = await hass.config_entries.options.async_init(entry.entry_id)
        await hass.config_entries.options.async_configure(
            form["flow_id"], {CONF_MODE: mode, CONF_SCAN_INTERVAL: 2.0}
        )
        await hass.async_block_till_done()  # the entry reloads
    await unload(hass, entry)


FLOWS: dict[str, Flow] = {
    "on-demand setup, polls and unload": on_demand_polls,
    "persistent setup, pushes and reconnect": persistent_reconnects,
    "Home Assistant stop": home_assistant_stops,
    "config flow validation": user_flow,
    "reauth": reauth,
    "reconfigure": reconfigure,
    "options change and reload": options_change,
}


@pytest.mark.parametrize("flow", FLOWS.values(), ids=FLOWS.keys())
async def test_flow_sends_only_read_only_commands(
    hass: HomeAssistant, cloud: FakeCloud, entry: MockConfigEntry, flow: Flow
) -> None:
    await flow(hass, cloud, entry)
    logged_in = [panel for panel in cloud.panels if panel.logged_in]
    assert logged_in  # not vacuous: real sessions reached data mode
    assert state_changes(cloud.panels) == []


# The fake panel uses level setting: the "alarm" entity's away level is area 0.
@pytest.mark.parametrize(
    ("service", "expected"),
    [
        ("alarm_arm_away", commands.set_areas([0])),
        ("alarm_disarm", commands.unset_areas([0])),
    ],
    ids=["arm away", "disarm"],
)
async def test_a_service_call_sends_exactly_its_command(
    hass: HomeAssistant,
    cloud: FakeCloud,
    loaded: MockConfigEntry,
    service: str,
    expected: bytes,
) -> None:
    del loaded  # requested so the entities exist
    if service == "alarm_disarm":
        await hass.services.async_call(
            "alarm_control_panel",
            "alarm_arm_away",
            {ATTR_ENTITY_ID: entity_id(hass, "alarm")},
            blocking=True,
        )
    before = len(state_commands(cloud.panels))
    await hass.services.async_call(
        "alarm_control_panel",
        service,
        {ATTR_ENTITY_ID: entity_id(hass, "alarm")},
        blocking=True,
    )
    assert state_commands(cloud.panels)[before:] == [expected]
