# SPDX-FileCopyrightText: 2026 Paul Fryer
# SPDX-License-Identifier: Apache-2.0
"""Options flow for the connection mode, poll interval and arm/disarm rules."""

from __future__ import annotations

from typing import TYPE_CHECKING, Any

from homeassistant.config_entries import OptionsFlowWithReload
from homeassistant.helpers.selector import (
    BooleanSelector,
    NumberSelector,
    NumberSelectorConfig,
    NumberSelectorMode,
    SelectSelector,
    SelectSelectorConfig,
    SelectSelectorMode,
)
import voluptuous as vol

from .access_form import access_errors, async_access_fields, async_access_options
from .const import (
    CONF_ALWAYS_FORCE_ARM,
    CONF_ARM_ACCESS,
    CONF_MODE,
    CONF_PIN,
    CONF_SCAN_INTERVAL,
    DEFAULT_SCAN_INTERVAL,
    MAX_SCAN_INTERVAL,
    MIN_SCAN_INTERVAL,
    MODE_ON_DEMAND,
    MODE_PERSISTENT,
    ArmAccess,
)

if TYPE_CHECKING:
    from homeassistant.config_entries import ConfigFlowResult


class PyronixOptionsFlow(OptionsFlowWithReload):
    """Choose the connection mode, poll interval and who may arm and disarm."""

    async def async_step_init(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        """Show and save the options; the entry reloads when they change."""
        saved = self.config_entry.options
        errors: dict[str, str] = {}
        if user_input is not None:
            errors = access_errors(user_input, saved)
            if not errors:
                access = await async_access_options(self.hass, user_input, saved)
                return self.async_create_entry(
                    data={
                        CONF_MODE: user_input[CONF_MODE],
                        CONF_SCAN_INTERVAL: int(user_input[CONF_SCAN_INTERVAL]),
                        **access,
                        CONF_ALWAYS_FORCE_ARM: user_input[CONF_ALWAYS_FORCE_ARM],
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
                # An entry from before the access option shows what it does now.
                **await async_access_fields(
                    self.hass, {CONF_ARM_ACCESS: ArmAccess.ANYONE, **saved}
                ),
                vol.Required(CONF_ALWAYS_FORCE_ARM, default=False): BooleanSelector(),
            }
        )
        return self.async_show_form(
            step_id="init",
            data_schema=self.add_suggested_values_to_schema(
                schema,  # type: ignore[arg-type,unused-ignore]  # probatio on HA >= 2026.3
                {k: v for k, v in (user_input or saved).items() if k != CONF_PIN},
            ),
            errors=errors,
        )
