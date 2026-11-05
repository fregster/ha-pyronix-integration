# SPDX-FileCopyrightText: 2026 Paul Fryer
# SPDX-License-Identifier: Apache-2.0
"""Form fields for choosing who may arm and disarm, shared by the config flows.

The setup flow and the options flow show the same fields, check them the same
way, and turn them into the same options. The PIN itself is never stored, only
its salted hash.
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Any

from homeassistant.helpers.selector import (
    BooleanSelector,
    SelectOptionDict,
    SelectSelector,
    SelectSelectorConfig,
    SelectSelectorMode,
    TextSelector,
    TextSelectorConfig,
    TextSelectorType,
)
import voluptuous as vol

from .const import (
    CONF_ALLOW_AUTOMATIONS,
    CONF_ARM_ACCESS,
    CONF_PIN,
    CONF_PIN_HASH,
    CONF_USERS,
    PIN_MAX_LENGTH,
    PIN_MIN_LENGTH,
    ArmAccess,
)
from .pin_hash import hash_pin

if TYPE_CHECKING:
    from collections.abc import Mapping

    from homeassistant.core import HomeAssistant


async def _user_options(hass: HomeAssistant) -> list[SelectOptionDict]:
    """Return the active, human Home Assistant users as select options."""
    users = await hass.auth.async_get_users()
    return [
        SelectOptionDict(value=user.id, label=user.name or user.id)
        for user in sorted(users, key=lambda u: (u.name or "").casefold())
        if user.is_active and not user.system_generated
    ]


async def async_access_fields(
    hass: HomeAssistant, current: Mapping[str, Any]
) -> dict[vol.Marker, Any]:
    """Return the access fields, defaulting to ``current`` (PIN mode if unset).

    Args:
        hass: Lists the users to choose from.
        current: The saved options, or the values just submitted.
    """
    return {
        vol.Required(
            CONF_ARM_ACCESS, default=current.get(CONF_ARM_ACCESS, ArmAccess.PIN)
        ): SelectSelector(
            SelectSelectorConfig(
                options=[mode.value for mode in ArmAccess],
                mode=SelectSelectorMode.LIST,
                translation_key=CONF_ARM_ACCESS,
            )
        ),
        vol.Optional(CONF_PIN): TextSelector(
            TextSelectorConfig(type=TextSelectorType.PASSWORD)
        ),
        vol.Optional(CONF_USERS, default=list(current.get(CONF_USERS, []))): (
            SelectSelector(
                SelectSelectorConfig(
                    options=await _user_options(hass),
                    multiple=True,
                    mode=SelectSelectorMode.DROPDOWN,
                )
            )
        ),
        vol.Optional(
            CONF_ALLOW_AUTOMATIONS,
            default=bool(current.get(CONF_ALLOW_AUTOMATIONS, False)),
        ): BooleanSelector(),
    }


def _pin_valid(pin: str) -> bool:
    """Return whether ``pin`` is 4 to 8 digits."""
    return (
        pin.isdecimal()
        and pin.isascii()
        and (PIN_MIN_LENGTH <= len(pin) <= PIN_MAX_LENGTH)
    )


def access_errors(
    user_input: Mapping[str, Any], previous: Mapping[str, Any]
) -> dict[str, str]:
    """Return the form errors of the access fields.

    Args:
        user_input: The submitted form.
        previous: The saved options; a saved PIN may be kept by leaving the
            PIN field empty.
    """
    mode = ArmAccess(user_input[CONF_ARM_ACCESS])
    if mode is ArmAccess.PIN:
        pin = user_input.get(CONF_PIN, "")
        if (pin and not _pin_valid(pin)) or (not pin and CONF_PIN_HASH not in previous):
            return {CONF_PIN: "invalid_pin"}
    if mode is ArmAccess.USERS and not user_input.get(CONF_USERS):
        return {CONF_USERS: "no_users"}
    return {}


async def async_access_options(
    hass: HomeAssistant, user_input: Mapping[str, Any], previous: Mapping[str, Any]
) -> dict[str, Any]:
    """Return the options to store for valid access fields.

    Args:
        hass: Hashes a new PIN in an executor.
        user_input: The submitted form, checked by :func:`access_errors`.
        previous: The saved options, whose PIN hash is kept if no new PIN.
    """
    mode = ArmAccess(user_input[CONF_ARM_ACCESS])
    options: dict[str, Any] = {CONF_ARM_ACCESS: mode.value}
    if mode is ArmAccess.PIN:
        pin = user_input.get(CONF_PIN)
        options[CONF_PIN_HASH] = (
            await hass.async_add_executor_job(hash_pin, pin)
            if pin
            else previous[CONF_PIN_HASH]
        )
    elif mode is ArmAccess.USERS:
        options[CONF_USERS] = list(user_input[CONF_USERS])
        options[CONF_ALLOW_AUTOMATIONS] = bool(
            user_input.get(CONF_ALLOW_AUTOMATIONS, False)
        )
    return options
