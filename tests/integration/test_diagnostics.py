# SPDX-FileCopyrightText: 2026 Paul Fryer
# SPDX-License-Identifier: Apache-2.0
from __future__ import annotations

import base64
import json
from typing import TYPE_CHECKING

from homeassistant.components.diagnostics import REDACTED
import pytest
from pytest_homeassistant_custom_component.common import MockConfigEntry  # type: ignore[import-untyped]  # the HA test plugin ships no py.typed

from custom_components.pyronix_cloud.const import (
    CONF_ARM_ACCESS,
    CONF_CLOUD_URL,
    CONF_PIN_HASH,
    DOMAIN,
)
from custom_components.pyronix_cloud.diagnostics import (
    async_get_config_entry_diagnostics,
)
from pyronix_cloud.testing import dump
from pyronix_cloud.testing.fake_panel import PanelBehaviour
from tests.integration.conftest import ENTRY_DATA, PERSISTENT, dump_with, until

if TYPE_CHECKING:
    from homeassistant.core import HomeAssistant

    from pyronix_cloud.testing.fake_cloud import FakeCloud

CLOUD_URL = "ws://127.0.0.1:8765/private-relay"
PIN_HASH = "scrypt$16384$8$1$0011$aabbccdd"  # shaped like a real one
OPTIONS = {**PERSISTENT, CONF_ARM_ACCESS: "pin", CONF_PIN_HASH: PIN_HASH}
SITE_NAME = "12 Acacia Avenue"
SECRETS = [
    SITE_NAME,
    *(str(value) for value in ENTRY_DATA.values()),  # app password and user code
    CLOUD_URL,
    PIN_HASH,
    dump.PANEL_ID,
    "Manager",  # the keypad user's name
    "User 1",  # who last set an area
]


@pytest.mark.parametrize(
    "panel_behaviour", [PanelBehaviour(documents=dump_with(SITE_NAME))]
)
async def test_diagnostics_are_redacted(hass: HomeAssistant, cloud: FakeCloud) -> None:
    entry = MockConfigEntry(
        domain=DOMAIN,
        title="Pyronix alarm",
        data={**ENTRY_DATA, CONF_CLOUD_URL: CLOUD_URL},
        options=OPTIONS,
        unique_id=dump.PANEL_ID,
    )
    entry.add_to_hass(hass)
    assert await hass.config_entries.async_setup(entry.entry_id)
    await hass.async_block_till_done()
    for text in dump.door_open_sequence():
        await cloud.push(text)
    await until(lambda: entry.runtime_data.data.areas[0].faults != ())

    result = await async_get_config_entry_diagnostics(hass, entry)

    text = json.dumps(result)
    for secret in SECRETS:
        assert secret not in text
    panel = cloud.sessions[0].panel
    assert panel is not None
    key = panel.session_key
    assert key is not None
    for form in (key.hex(), key.hex().upper(), base64.b64encode(key).decode()):
        assert form not in text
    assert set(result["entry"]["data"].values()) == {REDACTED}
    assert result["entry"]["options"] == {
        **PERSISTENT,
        CONF_ARM_ACCESS: "pin",
        CONF_PIN_HASH: REDACTED,
    }
    assert result["persistent"] is True
    assert result["connection_state"] == "connected"
    assert result["last_update_success"] is True
    snapshot = result["snapshot"]
    assert snapshot["info"]["panel_id"] == REDACTED
    assert snapshot["info"]["site_name"] == REDACTED
    assert snapshot["set_type"] == "LEVEL"
    assert snapshot["dump_complete"] is True
    area = snapshot["areas"]["0"]
    assert area["status"] == "CAN_OVERRIDE"
    assert area["faults"] == ["Landing DT Open"]
    assert area["last_set"] == REDACTED
    assert snapshot["inputs"]["7"]["state"] == "OPEN"
    assert snapshot["user"] == {
        "name": REDACTED,
        "set_areas": [0, 1, 2],
        "unset_areas": [0, 1, 2],
    }
    assert snapshot["faults"] == []
