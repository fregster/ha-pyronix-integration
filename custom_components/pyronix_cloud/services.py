# SPDX-FileCopyrightText: 2026 Paul Fryer
# SPDX-License-Identifier: Apache-2.0
"""Register the integration's service actions on its alarm entities."""

from __future__ import annotations

from typing import TYPE_CHECKING, Final

from homeassistant.components.alarm_control_panel.const import (
    DOMAIN as ALARM_CONTROL_PANEL_DOMAIN,
)
from homeassistant.const import ATTR_CODE
from homeassistant.core import callback
from homeassistant.helpers import config_validation as cv
from homeassistant.helpers import service
import voluptuous as vol

from .const import ATTR_MODE, DOMAIN, FORCE_ARM_MODES, SERVICE_FORCE_ARM

if TYPE_CHECKING:
    from homeassistant.core import HomeAssistant

FORCE_ARM_SCHEMA: Final = {
    vol.Optional(ATTR_CODE): cv.string,
    vol.Optional(ATTR_MODE): vol.In(list(FORCE_ARM_MODES)),
}


@callback
def async_setup_services(hass: HomeAssistant) -> None:
    """Register ``pyronix_cloud.force_arm`` for this integration's alarm entities."""
    service.async_register_platform_entity_service(
        hass,
        DOMAIN,
        SERVICE_FORCE_ARM,
        entity_domain=ALARM_CONTROL_PANEL_DOMAIN,
        schema=FORCE_ARM_SCHEMA,  # type: ignore[arg-type,unused-ignore]  # probatio on HA >= 2026.3
        func="async_force_arm",
    )
