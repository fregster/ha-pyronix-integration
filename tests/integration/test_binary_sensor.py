# SPDX-FileCopyrightText: 2026 Paul Fryer
# SPDX-License-Identifier: Apache-2.0
from __future__ import annotations

import json
from typing import TYPE_CHECKING

from homeassistant.const import STATE_OFF, STATE_ON, STATE_UNAVAILABLE, STATE_UNKNOWN
from homeassistant.helpers import entity_registry as er
import pytest

from custom_components.pyronix_cloud.binary_sensor import device_class
from custom_components.pyronix_cloud.const import DOMAIN
from pyronix_cloud import Input, InputState
from pyronix_cloud.testing import dump
from tests.integration.conftest import PERSISTENT, until

if TYPE_CHECKING:
    from homeassistant.core import HomeAssistant
    from pytest_homeassistant_custom_component.common import MockConfigEntry  # type: ignore[import-untyped]  # the HA test plugin ships no py.typed

    from pyronix_cloud.testing.fake_cloud import FakeCloud


def entity_id(hass: HomeAssistant, number: int) -> str | None:
    return er.async_get(hass).async_get_entity_id(
        "binary_sensor", DOMAIN, f"{dump.PANEL_ID}_input_{number}"
    )


def state_of(hass: HomeAssistant, number: int) -> str | None:
    found = entity_id(hass, number)
    state = hass.states.get(found) if found is not None else None
    return state.state if state is not None else None


def input_push(number: int, name: str, value: int) -> str:
    """Return a pushed input update for any input number, name and state."""
    detail = dict(dump.input_detail(0), R=number, N=name, T="Intruder", V=value)
    return json.dumps({"type": "input", "isnew": 0, "Inputs": 1, "Detail": [detail]})


@pytest.mark.parametrize(
    ("number", "name", "kind"),
    [
        (0, "Front Door", "opening"),
        (1, "Hall DT", "motion"),
        (8, "Bedroom Shock", None),
        (9, "Garage Heat", "smoke"),
    ],
)
async def test_one_sensor_per_input(
    hass: HomeAssistant,
    loaded: MockConfigEntry,
    number: int,
    name: str,
    kind: str | None,
) -> None:
    found = entity_id(hass, number)
    assert found is not None
    state = hass.states.get(found)
    assert state is not None
    assert state.state == STATE_OFF
    assert state.name == f"Pyronix alarm {name}"
    assert state.attributes.get("device_class") == kind
    entries = er.async_entries_for_config_entry(er.async_get(hass), loaded.entry_id)
    assert len([e for e in entries if e.domain == "binary_sensor"]) == 10


@pytest.mark.parametrize("entry_options", [PERSISTENT])
@pytest.mark.usefixtures("loaded")
async def test_pushed_input_states(hass: HomeAssistant, cloud: FakeCloud) -> None:
    for text in dump.door_open_sequence(7):
        await cloud.push(text)
    await until(lambda: state_of(hass, 7) == STATE_ON)
    await cloud.push(input_push(7, "Landing DT", 2))  # a problem: unknown
    await until(lambda: state_of(hass, 7) == STATE_UNKNOWN)
    for text in dump.door_close_sequence(7):
        await cloud.push(text)
    await until(lambda: state_of(hass, 7) == STATE_OFF)


@pytest.mark.parametrize("entry_options", [PERSISTENT])
async def test_new_inputs_are_added(
    hass: HomeAssistant, cloud: FakeCloud, loaded: MockConfigEntry
) -> None:
    assert entity_id(hass, 10) is None
    await cloud.push(input_push(10, "Shed Window", 1))
    await until(lambda: state_of(hass, 10) == STATE_ON)
    found = entity_id(hass, 10)
    assert found is not None
    state = hass.states.get(found)
    assert state is not None
    assert state.attributes["device_class"] == "opening"
    # A change to a known input adds nothing.
    await cloud.push(input_push(10, "Shed Window", 0))
    await until(lambda: state_of(hass, 10) == STATE_OFF)
    entries = er.async_entries_for_config_entry(er.async_get(hass), loaded.entry_id)
    assert len([e for e in entries if e.domain == "binary_sensor"]) == 11


@pytest.mark.parametrize("entry_options", [PERSISTENT])
@pytest.mark.usefixtures("loaded")
async def test_unavailable_when_the_session_drops(
    hass: HomeAssistant, cloud: FakeCloud
) -> None:
    await cloud.drop()
    await until(lambda: state_of(hass, 0) == STATE_UNAVAILABLE)


@pytest.mark.parametrize(
    ("name", "kind", "expected"),
    [
        ("Hall PIR", "Intruder", "motion"),
        ("Bedroom Window", "Intruder", "opening"),
        ("Kitchen Smoke", "Fire", "smoke"),
        ("Panic", "PA", None),
    ],
)
def test_device_class(name: str, kind: str, expected: str | None) -> None:
    item = Input(
        number=0,
        name=name,
        location="",
        input_type=kind,
        areas=(0,),
        state=InputState.CLOSED,
        status_text="Closed",
        omittable=True,
        omitted=False,
    )
    assert device_class(item) == expected
