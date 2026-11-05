# SPDX-FileCopyrightText: 2026 Paul Fryer
# SPDX-License-Identifier: Apache-2.0
"""UI flows that create, re-authenticate and reconfigure Pyronix Cloud entries."""

from __future__ import annotations

import logging
import secrets
from typing import TYPE_CHECKING, Any, Final, override

from homeassistant.config_entries import ConfigFlow, ConfigFlowResult
from homeassistant.core import callback
from homeassistant.data_entry_flow import section
from homeassistant.helpers.selector import (
    TextSelector,
    TextSelectorConfig,
    TextSelectorType,
)
import voluptuous as vol

from pyronix_cloud import (
    LIVE_URL,
    AccessDeniedError,
    AuthenticationError,
    CommunicationError,
    ConnectionClosedError,
    PanelBusyError,
    PanelOfflineError,
    ProtocolError,
    PyronixTimeoutError,
    SubscriptionExpiredError,
)

from .client_factory import create_client
from .const import (
    CONF_ADVANCED,
    CONF_APP_PASSWORD,
    CONF_APP_USER,
    CONF_CLOUD_URL,
    CONF_DEVICE_ID,
    CONF_LIVE_UPDATES,
    CONF_MODE,
    CONF_PANEL_ID,
    CONF_USER_CODE,
    DEFAULT_APP_USER,
    DEVICE_ID_BYTES,
    DOMAIN,
    MODE_PERSISTENT,
)
from .input_checks import field_errors, normalise_system_id, url_allowed
from .options_flow import PyronixOptionsFlow

if TYPE_CHECKING:
    from collections.abc import Mapping

    from homeassistant.config_entries import ConfigEntry

    from pyronix_cloud import PanelSnapshot

_LOGGER = logging.getLogger(__name__)

_ERRORS: Final[
    tuple[tuple[type[Exception] | tuple[type[Exception], ...], str], ...]
] = (
    ((AuthenticationError, AccessDeniedError), "invalid_auth"),
    (PanelBusyError, "panel_busy"),
    (PanelOfflineError, "panel_offline"),
    (SubscriptionExpiredError, "subscription_expired"),
    (
        (CommunicationError, ConnectionClosedError, PyronixTimeoutError, ProtocolError),
        "cannot_connect",
    ),
)
_DEFAULT_TITLE: Final = "Pyronix alarm"

_SECRET = TextSelector(TextSelectorConfig(type=TextSelectorType.PASSWORD))
_SECRETS_SCHEMA: Final[dict[vol.Marker, Any]] = {
    vol.Required(CONF_APP_PASSWORD): _SECRET,
    vol.Required(CONF_USER_CODE): _SECRET,
}
_REAUTH_SCHEMA: Final = vol.Schema(_SECRETS_SCHEMA)
_RECONFIGURE_SCHEMA: Final = vol.Schema(
    {vol.Required(CONF_APP_USER): str, **_SECRETS_SCHEMA}
)


_USER_SCHEMA: Final = vol.Schema(
    {
        vol.Required(CONF_APP_USER, default=DEFAULT_APP_USER): str,
        vol.Required(CONF_PANEL_ID): str,
        **_SECRETS_SCHEMA,
        vol.Optional(CONF_LIVE_UPDATES, default=False): bool,
        vol.Required(CONF_ADVANCED): section(
            vol.Schema(  # type: ignore[arg-type,unused-ignore]  # probatio on HA >= 2026.3
                {vol.Optional(CONF_CLOUD_URL, default=LIVE_URL): str}
            ),
            {"collapsed": True},
        ),
    }
)


def _user_errors(user_input: Mapping[str, Any]) -> dict[str, str]:
    """Return the user step's form errors, checked before any connection."""
    errors = field_errors(user_input)
    url = user_input[CONF_ADVANCED].get(CONF_CLOUD_URL, LIVE_URL)
    if not url_allowed(url):
        errors["base"] = "invalid_url"  # shown even while the section is collapsed
    return errors


def _entry_data(user_input: Mapping[str, Any], device_id: str) -> dict[str, Any]:
    """Return entry data from the user step; the URL is kept only if not the default."""
    data = {
        key: value
        for key, value in user_input.items()
        if key not in {CONF_ADVANCED, CONF_LIVE_UPDATES}
    }
    data[CONF_DEVICE_ID] = device_id
    url = user_input[CONF_ADVANCED].get(CONF_CLOUD_URL, LIVE_URL)
    if url != LIVE_URL:
        data[CONF_CLOUD_URL] = url
    return data


def _entry_options(user_input: Mapping[str, Any]) -> dict[str, Any]:
    """Return the new entry's options: persistent mode if live updates were ticked."""
    if user_input.get(CONF_LIVE_UPDATES, False):
        return {CONF_MODE: MODE_PERSISTENT}
    return {}


def _error_key(err: Exception) -> str:
    """Return the form error for a failed validation session."""
    return next((key for kinds, key in _ERRORS if isinstance(err, kinds)), "unknown")


def _reported_id(snapshot: PanelSnapshot, fallback: str | None) -> str | None:
    """Return the System ID the panel reported, or ``fallback`` if it sent none."""
    if snapshot.info is None:
        return fallback
    return snapshot.info.panel_id.upper()


def _title(snapshot: PanelSnapshot) -> str:
    """Return the entry title: the panel's site name, if it has one."""
    site = snapshot.info.site_name.strip() if snapshot.info else ""
    return site or _DEFAULT_TITLE


class PyronixConfigFlow(ConfigFlow, domain=DOMAIN):
    """Add a panel: validate the app's login details with one read-only session."""

    VERSION = 1
    MINOR_VERSION = 1

    def __init__(self) -> None:
        """Generate this client's device ID once for the flow."""
        self._device_id = secrets.token_hex(DEVICE_ID_BYTES)

    @staticmethod
    @callback
    @override
    def async_get_options_flow(config_entry: ConfigEntry) -> PyronixOptionsFlow:
        """Return the options flow."""
        return PyronixOptionsFlow()

    @override
    async def async_step_user(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        """Ask for the app's login details and check them against the panel."""
        errors: dict[str, str] = {}
        if user_input is not None:
            user_input = {
                **user_input,
                CONF_PANEL_ID: normalise_system_id(user_input[CONF_PANEL_ID]),
            }
            errors = _user_errors(user_input)
            if not errors:
                await self.async_set_unique_id(user_input[CONF_PANEL_ID])
                self._abort_if_unique_id_configured()
                data = _entry_data(user_input, self._device_id)
                result = await self._async_validate(data)
                if isinstance(result, str):
                    errors["base"] = result
                else:
                    return self.async_create_entry(
                        title=_title(result),
                        data=data,
                        options=_entry_options(user_input),
                    )
        return self.async_show_form(
            step_id="user",
            data_schema=self.add_suggested_values_to_schema(
                _USER_SCHEMA,  # type: ignore[arg-type,unused-ignore]  # probatio on HA >= 2026.3
                user_input,
            ),
            errors=errors,
        )

    async def async_step_reauth(
        self,
        entry_data: Mapping[str, Any],  # noqa: ARG002 - Home Assistant's signature
    ) -> ConfigFlowResult:
        """Start re-authentication after the panel rejected the stored details."""
        return await self.async_step_reauth_confirm()

    async def async_step_reauth_confirm(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        """Ask for a new app password and user code."""
        entry = self._get_reauth_entry()
        return await self._async_update_entry(
            "reauth_confirm", entry, _REAUTH_SCHEMA, user_input
        )

    async def async_step_reconfigure(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        """Change the device name, app password or user code of an entry."""
        entry = self._get_reconfigure_entry()
        return await self._async_update_entry(
            "reconfigure", entry, _RECONFIGURE_SCHEMA, user_input
        )

    async def _async_update_entry(
        self,
        step_id: str,
        entry: ConfigEntry,
        schema: vol.Schema,
        user_input: dict[str, Any] | None,
    ) -> ConfigFlowResult:
        """Validate new details for ``entry``, then save them and reload it."""
        errors: dict[str, str] = {}
        if user_input is not None:
            errors = field_errors(user_input)
        if user_input is not None and not errors:
            result = await self._async_validate({**entry.data, **user_input})
            if isinstance(result, str):
                errors["base"] = result
            else:
                await self.async_set_unique_id(_reported_id(result, entry.unique_id))
                self._abort_if_unique_id_mismatch()
                return self.async_update_reload_and_abort(
                    entry, data_updates=user_input
                )
        suggested = {CONF_APP_USER: entry.data[CONF_APP_USER], **(user_input or {})}
        return self.async_show_form(
            step_id=step_id,
            data_schema=self.add_suggested_values_to_schema(
                schema,  # type: ignore[arg-type,unused-ignore]  # probatio on HA >= 2026.3
                suggested,
            ),
            errors=errors,
        )

    async def _async_validate(self, data: Mapping[str, Any]) -> PanelSnapshot | str:
        """Open and close one read-only session; return the state or an error key."""
        try:
            client = create_client(self.hass, data)
            async with client:
                return client.snapshot
        except Exception as err:
            key = _error_key(err)
            if key == "unknown":
                _LOGGER.exception("Unexpected error while validating the panel")
            return key
