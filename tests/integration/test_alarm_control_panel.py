# SPDX-FileCopyrightText: 2026 Paul Fryer
# SPDX-License-Identifier: Apache-2.0
from __future__ import annotations

import json
from typing import TYPE_CHECKING, Any

from homeassistant.components.alarm_control_panel.const import (
    AlarmControlPanelEntityFeature,
    AlarmControlPanelState,
)
from homeassistant.const import ATTR_ENTITY_ID, STATE_UNAVAILABLE
from homeassistant.exceptions import HomeAssistantError, ServiceValidationError
from homeassistant.helpers import device_registry as dr
from homeassistant.helpers import entity_registry as er
import pytest

from custom_components.pyronix_cloud.alarm_control_panel import alarm_state
from custom_components.pyronix_cloud.const import DOMAIN
from pyronix_cloud.testing import dump
from pyronix_cloud.testing.fake_cloud import CloudBehaviour, FakeCloud
from pyronix_cloud.testing.fake_panel import PanelBehaviour
from tests.integration.conftest import PERSISTENT, until

if TYPE_CHECKING:
    from homeassistant.core import HomeAssistant
    from pytest_homeassistant_custom_component.common import MockConfigEntry  # type: ignore[import-untyped]  # the HA test plugin ships no py.typed

Feature = AlarmControlPanelEntityFeature
State = AlarmControlPanelState
ALL_LEVELS = Feature.ARM_AWAY | Feature.ARM_NIGHT | Feature.ARM_CUSTOM_BYPASS


def documents(
    set_type: str = "Level",
    set_areas: tuple[int, ...] = (0, 1, 2),
    unset_areas: tuple[int, ...] = (0, 1, 2),
) -> list[str]:
    """Return the fake's login dump with another set type or user rights."""
    result: list[str] = []
    for text in dump.login_dump():
        doc: dict[str, Any] = json.loads(text)
        if doc["type"] == "area":
            doc["SetType"] = set_type
        if doc["type"] == "user":
            doc["SetAreas"] = {"Q": len(set_areas), "R": list(set_areas)}
            doc["UnsetAreas"] = {"Q": len(unset_areas), "R": list(unset_areas)}
        result.append(json.dumps(doc))
    return result


def entity_id(hass: HomeAssistant, key: str) -> str:
    """Return the entity ID of the alarm entity with unique ID suffix ``key``."""
    found = er.async_get(hass).async_get_entity_id(
        "alarm_control_panel", DOMAIN, f"{dump.PANEL_ID}_{key}"
    )
    assert found is not None
    return found


def state_of(hass: HomeAssistant, key: str) -> str:
    state = hass.states.get(entity_id(hass, key))
    assert state is not None
    return state.state


def features_of(hass: HomeAssistant, key: str) -> int:
    state = hass.states.get(entity_id(hass, key))
    assert state is not None
    return int(state.attributes["supported_features"])


async def call(hass: HomeAssistant, service: str, key: str = "alarm") -> None:
    await hass.services.async_call(
        "alarm_control_panel",
        service,
        {ATTR_ENTITY_ID: entity_id(hass, key)},
        blocking=True,
    )


def last_commands(cloud: FakeCloud) -> list[bytes]:
    panel = cloud.sessions[-1].panel
    assert panel is not None
    return panel.received_commands


async def test_level_entity(hass: HomeAssistant, loaded: MockConfigEntry) -> None:
    state = hass.states.get(entity_id(hass, "alarm"))
    assert state is not None
    assert state.state == State.DISARMED
    assert state.name == "Pyronix alarm"
    assert state.attributes["supported_features"] == ALL_LEVELS
    assert state.attributes["code_arm_required"] is False
    assert state.attributes["code_format"] is None
    assert set(state.attributes) == {
        "code_arm_required",
        "code_format",
        "changed_by",
        "friendly_name",
        "supported_features",
    }
    registry = dr.async_get(hass)
    [device] = dr.async_entries_for_config_entry(registry, loaded.entry_id)
    assert device.identifiers == {(DOMAIN, dump.PANEL_ID)}
    assert device.manufacturer == "Pyronix"
    assert device.model == "Euro 46 / Enforcer"
    assert device.name == loaded.title
    assert device.sw_version


@pytest.mark.parametrize(
    "case",
    [
        ("alarm_arm_away", 0, State.ARMED_AWAY),
        ("alarm_arm_night", 1, State.ARMED_NIGHT),
        ("alarm_arm_custom_bypass", 2, State.ARMED_CUSTOM_BYPASS),
    ],
)
async def test_arm_and_disarm_levels(
    hass: HomeAssistant,
    cloud: FakeCloud,
    loaded: MockConfigEntry,
    case: tuple[str, int, State],
) -> None:
    service, area, armed = case
    await call(hass, service)
    assert last_commands(cloud) == [bytes([0x2D, 0x81, area])]
    assert state_of(hass, "alarm") in {State.ARMING, armed}
    await loaded.runtime_data.async_refresh()
    await hass.async_block_till_done()
    assert state_of(hass, "alarm") == armed
    await call(hass, "alarm_disarm")
    assert last_commands(cloud) == [bytes([0x2F, 0x01, area])]
    assert state_of(hass, "alarm") == State.DISARMED


@pytest.mark.usefixtures("loaded")
async def test_disarm_when_disarmed_sends_nothing(
    hass: HomeAssistant, cloud: FakeCloud
) -> None:
    sessions = len(cloud.sessions)
    await call(hass, "alarm_disarm")
    assert len(cloud.sessions) == sessions


@pytest.mark.parametrize("panel_behaviour", [PanelBehaviour(confirm_commands=False)])
@pytest.mark.usefixtures("loaded")
async def test_arm_not_confirmed(hass: HomeAssistant) -> None:
    with pytest.raises(HomeAssistantError) as raised:
        await call(hass, "alarm_arm_away")
    assert raised.value.translation_key == "command_not_confirmed"


@pytest.mark.parametrize("entry_options", [PERSISTENT])
@pytest.mark.parametrize(
    "panel_behaviour",
    [PanelBehaviour(documents=documents(set_areas=(0,), unset_areas=(0,)))],
)
@pytest.mark.usefixtures("loaded")
async def test_features_follow_user_rights(
    hass: HomeAssistant, cloud: FakeCloud
) -> None:
    assert features_of(hass, "alarm") == Feature.ARM_AWAY
    entity = hass.data["alarm_control_panel"].get_entity(entity_id(hass, "alarm"))
    assert entity is not None
    with pytest.raises(ServiceValidationError) as raised:
        await entity.async_alarm_arm_night()
    assert raised.value.translation_key == "mode_not_supported"
    await cloud.push(dump.area_update(1, status=1, text="Set"))
    await until(lambda: state_of(hass, "alarm") == State.ARMED_NIGHT)
    with pytest.raises(HomeAssistantError) as error:
        await call(hass, "alarm_disarm")
    assert error.value.translation_key == "not_permitted"


@pytest.mark.parametrize("entry_options", [PERSISTENT])
@pytest.mark.usefixtures("loaded")
async def test_level_c_unused_drops_its_feature(
    hass: HomeAssistant, cloud: FakeCloud
) -> None:
    await cloud.push(dump.area_update(2, status=4, text="Cannot Set"))
    await until(
        lambda: features_of(hass, "alarm") == Feature.ARM_AWAY | Feature.ARM_NIGHT
    )


@pytest.mark.parametrize("entry_options", [PERSISTENT])
@pytest.mark.parametrize(
    ("status", "expected"),
    [(2, State.TRIGGERED), (3, State.ARMING), (1, State.ARMED_AWAY)],
)
@pytest.mark.usefixtures("loaded")
async def test_state_from_area_status(
    hass: HomeAssistant,
    cloud: FakeCloud,
    status: int,
    expected: State,
) -> None:
    # The status text is deliberately misleading: only the status code counts.
    await cloud.push(dump.area_update(0, status=status, text="Unset"))
    await until(lambda: state_of(hass, "alarm") == expected)


@pytest.mark.parametrize("entry_options", [PERSISTENT])
@pytest.mark.usefixtures("loaded")
async def test_alarm_in_any_level_wins(hass: HomeAssistant, cloud: FakeCloud) -> None:
    await cloud.push(dump.area_update(0, status=1, text="Set"))
    await cloud.push(dump.area_update(2, status=2, text="Alarm"))
    await until(lambda: state_of(hass, "alarm") == State.TRIGGERED)


@pytest.mark.parametrize(
    "panel_behaviour",
    [PanelBehaviour(documents=documents(set_type="Area", set_areas=(0, 1)))],
)
async def test_area_panel_has_one_entity_per_area(
    hass: HomeAssistant, cloud: FakeCloud, loaded: MockConfigEntry
) -> None:
    registry = er.async_get(hass)
    assert (
        registry.async_get_entity_id(
            "alarm_control_panel", DOMAIN, f"{dump.PANEL_ID}_alarm"
        )
        is None
    )
    for number in (3, 4, 5):  # unused areas get no entity
        unique_id = f"{dump.PANEL_ID}_area_{number}"
        assert (
            registry.async_get_entity_id("alarm_control_panel", DOMAIN, unique_id)
            is None
        )
    garage = hass.states.get(entity_id(hass, "area_2"))
    assert garage is not None
    assert garage.name == "Pyronix alarm Garage"
    assert garage.state == State.DISARMED
    assert garage.attributes["supported_features"] == 0
    assert features_of(hass, "area_0") == Feature.ARM_AWAY
    await call(hass, "alarm_arm_away", "area_1")
    assert last_commands(cloud) == [bytes([0x2D, 0x81, 1])]
    await loaded.runtime_data.async_refresh()
    await hass.async_block_till_done()
    assert state_of(hass, "area_1") == State.ARMED_AWAY
    await call(hass, "alarm_disarm", "area_1")
    assert last_commands(cloud) == [bytes([0x2F, 0x01, 1])]
    assert state_of(hass, "area_1") == State.DISARMED


async def test_unavailable_when_the_panel_cannot_be_reached(
    hass: HomeAssistant, cloud: FakeCloud, loaded: MockConfigEntry
) -> None:
    cloud.behaviour = CloudBehaviour(error_letter="n")
    await loaded.runtime_data.async_refresh()
    await hass.async_block_till_done()
    assert state_of(hass, "alarm") == STATE_UNAVAILABLE
    cloud.behaviour = CloudBehaviour()
    await loaded.runtime_data.async_refresh()
    await hass.async_block_till_done()
    assert state_of(hass, "alarm") == State.DISARMED


def test_no_areas_have_no_state() -> None:
    assert alarm_state([], {}) is None
