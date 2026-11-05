# SPDX-FileCopyrightText: 2026 Paul Fryer
# SPDX-License-Identifier: Apache-2.0
"""The alarm entities' access check, plain arming and the force_arm action."""

from __future__ import annotations

from typing import TYPE_CHECKING, Any

from homeassistant.auth.const import GROUP_ID_USER
from homeassistant.const import ATTR_CODE, ATTR_ENTITY_ID
from homeassistant.core import Context
from homeassistant.exceptions import HomeAssistantError, ServiceValidationError
import pytest

from custom_components.pyronix_cloud.const import (
    CONF_ALLOW_AUTOMATIONS,
    CONF_ALWAYS_FORCE_ARM,
    CONF_ARM_ACCESS,
    CONF_PIN_HASH,
    CONF_USERS,
    DOMAIN,
    SERVICE_FORCE_ARM,
)
from custom_components.pyronix_cloud.pin_hash import hash_pin
from pyronix_cloud.testing import dump
from pyronix_cloud.testing.fake_panel import PanelBehaviour
from tests.integration.conftest import PERSISTENT, until
from tests.integration.test_alarm_control_panel import (
    documents,
    entity_id,
    last_commands,
)

if TYPE_CHECKING:
    from homeassistant.core import HomeAssistant
    from pytest_homeassistant_custom_component.common import MockConfigEntry  # type: ignore[import-untyped]  # the HA test plugin ships no py.typed

    from pyronix_cloud.testing.fake_cloud import FakeCloud

PIN = {CONF_ARM_ACCESS: "pin", CONF_PIN_HASH: hash_pin("4711")}
AREA_PANEL = PanelBehaviour(documents=documents(set_type="Area"))
PLAIN_A = bytes([0x2D, 0x01, 0])
FORCED_A = bytes([0x2D, 0x81, 0])


async def call(
    hass: HomeAssistant,
    service: str,
    key: str = "alarm",
    *,
    context: Context | None = None,
    data: dict[str, Any] | None = None,
) -> None:
    """Call an alarm action, or this integration's force_arm action."""
    domain = DOMAIN if service == SERVICE_FORCE_ARM else "alarm_control_panel"
    await hass.services.async_call(
        domain,
        service,
        {ATTR_ENTITY_ID: entity_id(hass, key), **(data or {})},
        blocking=True,
        context=context,
    )


async def refusal(
    hass: HomeAssistant,
    service: str,
    *,
    context: Context | None = None,
    data: dict[str, Any] | None = None,
) -> HomeAssistantError:
    with pytest.raises(HomeAssistantError) as raised:
        await call(hass, service, context=context, data=data)
    return raised.value


def sessions_with_commands(cloud: FakeCloud) -> int:
    return sum(1 for s in cloud.sessions if s.panel and s.panel.received_commands)


@pytest.mark.parametrize("entry_options", [PIN])
@pytest.mark.usefixtures("loaded")
async def test_pin_mode(hass: HomeAssistant, cloud: FakeCloud) -> None:
    state = hass.states.get(entity_id(hass, "alarm"))
    assert state is not None
    assert state.attributes["code_format"] == "number"
    assert state.attributes["code_arm_required"] is True
    missing = await refusal(hass, "alarm_arm_away")
    assert isinstance(missing, ServiceValidationError)
    wrong = await refusal(hass, "alarm_arm_away", data={ATTR_CODE: "1234"})
    assert wrong.translation_key == "invalid_code"
    forced = await refusal(hass, "force_arm", data={ATTR_CODE: "0000"})
    assert forced.translation_key == "invalid_code"
    assert sessions_with_commands(cloud) == 0
    await call(hass, "alarm_arm_away", data={ATTR_CODE: "4711"})
    assert last_commands(cloud) == [PLAIN_A]
    disarm = await refusal(hass, "alarm_disarm")
    assert disarm.translation_key == "invalid_code"
    assert last_commands(cloud) == [PLAIN_A]
    await call(hass, "alarm_disarm", data={ATTR_CODE: "4711"})
    assert last_commands(cloud) == [bytes([0x2F, 0x01, 0])]


USERS = {CONF_ARM_ACCESS: "users", CONF_ALLOW_AUTOMATIONS: False}


async def users_entry(
    hass: HomeAssistant, entry: MockConfigEntry, *, automations: bool
) -> tuple[Context, Context]:
    """Set up ``entry`` in users mode; return a chosen and an unchosen context."""
    alice = await hass.auth.async_create_user("Alice", group_ids=[GROUP_ID_USER])
    mallory = await hass.auth.async_create_user("Mallory", group_ids=[GROUP_ID_USER])
    options = {**USERS, CONF_USERS: [alice.id], CONF_ALLOW_AUTOMATIONS: automations}
    hass.config_entries.async_update_entry(entry, options=options)
    assert await hass.config_entries.async_setup(entry.entry_id)
    await hass.async_block_till_done()
    return Context(user_id=alice.id), Context(user_id=mallory.id)


async def test_users_mode(
    hass: HomeAssistant, cloud: FakeCloud, entry: MockConfigEntry
) -> None:
    alice, mallory = await users_entry(hass, entry, automations=False)
    state = hass.states.get(entity_id(hass, "alarm"))
    assert state is not None
    assert state.attributes["code_format"] is None
    assert state.attributes["code_arm_required"] is False
    denied = await refusal(hass, "alarm_arm_away", context=mallory)
    assert denied.translation_key == "user_not_allowed"
    automation = await refusal(hass, "alarm_arm_away", context=Context())
    assert automation.translation_key == "automation_not_allowed"
    automation = await refusal(hass, "alarm_disarm", context=Context())
    assert automation.translation_key == "automation_not_allowed"
    assert sessions_with_commands(cloud) == 0
    await call(hass, "alarm_arm_away", context=alice)
    assert last_commands(cloud) == [PLAIN_A]


async def test_users_mode_can_allow_automations(
    hass: HomeAssistant, cloud: FakeCloud, entry: MockConfigEntry
) -> None:
    _alice, mallory = await users_entry(hass, entry, automations=True)
    await call(hass, "force_arm", context=Context(), data={"mode": "night"})
    assert last_commands(cloud) == [bytes([0x2D, 0x81, 1])]
    denied = await refusal(hass, "alarm_disarm", context=mallory)
    assert denied.translation_key == "user_not_allowed"


@pytest.mark.parametrize("entry_options", [PERSISTENT])
async def test_blocked_arm_names_the_fault(
    hass: HomeAssistant, cloud: FakeCloud, loaded: MockConfigEntry
) -> None:
    for text in dump.door_open_sequence():
        await cloud.push(text)
    await until(lambda: loaded.runtime_data.data.areas[0].faults != ())
    commands_before = list(last_commands(cloud))
    error = await refusal(hass, "alarm_arm_away")
    assert error.translation_key == "arm_refused"
    assert error.translation_placeholders == {
        "area": "Full Set",
        "reason": "Landing DT Open",
    }
    assert "Landing DT Open" in str(error)
    assert last_commands(cloud) == commands_before  # nothing was sent
    await call(hass, "force_arm")
    assert last_commands(cloud) == [*commands_before, FORCED_A]


@pytest.mark.parametrize("panel_behaviour", [PanelBehaviour(set_statuses=(6,))])
@pytest.mark.usefixtures("loaded")
async def test_panel_refusal_after_sending(
    hass: HomeAssistant, cloud: FakeCloud
) -> None:
    error = await refusal(hass, "alarm_arm_away")
    assert error.translation_key == "arm_refused"
    assert error.translation_placeholders == {
        "area": "Full Set",
        "reason": "Set Cancelled",
    }
    assert last_commands(cloud) == [PLAIN_A]  # sent once, never repeated


@pytest.mark.parametrize(
    ("data", "sent"),
    [
        ({}, FORCED_A),
        ({"mode": "away"}, FORCED_A),
        ({"mode": "night"}, bytes([0x2D, 0x81, 1])),
        ({"mode": "custom_bypass"}, bytes([0x2D, 0x81, 2])),
    ],
)
@pytest.mark.usefixtures("loaded")
async def test_force_arm_levels(
    hass: HomeAssistant, cloud: FakeCloud, data: dict[str, str], sent: bytes
) -> None:
    await call(hass, "force_arm", data=data)
    assert last_commands(cloud) == [sent]


@pytest.mark.parametrize(
    "panel_behaviour",
    [PanelBehaviour(documents=documents(set_areas=(0,), unset_areas=(0,)))],
)
@pytest.mark.usefixtures("loaded")
async def test_force_arm_of_an_unavailable_level(hass: HomeAssistant) -> None:
    error = await refusal(hass, "force_arm", data={"mode": "night"})
    assert error.translation_key == "mode_not_supported"


@pytest.mark.parametrize("panel_behaviour", [AREA_PANEL])
@pytest.mark.usefixtures("loaded")
async def test_force_arm_area_ignores_mode(
    hass: HomeAssistant, cloud: FakeCloud
) -> None:
    await call(hass, "force_arm", "area_1", data={"mode": "night"})
    assert last_commands(cloud) == [bytes([0x2D, 0x81, 1])]


@pytest.mark.parametrize("entry_options", [{CONF_ALWAYS_FORCE_ARM: True}])
@pytest.mark.usefixtures("loaded")
async def test_always_force_arm(hass: HomeAssistant, cloud: FakeCloud) -> None:
    await call(hass, "alarm_arm_night")
    assert last_commands(cloud) == [bytes([0x2D, 0x81, 1])]


@pytest.mark.parametrize("entry_options", [{**PIN, CONF_ALWAYS_FORCE_ARM: True}])
@pytest.mark.parametrize("panel_behaviour", [AREA_PANEL])
@pytest.mark.usefixtures("loaded")
async def test_area_entity_checks_access(hass: HomeAssistant, cloud: FakeCloud) -> None:
    with pytest.raises(ServiceValidationError):
        await call(hass, "alarm_disarm", "area_0", data={ATTR_CODE: "1"})
    await call(hass, "alarm_arm_away", "area_0", data={ATTR_CODE: "4711"})
    assert last_commands(cloud) == [FORCED_A]
