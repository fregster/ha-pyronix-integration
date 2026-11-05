# SPDX-FileCopyrightText: 2026 Paul Fryer
# SPDX-License-Identifier: Apache-2.0
from __future__ import annotations

from typing import TYPE_CHECKING

from homeassistant.exceptions import ServiceValidationError
import pytest

from custom_components.pyronix_cloud.access import AccessPolicy, async_check_access
from custom_components.pyronix_cloud.const import (
    CONF_ALLOW_AUTOMATIONS,
    CONF_ARM_ACCESS,
    CONF_PIN_HASH,
    CONF_USERS,
    ArmAccess,
)
from custom_components.pyronix_cloud.pin_hash import hash_pin

if TYPE_CHECKING:
    from homeassistant.core import HomeAssistant

PIN_HASH = hash_pin("4711")


def test_missing_option_means_anyone() -> None:
    policy = AccessPolicy.from_options({})
    assert policy == AccessPolicy(mode=ArmAccess.ANYONE)
    assert not policy.code_required


def test_policy_from_options() -> None:
    pin = AccessPolicy.from_options({CONF_ARM_ACCESS: "pin", CONF_PIN_HASH: PIN_HASH})
    assert pin.code_required
    assert pin.pin_hash == PIN_HASH
    users = AccessPolicy.from_options(
        {CONF_ARM_ACCESS: "users", CONF_USERS: ["a", "b"], CONF_ALLOW_AUTOMATIONS: True}
    )
    assert users.users == frozenset({"a", "b"})
    assert users.allow_automations
    assert not users.code_required


async def denied(
    hass: HomeAssistant, policy: AccessPolicy, code: str | None, user: str | None
) -> str | None:
    """Return the translation key of the refusal, or None if allowed."""
    try:
        await async_check_access(hass, policy, code=code, user_id=user)
    except ServiceValidationError as err:
        return err.translation_key
    return None


@pytest.mark.parametrize(
    ("code", "user", "expected"),
    [
        ("4711", None, None),
        ("4711", "someone", None),
        ("4712", "someone", "invalid_code"),
        ("", None, "invalid_code"),
        (None, "someone", "invalid_code"),
    ],
)
async def test_pin_mode(
    hass: HomeAssistant, code: str | None, user: str | None, expected: str | None
) -> None:
    policy = AccessPolicy(mode=ArmAccess.PIN, pin_hash=PIN_HASH)
    assert await denied(hass, policy, code, user) == expected


async def test_pin_mode_without_a_stored_pin_denies(hass: HomeAssistant) -> None:
    policy = AccessPolicy(mode=ArmAccess.PIN)
    assert await denied(hass, policy, "4711", None) == "invalid_code"


@pytest.mark.parametrize(
    ("allow_automations", "user", "expected"),
    [
        (False, "alice", None),
        (False, "mallory", "user_not_allowed"),
        (False, None, "automation_not_allowed"),
        (True, None, None),
        (True, "mallory", "user_not_allowed"),
    ],
)
async def test_users_mode(
    hass: HomeAssistant,
    allow_automations: bool,  # noqa: FBT001 - a test parameter
    user: str | None,
    expected: str | None,
) -> None:
    policy = AccessPolicy(
        mode=ArmAccess.USERS,
        users=frozenset({"alice"}),
        allow_automations=allow_automations,
    )
    assert await denied(hass, policy, "ignored", user) == expected


async def test_anyone_mode(hass: HomeAssistant) -> None:
    policy = AccessPolicy(mode=ArmAccess.ANYONE)
    assert await denied(hass, policy, None, None) is None


async def test_refusals_are_translated(hass: HomeAssistant) -> None:
    policy = AccessPolicy(mode=ArmAccess.PIN, pin_hash=PIN_HASH)
    with pytest.raises(ServiceValidationError) as raised:
        await async_check_access(hass, policy, code="0000", user_id=None)
    assert raised.value.translation_domain == "pyronix_cloud"
