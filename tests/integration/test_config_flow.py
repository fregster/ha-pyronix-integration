# SPDX-FileCopyrightText: 2026 Paul Fryer
# SPDX-License-Identifier: Apache-2.0
from __future__ import annotations

import json
from pathlib import Path
from typing import TYPE_CHECKING, Any

from homeassistant.config_entries import SOURCE_USER
from homeassistant.data_entry_flow import FlowResultType
import pytest

from custom_components.pyronix_cloud.const import (
    CONF_ADVANCED,
    CONF_APP_PASSWORD,
    CONF_APP_USER,
    CONF_CLOUD_URL,
    CONF_DEVICE_ID,
    CONF_PANEL_ID,
    CONF_USER_CODE,
    DOMAIN,
)
from pyronix_cloud import LIVE_URL, CommunicationError
from pyronix_cloud.testing import dump
from pyronix_cloud.testing.fake_cloud import CloudBehaviour, FakeCloud
from pyronix_cloud.testing.fake_panel import PanelBehaviour
from tests.integration.conftest import ENTRY_DATA, Gate, dump_with, said_goodbye

if TYPE_CHECKING:
    from homeassistant.config_entries import ConfigFlowResult
    from homeassistant.core import HomeAssistant
    from pytest_homeassistant_custom_component.common import MockConfigEntry  # type: ignore[import-untyped]  # the HA test plugin ships no py.typed

USER_INPUT: dict[str, Any] = {
    CONF_APP_USER: "Home Assistant",
    CONF_PANEL_ID: " testpanl ",
    CONF_APP_PASSWORD: "app-password",
    CONF_USER_CODE: "1234",
    CONF_ADVANCED: {CONF_CLOUD_URL: LIVE_URL},
}
COMPONENT = Path(__file__).parents[2] / "custom_components" / "pyronix_cloud"


async def start(hass: HomeAssistant) -> ConfigFlowResult:
    return await hass.config_entries.flow.async_init(
        DOMAIN, context={"source": SOURCE_USER}
    )


def with_url(url: str) -> dict[str, Any]:
    return {**USER_INPUT, CONF_ADVANCED: {CONF_CLOUD_URL: url}}


async def submit(
    hass: HomeAssistant, flow: ConfigFlowResult, user_input: dict[str, Any]
) -> ConfigFlowResult:
    return await hass.config_entries.flow.async_configure(flow["flow_id"], user_input)


def field_names(result: ConfigFlowResult) -> list[str]:
    schema = result["data_schema"]
    assert schema is not None
    return [str(key) for key in schema.schema]


async def test_user_creates_entry(hass: HomeAssistant, cloud: FakeCloud) -> None:
    form = await start(hass)
    assert form["type"] is FlowResultType.FORM
    assert form["step_id"] == "user"
    assert field_names(form) == [
        CONF_APP_USER,
        CONF_PANEL_ID,
        CONF_APP_PASSWORD,
        CONF_USER_CODE,
        CONF_ADVANCED,
    ]
    result = await submit(hass, form, USER_INPUT)
    assert result["type"] is FlowResultType.CREATE_ENTRY
    assert result["title"] == "Pyronix alarm"
    data = result["data"]
    assert data[CONF_PANEL_ID] == dump.PANEL_ID
    assert CONF_CLOUD_URL not in data  # the default relay is not stored
    assert CONF_ADVANCED not in data
    assert cloud.sessions[0].url == LIVE_URL
    assert len(data[CONF_DEVICE_ID]) == 16
    int(data[CONF_DEVICE_ID], 16)
    assert result["result"].unique_id == dump.PANEL_ID
    # Validation is one read-only session: login, goodbye, no commands. (The
    # entry's own first poll follows it.)
    await hass.async_block_till_done()
    assert all(said_goodbye(s) for s in cloud.sessions)
    session = cloud.sessions[0]
    assert said_goodbye(session)
    assert session.panel is not None
    assert session.panel.received_commands == []


async def test_device_id_is_kept_across_retries(
    hass: HomeAssistant, gate: Gate
) -> None:
    form = await start(hass)
    gate.error = CommunicationError("down")
    failed = await submit(hass, form, USER_INPUT)
    assert failed["errors"] == {"base": "cannot_connect"}
    gate.error = None
    created = await submit(hass, failed, USER_INPUT)
    assert created["type"] is FlowResultType.CREATE_ENTRY
    other = await submit(hass, await start(hass), USER_INPUT)
    assert other["type"] is FlowResultType.ABORT
    assert other["reason"] == "already_configured"


@pytest.mark.parametrize(
    ("panel_behaviour", "title"),
    [
        (PanelBehaviour(documents=dump_with("  Home  ")), "Home"),
        (PanelBehaviour(documents=dump_with(None)), "Pyronix alarm"),
    ],
)
async def test_title_from_site_name(hass: HomeAssistant, title: str) -> None:
    result = await submit(hass, await start(hass), USER_INPUT)
    assert result["title"] == title


@pytest.mark.parametrize("system_id", ["ABC", "TESTPANEL", ""])
async def test_invalid_system_id(
    hass: HomeAssistant, cloud: FakeCloud, system_id: str
) -> None:
    result = await submit(
        hass, await start(hass), {**USER_INPUT, CONF_PANEL_ID: system_id}
    )
    assert result["type"] is FlowResultType.FORM
    assert result["errors"] == {CONF_PANEL_ID: "invalid_system_id"}
    assert cloud.sessions == []


@pytest.mark.parametrize(
    "url",
    [
        "wss://relay.example.com",
        "wss://apptestlb.pyronixcloud.com:443",
        "ws://localhost:8080",
        "ws://127.0.0.1:9000/",
    ],
)
async def test_advanced_url_accepted(
    hass: HomeAssistant, cloud: FakeCloud, url: str
) -> None:
    result = await submit(hass, await start(hass), with_url(url))
    assert result["type"] is FlowResultType.CREATE_ENTRY
    assert result["data"][CONF_CLOUD_URL] == url
    assert cloud.sessions[0].url == url


@pytest.mark.parametrize(
    "url",
    [
        "ws://relay.example.com",
        "ws://192.168.1.2",
        "http://localhost",
        "wss://",
        "wss://[::1",
        "relay.example.com",
    ],
)
async def test_advanced_url_rejected(
    hass: HomeAssistant, cloud: FakeCloud, url: str
) -> None:
    result = await submit(hass, await start(hass), with_url(url))
    assert result["type"] is FlowResultType.FORM
    assert result["errors"] == {"base": "invalid_url"}
    assert cloud.sessions == []


@pytest.mark.parametrize(
    ("letter", "error"),
    [
        ("a", "invalid_auth"),
        ("b", "panel_busy"),
        ("n", "panel_offline"),
        ("i", "subscription_expired"),
        ("z", "unknown"),
    ],
)
async def test_cloud_errors(
    hass: HomeAssistant, cloud: FakeCloud, letter: str, error: str
) -> None:
    form = await start(hass)
    cloud.behaviour = CloudBehaviour(error_letter=letter)
    result = await submit(hass, form, USER_INPUT)
    assert result["type"] is FlowResultType.FORM
    assert result["errors"] == {"base": error}
    assert cloud.sessions[0].closed_by_client
    cloud.behaviour = CloudBehaviour()
    assert (await submit(hass, result, USER_INPUT))["type"] is (
        FlowResultType.CREATE_ENTRY
    )


async def test_wrong_user_code(hass: HomeAssistant, cloud: FakeCloud) -> None:
    result = await submit(hass, await start(hass), {**USER_INPUT, CONF_USER_CODE: "9"})
    assert result["errors"] == {"base": "invalid_auth"}
    assert cloud.sessions[0].closed_by_client


@pytest.mark.parametrize(
    ("raised", "error"),
    [
        (CommunicationError("unreachable"), "cannot_connect"),
        (RuntimeError("bug"), "unknown"),
    ],
)
async def test_connection_errors(
    hass: HomeAssistant, gate: Gate, raised: Exception, error: str
) -> None:
    gate.error = raised
    result = await submit(hass, await start(hass), USER_INPUT)
    assert result["errors"] == {"base": error}


@pytest.mark.parametrize(
    ("field", "value", "error"),
    [
        (CONF_USER_CODE, "12ab", "invalid_user_code"),
        (CONF_USER_CODE, "123456789", "invalid_user_code"),
        (CONF_APP_PASSWORD, "pass<word", "invalid_characters"),
        (CONF_APP_USER, "Home & Away", "invalid_characters"),
    ],
)
async def test_malformed_credentials(
    hass: HomeAssistant, cloud: FakeCloud, field: str, value: str, error: str
) -> None:
    result = await submit(hass, await start(hass), {**USER_INPUT, field: value})
    assert result["errors"] == {field: error}
    assert value not in str(result)  # never echoed back
    assert cloud.sessions == []


async def test_reauth(hass: HomeAssistant, entry: MockConfigEntry) -> None:
    flow = await entry.start_reauth_flow(hass)
    assert flow["step_id"] == "reauth_confirm"
    failed = await submit(
        hass, flow, {CONF_APP_PASSWORD: "wrong", CONF_USER_CODE: "1234"}
    )
    assert failed["type"] is FlowResultType.FORM
    assert failed["errors"] == {"base": "invalid_auth"}
    result = await submit(
        hass, failed, {CONF_APP_PASSWORD: "app-password", CONF_USER_CODE: "1234"}
    )
    assert result["type"] is FlowResultType.ABORT
    assert result["reason"] == "reauth_successful"
    assert entry.data[CONF_DEVICE_ID] == ENTRY_DATA[CONF_DEVICE_ID]
    await hass.async_block_till_done()


async def test_reauth_rejects_a_malformed_code(
    hass: HomeAssistant, cloud: FakeCloud, entry: MockConfigEntry
) -> None:
    flow = await entry.start_reauth_flow(hass)
    result = await submit(
        hass, flow, {CONF_APP_PASSWORD: "app-password", CONF_USER_CODE: "12 34"}
    )
    assert result["errors"] == {CONF_USER_CODE: "invalid_user_code"}
    assert cloud.sessions == []


@pytest.mark.parametrize(
    "panel_behaviour", [PanelBehaviour(documents=dump.login_dump("OTHERPNL"))]
)
async def test_reauth_other_panel(hass: HomeAssistant, entry: MockConfigEntry) -> None:
    flow = await entry.start_reauth_flow(hass)
    result = await submit(
        hass, flow, {CONF_APP_PASSWORD: "app-password", CONF_USER_CODE: "1234"}
    )
    assert result["type"] is FlowResultType.ABORT
    assert result["reason"] == "unique_id_mismatch"


@pytest.mark.parametrize("panel_behaviour", [PanelBehaviour(documents=dump_with(None))])
async def test_reauth_without_panel_document(
    hass: HomeAssistant, entry: MockConfigEntry
) -> None:
    flow = await entry.start_reauth_flow(hass)
    result = await submit(
        hass, flow, {CONF_APP_PASSWORD: "app-password", CONF_USER_CODE: "1234"}
    )
    assert result["reason"] == "reauth_successful"
    await hass.async_block_till_done()


async def test_reconfigure(
    hass: HomeAssistant, cloud: FakeCloud, entry: MockConfigEntry
) -> None:
    flow = await entry.start_reconfigure_flow(hass)
    assert flow["step_id"] == "reconfigure"
    changes = {
        CONF_APP_USER: "Kitchen tablet",
        CONF_APP_PASSWORD: "app-password",
        CONF_USER_CODE: "4321",
    }
    malformed = await submit(hass, flow, {**changes, CONF_USER_CODE: ""})
    assert malformed["errors"] == {CONF_USER_CODE: "invalid_user_code"}
    failed = await submit(hass, malformed, changes)
    assert failed["errors"] == {"base": "invalid_auth"}
    cloud.behaviour = CloudBehaviour(error_letter="n")
    offline = await submit(hass, failed, {**changes, CONF_USER_CODE: "1234"})
    assert offline["errors"] == {"base": "panel_offline"}
    cloud.behaviour = CloudBehaviour()
    result = await submit(hass, offline, {**changes, CONF_USER_CODE: "1234"})
    assert result["type"] is FlowResultType.ABORT
    assert result["reason"] == "reconfigure_successful"
    assert entry.data[CONF_APP_USER] == "Kitchen tablet"
    assert entry.data[CONF_PANEL_ID] == dump.PANEL_ID
    await hass.async_block_till_done()


@pytest.mark.parametrize(
    "panel_behaviour", [PanelBehaviour(documents=dump.login_dump("OTHERPNL"))]
)
async def test_reconfigure_other_panel(
    hass: HomeAssistant, entry: MockConfigEntry
) -> None:
    flow = await entry.start_reconfigure_flow(hass)
    result = await submit(
        hass,
        flow,
        {
            CONF_APP_USER: "Home Assistant",
            CONF_APP_PASSWORD: "app-password",
            CONF_USER_CODE: "1234",
        },
    )
    assert result["type"] is FlowResultType.ABORT
    assert result["reason"] == "unique_id_mismatch"
    assert entry.data == ENTRY_DATA


def test_translations_match_strings() -> None:
    strings = json.loads((COMPONENT / "strings.json").read_text(encoding="utf-8"))
    english = json.loads(
        (COMPONENT / "translations" / "en.json").read_text(encoding="utf-8")
    )
    assert strings == english
