# SPDX-FileCopyrightText: 2026 Paul Fryer
# SPDX-License-Identifier: Apache-2.0
"""Decide whether a service call may arm or disarm, by the entry's access option."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Any, Final

from homeassistant.exceptions import ServiceValidationError

from .const import (
    CONF_ALLOW_AUTOMATIONS,
    CONF_ARM_ACCESS,
    CONF_PIN_HASH,
    CONF_USERS,
    DOMAIN,
    ArmAccess,
)
from .pin_hash import pin_matches

if TYPE_CHECKING:
    from collections.abc import Mapping

    from homeassistant.core import HomeAssistant


@dataclass(frozen=True, slots=True, kw_only=True)
class AccessPolicy:
    """Who may arm and disarm: PIN holders, chosen users, or anyone."""

    mode: ArmAccess
    pin_hash: str | None = field(default=None, repr=False)
    users: frozenset[str] = frozenset()
    allow_automations: bool = False

    @classmethod
    def from_options(cls, options: Mapping[str, Any]) -> AccessPolicy:
        """Read the policy from entry options; no access option means anyone.

        Entries created before the option existed keep working unchanged; a
        repair issue asks the admin to choose (see access_issue.py).
        """
        return cls(
            mode=ArmAccess(options.get(CONF_ARM_ACCESS, ArmAccess.ANYONE)),
            pin_hash=options.get(CONF_PIN_HASH),
            users=frozenset(options.get(CONF_USERS, ())),
            allow_automations=bool(options.get(CONF_ALLOW_AUTOMATIONS, False)),
        )

    @property
    def code_required(self) -> bool:
        """Whether every arm and disarm needs the PIN."""
        return self.mode is ArmAccess.PIN


INVALID_CODE: Final = "invalid_code"
USER_NOT_ALLOWED: Final = "user_not_allowed"
AUTOMATION_NOT_ALLOWED: Final = "automation_not_allowed"


def _denied(key: str) -> ServiceValidationError:
    """Return the translated error for a refused call."""
    return ServiceValidationError(translation_domain=DOMAIN, translation_key=key)


async def async_check_access(
    hass: HomeAssistant,
    policy: AccessPolicy,
    *,
    code: str | None,
    user_id: str | None,
) -> None:
    """Raise unless the call may arm or disarm.

    Args:
        hass: Runs the PIN check in an executor.
        policy: The entry's access policy.
        code: The code given with the call, if any.
        user_id: The calling user; None for automations and scripts.

    Raises:
        ServiceValidationError: The PIN is missing or wrong, the user is not
            chosen, or a call without a user is not allowed.
    """
    if policy.mode is ArmAccess.PIN:
        stored = policy.pin_hash
        if not code or stored is None:
            raise _denied(INVALID_CODE)
        if not await hass.async_add_executor_job(pin_matches, code, stored):
            raise _denied(INVALID_CODE)
    elif policy.mode is ArmAccess.USERS:
        if user_id is None:
            if not policy.allow_automations:
                raise _denied(AUTOMATION_NOT_ALLOWED)
        elif user_id not in policy.users:
            raise _denied(USER_NOT_ALLOWED)
