# SPDX-FileCopyrightText: 2026 Paul Fryer
# SPDX-License-Identifier: Apache-2.0
"""Raise or clear the repair issue that asks an admin who may arm and disarm.

Entries created before the access option existed still let anyone arm and
disarm, so upgrades keep working; the issue stays until the options are saved.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

from homeassistant.core import callback
from homeassistant.helpers import issue_registry as ir

from .const import CONF_ARM_ACCESS, DOMAIN

if TYPE_CHECKING:
    from homeassistant.config_entries import ConfigEntry
    from homeassistant.core import HomeAssistant

ISSUE_PREFIX = "arm_access_not_set"


def issue_id(entry: ConfigEntry) -> str:
    """Return the issue ID for ``entry``."""
    return f"{ISSUE_PREFIX}_{entry.entry_id}"


@callback
def async_update_access_issue(hass: HomeAssistant, entry: ConfigEntry) -> None:
    """Raise the issue if ``entry`` has no access option, else clear it."""
    if CONF_ARM_ACCESS in entry.options:
        async_delete_access_issue(hass, entry)
        return
    ir.async_create_issue(
        hass,
        DOMAIN,
        issue_id(entry),
        is_fixable=False,
        severity=ir.IssueSeverity.WARNING,
        translation_key=ISSUE_PREFIX,
        translation_placeholders={"title": entry.title},
    )


@callback
def async_delete_access_issue(hass: HomeAssistant, entry: ConfigEntry) -> None:
    """Clear the issue of ``entry``, if raised."""
    ir.async_delete_issue(hass, DOMAIN, issue_id(entry))
