# SPDX-FileCopyrightText: 2026 Paul Fryer
# SPDX-License-Identifier: Apache-2.0
from __future__ import annotations

from typing import TYPE_CHECKING

from homeassistant.helpers import issue_registry as ir
import pytest

from custom_components.pyronix_cloud.access_issue import issue_id
from custom_components.pyronix_cloud.const import CONF_ARM_ACCESS, DOMAIN

if TYPE_CHECKING:
    from homeassistant.core import HomeAssistant
    from pytest_homeassistant_custom_component.common import MockConfigEntry  # type: ignore[import-untyped]  # the HA test plugin ships no py.typed


def issue(hass: HomeAssistant, entry: MockConfigEntry) -> ir.IssueEntry | None:
    return ir.async_get(hass).async_get_issue(DOMAIN, issue_id(entry))


async def test_entry_without_access_option_raises_an_issue(
    hass: HomeAssistant, loaded: MockConfigEntry
) -> None:
    raised = issue(hass, loaded)
    assert raised is not None
    assert raised.translation_key == "arm_access_not_set"
    assert raised.translation_placeholders == {"title": "Pyronix alarm"}
    assert raised.severity is ir.IssueSeverity.WARNING
    assert not raised.is_fixable
    # Saving the options (any access choice) reloads the entry and clears it.
    hass.config_entries.async_update_entry(loaded, options={CONF_ARM_ACCESS: "anyone"})
    await hass.config_entries.async_reload(loaded.entry_id)
    await hass.async_block_till_done()
    assert issue(hass, loaded) is None


async def test_removing_the_entry_clears_the_issue(
    hass: HomeAssistant, loaded: MockConfigEntry
) -> None:
    assert issue(hass, loaded) is not None
    await hass.config_entries.async_remove(loaded.entry_id)
    await hass.async_block_till_done()
    assert issue(hass, loaded) is None


@pytest.mark.parametrize("entry_options", [{CONF_ARM_ACCESS: "anyone"}])
async def test_entry_with_access_option_has_no_issue(
    hass: HomeAssistant, loaded: MockConfigEntry
) -> None:
    assert issue(hass, loaded) is None
