# SPDX-FileCopyrightText: 2026 Paul Fryer
# SPDX-License-Identifier: Apache-2.0
"""Options flow that picks the connection mode and the on-demand poll interval."""

from __future__ import annotations

from typing import TYPE_CHECKING, Any

from homeassistant.config_entries import OptionsFlowWithReload
from homeassistant.helpers.selector import (
    NumberSelector,
    NumberSelectorConfig,
    NumberSelectorMode,
    SelectSelector,
    SelectSelectorConfig,
    SelectSelectorMode,
)
import voluptuous as vol

from .const import (
    CONF_MODE,
    CONF_SCAN_INTERVAL,
    DEFAULT_SCAN_INTERVAL,
    MAX_SCAN_INTERVAL,
    MIN_SCAN_INTERVAL,
    MODE_ON_DEMAND,
    MODE_PERSISTENT,
)

if TYPE_CHECKING:
    from homeassistant.config_entries import ConfigFlowResult


class PyronixOptionsFlow(OptionsFlowWithReload):
    """Choose on-demand polling or a persistent session, and the poll interval."""

    async def async_step_init(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        """Show and save the options; the entry reloads when they change."""
        if user_input is not None:
            return self.async_create_entry(
                data={
                    CONF_MODE: user_input[CONF_MODE],
                    CONF_SCAN_INTERVAL: int(user_input[CONF_SCAN_INTERVAL]),
                }
            )
        schema = vol.Schema(
            {
                vol.Required(CONF_MODE, default=MODE_ON_DEMAND): SelectSelector(
                    SelectSelectorConfig(
                        options=[MODE_ON_DEMAND, MODE_PERSISTENT],
                        mode=SelectSelectorMode.LIST,
                        translation_key=CONF_MODE,
                    )
                ),
                vol.Required(
                    CONF_SCAN_INTERVAL, default=DEFAULT_SCAN_INTERVAL
                ): NumberSelector(
                    NumberSelectorConfig(
                        min=MIN_SCAN_INTERVAL,
                        max=MAX_SCAN_INTERVAL,
                        step=1,
                        mode=NumberSelectorMode.BOX,
                        unit_of_measurement="min",
                    )
                ),
            }
        )
        return self.async_show_form(
            step_id="init",
            data_schema=self.add_suggested_values_to_schema(
                schema,  # type: ignore[arg-type,unused-ignore]  # probatio on HA >= 2026.3
                self.config_entry.options,
            ),
        )
