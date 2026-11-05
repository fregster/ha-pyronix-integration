# SPDX-FileCopyrightText: 2026 Paul Fryer
# SPDX-License-Identifier: LGPL-3.0-or-later
"""PyronixClient sends the caller's app profile, or the app's defaults, to the cloud."""

from __future__ import annotations

import base64
import binascii
from typing import TYPE_CHECKING

from pyronix_cloud import AppProfile
from pyronix_cloud.client import Credentials, PyronixClient, Timeouts
from pyronix_cloud.cloud import CloudRequest
from pyronix_cloud.testing import dump
from pyronix_cloud.testing.fake_cloud import FakeCloud

if TYPE_CHECKING:
    from pyronix_cloud.websocket import Connector, WebSocket

CREDENTIALS = Credentials(
    app_user="tester",
    panel_id=dump.PANEL_ID,
    app_password="app-password",  # noqa: S106 - the fake panel's test value
    user_code="1234",
    device_id="device-1",
)
FAST = Timeouts(connect=1, step=1, dump=1, command=0.05, close=1)
CUSTOM = AppProfile(
    app_version="7.00.002",
    app_type=17,
    os_name="iOS",
    os_version="18.1",
    network_type="Mobile",
    app_lang="de",
)
CUSTOM_TAIL = (
    "<l>7.00.002</l><t>17</t><m>iOS</m><v>18.1</v><c>Mobile</c><lang>de</lang></p>"
)
DEFAULT_TAIL = (
    "<l>6.03.001</l><t>16</t><m>Android</m><v>14</v><c>Wifi</c><lang>uk</lang></p>"
)


def recording(connector: Connector, sent: list[str]) -> Connector:
    """Wrap ``connector`` so every text the client sends lands in ``sent``."""

    async def connect(url: str) -> WebSocket:
        socket = await connector(url)
        send = socket.send_text

        async def send_text(data: str) -> None:
            sent.append(data)
            await send(data)

        socket.send_text = send_text  # type: ignore[method-assign]  # test spy
        return socket

    return connect


def cloud_requests(sent: list[str]) -> list[str]:
    """Return the ``<p>`` XML of each cloud request among the sent texts."""
    found = []
    for text in sent:
        body = text.strip()[1:-1]
        if not body.startswith("<p>"):
            try:
                body = base64.b64decode(body.replace("\n", "")).decode("latin-1")
            except binascii.Error:
                continue
        if body.startswith("<p>"):
            found.append(body)
    return found


async def requests_sent(profile: AppProfile | None) -> list[str]:
    """Connect and close once; return each cloud request's XML, checked by kind."""
    sent: list[str] = []
    async with FakeCloud() as fake:
        client = PyronixClient(
            CREDENTIALS,
            recording(fake.connector(), sent),
            url="memory://",
            timeouts=FAST,
            app_profile=profile,
        )
        await client.connect()
        await client.close()
        kinds = fake.sessions[0].requests
    xml = cloud_requests(sent)
    assert kinds == [
        CloudRequest.SUBSCRIPTION,
        CloudRequest.CONN_TO_CLOUD,
        CloudRequest.CONN_TO_PANEL,
    ]
    assert len(xml) == len(kinds)
    return xml


async def test_client_sends_the_given_profile_in_every_request() -> None:
    xml = await requests_sent(CUSTOM)
    assert all(request.endswith(CUSTOM_TAIL) for request in xml)


async def test_client_defaults_to_the_app_profile() -> None:
    xml = await requests_sent(None)
    assert all(request.endswith(DEFAULT_TAIL) for request in xml)
