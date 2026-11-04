# SPDX-FileCopyrightText: 2026 Paul Fryer
# SPDX-License-Identifier: Apache-2.0
from __future__ import annotations

from typing import TYPE_CHECKING

from homeassistant.data_entry_flow import FlowResultType

from custom_components.pyronix_cloud.const import (
    CONF_MODE,
    CONF_SCAN_INTERVAL,
    MODE_ON_DEMAND,
    MODE_PERSISTENT,
)

if TYPE_CHECKING:
    from homeassistant.core import HomeAssistant
    from pytest_homeassistant_custom_component.common import MockConfigEntry  # type: ignore[import-untyped]  # the HA test plugin ships no py.typed


async def test_options_flow(hass: HomeAssistant, entry: MockConfigEntry) -> None:
    form = await hass.config_entries.options.async_init(entry.entry_id)
    assert form["type"] is FlowResultType.FORM
    assert form["step_id"] == "init"
    result = await hass.config_entries.options.async_configure(
        form["flow_id"], {CONF_MODE: MODE_PERSISTENT, CONF_SCAN_INTERVAL: 5.0}
    )
    assert result["type"] is FlowResultType.CREATE_ENTRY
    assert entry.options == {CONF_MODE: MODE_PERSISTENT, CONF_SCAN_INTERVAL: 5}


async def test_options_form_defaults(
    hass: HomeAssistant, entry: MockConfigEntry
) -> None:
    form = await hass.config_entries.options.async_init(entry.entry_id)
    schema = form["data_schema"]
    assert schema is not None
    defaults = {str(key): key.default() for key in schema.schema}
    assert defaults == {CONF_MODE: MODE_ON_DEMAND, CONF_SCAN_INTERVAL: 2}
    hass.config_entries.options.async_abort(form["flow_id"])
