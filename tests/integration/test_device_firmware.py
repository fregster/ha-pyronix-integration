# SPDX-FileCopyrightText: 2026 Paul Fryer
# SPDX-License-Identifier: Apache-2.0
from __future__ import annotations

from dataclasses import replace
import json
from typing import TYPE_CHECKING

from homeassistant.helpers import device_registry as dr
import pytest

from custom_components.pyronix_cloud.const import DOMAIN
from pyronix_cloud.testing import dump
from tests.integration.conftest import PERSISTENT, until

if TYPE_CHECKING:
    from homeassistant.core import HomeAssistant
    from pytest_homeassistant_custom_component.common import MockConfigEntry  # type: ignore[import-untyped]  # the HA test plugin ships no py.typed

    from pyronix_cloud.testing.fake_cloud import FakeCloud


def panel_device(hass: HomeAssistant, entry: MockConfigEntry) -> dr.DeviceEntry | None:
    """Return the panel's device registry entry, if any."""
    devices = dr.async_entries_for_config_entry(dr.async_get(hass), entry.entry_id)
    return next(
        (d for d in devices if (DOMAIN, dump.PANEL_ID) in d.identifiers),
        None,
    )


def sw_version(hass: HomeAssistant, entry: MockConfigEntry) -> str | None:
    """Read the panel device's firmware version from the device registry."""
    device = panel_device(hass, entry)
    assert device is not None
    return device.sw_version


def panel_document(version_lo: int) -> str:
    """Return the login dump's panel document with another minor version."""
    panel = json.loads(dump.login_dump()[0])
    panel["VersionLo"] = version_lo
    return json.dumps(panel)


async def test_device_shows_the_firmware_at_setup(
    hass: HomeAssistant, loaded: MockConfigEntry
) -> None:
    info = loaded.runtime_data.data.info
    assert info is not None
    assert sw_version(hass, loaded) == info.firmware == "10.26"


async def test_new_firmware_updates_the_device(
    hass: HomeAssistant, loaded: MockConfigEntry
) -> None:
    coordinator = loaded.runtime_data
    snapshot = coordinator.data
    assert snapshot.info is not None
    upgraded = replace(snapshot, info=replace(snapshot.info, firmware="10.30"))
    coordinator.async_set_updated_data(upgraded)
    assert sw_version(hass, loaded) == "10.30"


async def test_unchanged_firmware_leaves_the_device_alone(
    hass: HomeAssistant, loaded: MockConfigEntry
) -> None:
    coordinator = loaded.runtime_data
    device = panel_device(hass, loaded)
    assert device is not None
    coordinator.async_set_updated_data(replace(coordinator.data, faults=()))
    assert panel_device(hass, loaded) is device  # an update replaces the entry


async def test_missing_panel_info_is_ignored(
    hass: HomeAssistant, loaded: MockConfigEntry
) -> None:
    coordinator = loaded.runtime_data
    coordinator.async_set_updated_data(replace(coordinator.data, info=None))
    assert sw_version(hass, loaded) == "10.26"


async def test_missing_device_is_ignored(
    hass: HomeAssistant, loaded: MockConfigEntry
) -> None:
    coordinator = loaded.runtime_data
    registry = dr.async_get(hass)
    device = panel_device(hass, loaded)
    assert device is not None
    registry.async_remove_device(device.id)
    snapshot = coordinator.data
    assert snapshot.info is not None
    upgraded = replace(snapshot, info=replace(snapshot.info, firmware="10.30"))
    coordinator.async_set_updated_data(upgraded)
    assert panel_device(hass, loaded) is None


@pytest.mark.parametrize("entry_options", [PERSISTENT])
async def test_live_push_updates_the_device(
    hass: HomeAssistant, cloud: FakeCloud, loaded: MockConfigEntry
) -> None:
    await cloud.push(panel_document(version_lo=31))
    await until(lambda: sw_version(hass, loaded) == "10.31")
    assert loaded.runtime_data.persistent
