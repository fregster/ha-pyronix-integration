# SPDX-FileCopyrightText: 2026 Paul Fryer
# SPDX-License-Identifier: Apache-2.0
from __future__ import annotations

from typing import TYPE_CHECKING

from homeassistant.const import ATTR_ENTITY_ID
from homeassistant.setup import async_setup_component
import pytest
import voluptuous as vol

from custom_components.pyronix_cloud.const import DOMAIN, SERVICE_FORCE_ARM

if TYPE_CHECKING:
    from homeassistant.core import HomeAssistant


async def test_force_arm_is_registered_without_entries(hass: HomeAssistant) -> None:
    assert await async_setup_component(hass, DOMAIN, {})
    assert hass.services.has_service(DOMAIN, SERVICE_FORCE_ARM)


async def test_force_arm_rejects_an_unknown_mode(hass: HomeAssistant) -> None:
    assert await async_setup_component(hass, DOMAIN, {})
    with pytest.raises(vol.Invalid):
        await hass.services.async_call(
            DOMAIN,
            SERVICE_FORCE_ARM,
            {ATTR_ENTITY_ID: "alarm_control_panel.x", "mode": "home"},
            blocking=True,
        )
