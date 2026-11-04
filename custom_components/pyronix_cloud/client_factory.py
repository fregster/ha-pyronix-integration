# SPDX-FileCopyrightText: 2026 Paul Fryer
# SPDX-License-Identifier: Apache-2.0
"""Create pyronix_cloud clients for a config entry."""

from __future__ import annotations

from typing import TYPE_CHECKING, Any

from homeassistant.helpers.aiohttp_client import async_get_clientsession

from pyronix_cloud import (
    LIVE_URL,
    Credentials,
    PyronixClient,
    Timeouts,
    aiohttp_connector,
)

from .const import (
    CONF_APP_PASSWORD,
    CONF_APP_USER,
    CONF_CLOUD_URL,
    CONF_DEVICE_ID,
    CONF_PANEL_ID,
    CONF_USER_CODE,
)

if TYPE_CHECKING:
    from collections.abc import Mapping

    from homeassistant.core import HomeAssistant

    from pyronix_cloud.websocket import Connector


def session_timeouts() -> Timeouts:
    """Return the phase timeouts of every panel session (tests shorten them)."""
    return Timeouts()


def connector_for(hass: HomeAssistant) -> Connector:
    """Return a WebSocket connector that uses Home Assistant's shared aiohttp session.

    Args:
        hass: The Home Assistant instance.
    """
    session = async_get_clientsession(hass)
    return aiohttp_connector(session, timeout=session_timeouts().connect)


def create_client(hass: HomeAssistant, data: Mapping[str, Any]) -> PyronixClient:
    """Return a client for the panel described by config entry data.

    Nothing is sent until the client connects.

    Args:
        hass: The Home Assistant instance.
        data: Config entry data (or a config flow's equivalent).

    Raises:
        ValueError: A credential is malformed; the message names no value.
    """
    credentials = Credentials(
        app_user=data[CONF_APP_USER],
        panel_id=data[CONF_PANEL_ID],
        app_password=data[CONF_APP_PASSWORD],
        user_code=data[CONF_USER_CODE],
        device_id=data[CONF_DEVICE_ID],
    )
    return PyronixClient(
        credentials,
        connector_for(hass),
        url=data.get(CONF_CLOUD_URL, LIVE_URL),
        timeouts=session_timeouts(),
    )
