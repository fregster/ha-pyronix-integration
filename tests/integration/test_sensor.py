# SPDX-FileCopyrightText: 2026 Paul Fryer
# SPDX-License-Identifier: Apache-2.0
from __future__ import annotations

import json
from typing import TYPE_CHECKING
from unittest.mock import patch

from homeassistant.const import STATE_UNAVAILABLE, EntityCategory
from homeassistant.helpers import entity_registry as er
import pytest

from custom_components.pyronix_cloud.const import DOMAIN
from custom_components.pyronix_cloud.sensor import ConnectionStateSensor
from pyronix_cloud.testing import dump
from pyronix_cloud.testing.fake_cloud import CloudBehaviour
from tests.integration.conftest import PERSISTENT, until

if TYPE_CHECKING:
    from collections.abc import Iterator

    from homeassistant.core import HomeAssistant
    from pytest_homeassistant_custom_component.common import MockConfigEntry  # type: ignore[import-untyped]  # the HA test plugin ships no py.typed

    from pyronix_cloud.testing.fake_cloud import FakeCloud


def entity_id(hass: HomeAssistant, key: str) -> str:
    found = er.async_get(hass).async_get_entity_id(
        "sensor", DOMAIN, f"{dump.PANEL_ID}_{key}"
    )
    assert found is not None
    return found


def state_of(hass: HomeAssistant, key: str) -> str | None:
    state = hass.states.get(entity_id(hass, key))
    return state.state if state is not None else None


@pytest.fixture
def enabled() -> Iterator[None]:
    """Enable the connection state sensor, which is disabled by default."""
    with patch.object(
        ConnectionStateSensor, "_attr_entity_registry_enabled_default", new=True
    ):
        yield


@pytest.mark.usefixtures("loaded")
async def test_connection_state_is_disabled_by_default(hass: HomeAssistant) -> None:
    entry = er.async_get(hass).async_get(entity_id(hass, "connection_state"))
    assert entry is not None
    assert entry.disabled_by is er.RegistryEntryDisabler.INTEGRATION
    assert entry.entity_category is EntityCategory.DIAGNOSTIC
    assert hass.states.get(entity_id(hass, "connection_state")) is None


@pytest.mark.usefixtures("enabled")
async def test_connection_state_shows_errors(
    hass: HomeAssistant, cloud: FakeCloud, loaded: MockConfigEntry
) -> None:
    state = hass.states.get(entity_id(hass, "connection_state"))
    assert state is not None
    assert state.state == "idle"
    assert state.attributes["options"] == ["idle", "connecting", "connected", "error"]
    assert state.attributes["device_class"] == "enum"
    cloud.behaviour = CloudBehaviour(error_letter="n")
    await loaded.runtime_data.async_refresh()
    await hass.async_block_till_done()
    assert state_of(hass, "connection_state") == "error"
    assert state_of(hass, "faults") == STATE_UNAVAILABLE


@pytest.mark.parametrize("entry_options", [PERSISTENT])
@pytest.mark.usefixtures("enabled", "loaded")
async def test_connection_state_in_persistent_mode(hass: HomeAssistant) -> None:
    assert state_of(hass, "connection_state") == "connected"


@pytest.mark.parametrize("entry_options", [PERSISTENT])
@pytest.mark.usefixtures("loaded")
async def test_faults_count(hass: HomeAssistant, cloud: FakeCloud) -> None:
    faults = hass.states.get(entity_id(hass, "faults"))
    assert faults is not None
    assert faults.state == "0"
    assert set(faults.attributes) == {"friendly_name", "state_class"}
    entry = er.async_get(hass).async_get(entity_id(hass, "faults"))
    assert entry is not None
    assert entry.entity_category is EntityCategory.DIAGNOSTIC
    for text in dump.door_open_sequence():
        await cloud.push(text)
    await until(lambda: state_of(hass, "faults") == "1")
    panel_fault = {"I": "Mains Fail", "A": {"Q": 1, "R": [0]}}
    await cloud.push(
        json.dumps({"type": "fault", "Faults": 1, "Detail": [panel_fault]})
    )
    await until(lambda: state_of(hass, "faults") == "2")
