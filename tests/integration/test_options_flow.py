# SPDX-FileCopyrightText: 2026 Paul Fryer
# SPDX-License-Identifier: Apache-2.0
from __future__ import annotations

from typing import TYPE_CHECKING, Any

from homeassistant.data_entry_flow import FlowResultType
import pytest
import voluptuous as vol

from custom_components.pyronix_cloud.const import (
    CONF_ALLOW_AUTOMATIONS,
    CONF_ALWAYS_FORCE_ARM,
    CONF_ARM_ACCESS,
    CONF_MODE,
    CONF_PIN,
    CONF_PIN_HASH,
    CONF_SCAN_INTERVAL,
    CONF_USERS,
    MODE_ON_DEMAND,
    MODE_PERSISTENT,
)
from custom_components.pyronix_cloud.pin_hash import hash_pin, pin_matches

if TYPE_CHECKING:
    from homeassistant.config_entries import ConfigFlowResult
    from homeassistant.core import HomeAssistant
    from pytest_homeassistant_custom_component.common import MockConfigEntry  # type: ignore[import-untyped]  # the HA test plugin ships no py.typed

BASE: dict[str, Any] = {
    CONF_MODE: MODE_ON_DEMAND,
    CONF_SCAN_INTERVAL: 2.0,
    CONF_ALWAYS_FORCE_ARM: False,
}
SAVED_PIN = hash_pin("4711")


def defaults(form: ConfigFlowResult) -> dict[str, Any]:
    schema = form["data_schema"]
    assert schema is not None
    return {
        str(key): key.default()
        for key in schema.schema
        if key.default is not vol.UNDEFINED
    }


def suggested(form: ConfigFlowResult) -> dict[str, Any]:
    schema = form["data_schema"]
    assert schema is not None
    return {
        str(key): key.description["suggested_value"]
        for key in schema.schema
        if key.description and "suggested_value" in key.description
    }


async def configure(
    hass: HomeAssistant, entry: MockConfigEntry, user_input: dict[str, Any]
) -> ConfigFlowResult:
    form = await hass.config_entries.options.async_init(entry.entry_id)
    return await hass.config_entries.options.async_configure(
        form["flow_id"], user_input
    )


async def test_options_flow(hass: HomeAssistant, entry: MockConfigEntry) -> None:
    form = await hass.config_entries.options.async_init(entry.entry_id)
    assert form["type"] is FlowResultType.FORM
    assert form["step_id"] == "init"
    result = await hass.config_entries.options.async_configure(
        form["flow_id"],
        {
            **BASE,
            CONF_MODE: MODE_PERSISTENT,
            CONF_SCAN_INTERVAL: 5.0,
            CONF_ARM_ACCESS: "anyone",
            CONF_ALWAYS_FORCE_ARM: True,
        },
    )
    assert result["type"] is FlowResultType.CREATE_ENTRY
    assert entry.options == {
        CONF_MODE: MODE_PERSISTENT,
        CONF_SCAN_INTERVAL: 5,
        CONF_ARM_ACCESS: "anyone",
        CONF_ALWAYS_FORCE_ARM: True,
    }


async def test_options_form_defaults(
    hass: HomeAssistant, entry: MockConfigEntry
) -> None:
    form = await hass.config_entries.options.async_init(entry.entry_id)
    # An entry from before the access option shows what it does now: anyone.
    assert defaults(form) == {
        CONF_MODE: MODE_ON_DEMAND,
        CONF_SCAN_INTERVAL: 2,
        CONF_ARM_ACCESS: "anyone",
        CONF_USERS: [],
        CONF_ALLOW_AUTOMATIONS: False,
        CONF_ALWAYS_FORCE_ARM: False,
    }
    hass.config_entries.options.async_abort(form["flow_id"])


@pytest.mark.parametrize(
    "entry_options", [{CONF_ARM_ACCESS: "pin", CONF_PIN_HASH: SAVED_PIN}]
)
async def test_empty_pin_keeps_the_saved_pin(
    hass: HomeAssistant, entry: MockConfigEntry
) -> None:
    form = await hass.config_entries.options.async_init(entry.entry_id)
    assert CONF_PIN_HASH not in str(form)
    assert defaults(form)[CONF_ARM_ACCESS] == "pin"
    result = await hass.config_entries.options.async_configure(
        form["flow_id"], {**BASE, CONF_ARM_ACCESS: "pin"}
    )
    assert result["type"] is FlowResultType.CREATE_ENTRY
    assert entry.options[CONF_PIN_HASH] == SAVED_PIN


@pytest.mark.parametrize(
    "entry_options", [{CONF_ARM_ACCESS: "pin", CONF_PIN_HASH: SAVED_PIN}]
)
async def test_new_pin_replaces_the_saved_pin(
    hass: HomeAssistant, entry: MockConfigEntry
) -> None:
    result = await configure(
        hass, entry, {**BASE, CONF_ARM_ACCESS: "pin", CONF_PIN: "9999"}
    )
    assert result["type"] is FlowResultType.CREATE_ENTRY
    assert pin_matches("9999", entry.options[CONF_PIN_HASH])
    assert "9999" not in str(entry.options)


async def test_pin_mode_needs_a_pin_when_none_is_saved(
    hass: HomeAssistant, entry: MockConfigEntry
) -> None:
    result = await configure(
        hass, entry, {**BASE, CONF_ARM_ACCESS: "pin", CONF_PIN: "12"}
    )
    assert result["type"] is FlowResultType.FORM
    assert result["errors"] == {CONF_PIN: "invalid_pin"}
    assert CONF_PIN not in suggested(result)  # the PIN is never echoed back
    assert suggested(result)[CONF_ARM_ACCESS] == "pin"
    retry = await hass.config_entries.options.async_configure(
        result["flow_id"], {**BASE, CONF_ARM_ACCESS: "pin"}
    )
    assert retry["errors"] == {CONF_PIN: "invalid_pin"}
    hass.config_entries.options.async_abort(result["flow_id"])


async def test_users_mode(hass: HomeAssistant, entry: MockConfigEntry) -> None:
    alice = await hass.auth.async_create_user("Alice")
    failed = await configure(hass, entry, {**BASE, CONF_ARM_ACCESS: "users"})
    assert failed["errors"] == {CONF_USERS: "no_users"}
    result = await hass.config_entries.options.async_configure(
        failed["flow_id"],
        {**BASE, CONF_ARM_ACCESS: "users", CONF_USERS: [alice.id]},
    )
    assert result["type"] is FlowResultType.CREATE_ENTRY
    assert entry.options == {
        CONF_MODE: MODE_ON_DEMAND,
        CONF_SCAN_INTERVAL: 2,
        CONF_ARM_ACCESS: "users",
        CONF_USERS: [alice.id],
        CONF_ALLOW_AUTOMATIONS: False,
        CONF_ALWAYS_FORCE_ARM: False,
    }
