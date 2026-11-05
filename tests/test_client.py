# SPDX-FileCopyrightText: 2026 Paul Fryer
# SPDX-License-Identifier: LGPL-3.0-or-later
from __future__ import annotations

import asyncio
from typing import TYPE_CHECKING

import pytest

from pyronix_cloud import commands
from pyronix_cloud.client import Credentials, PyronixClient, Timeouts
from pyronix_cloud.errors import (
    AccessDeniedError,
    AuthenticationError,
    CloudError,
    CommandNotConfirmedError,
    CommunicationError,
    ConnectionClosedError,
    NotConnectedError,
    NotPermittedError,
    PanelBusyError,
    PanelOfflineError,
    PyronixTimeoutError,
    SubscriptionExpiredError,
    UnknownCloudReplyError,
)
from pyronix_cloud.models import AreaStatus, InputState
from pyronix_cloud.not_polling_retry import NO_RETRY
from pyronix_cloud.testing import dump
from pyronix_cloud.testing.fake_cloud import CloudBehaviour, FakeCloud
from pyronix_cloud.testing.fake_panel import FakePanel, PanelBehaviour
from pyronix_cloud.transport import wrap

if TYPE_CHECKING:
    from collections.abc import AsyncIterator, Callable

    from pyronix_cloud.panel_state import PanelSnapshot
    from pyronix_cloud.testing.cloud_session import CloudSession
    from pyronix_cloud.testing.memory_socket import MemoryWebSocket
    from pyronix_cloud.websocket import Connector, WebSocket

CREDENTIALS = Credentials(
    app_user="tester",
    panel_id=dump.PANEL_ID,
    app_password="app-password",  # noqa: S106 - the fake panel's test value
    user_code="1234",
    device_id="device-1",
)
# The fake panel confirms at once, so 1 s only matters on a starved event loop.
FAST = Timeouts(connect=1, step=1, dump=1, command=1, close=1)
NO_CONFIRM_WAIT = Timeouts(connect=1, step=1, dump=1, command=0.05, close=1)


def client_for(
    connector: Connector,
    credentials: Credentials = CREDENTIALS,
    timeouts: Timeouts = FAST,
) -> PyronixClient:
    return PyronixClient(
        credentials, connector, url="memory://", timeouts=timeouts, retry=NO_RETRY
    )


def panels(behaviour: PanelBehaviour) -> Callable[[], FakePanel]:
    return lambda: FakePanel(behaviour)


def is_connected(client: PyronixClient) -> bool:
    return client.connected  # a call, so mypy does not narrow the property


async def until(predicate: Callable[[], bool]) -> None:
    for _ in range(200):
        if predicate():
            return
        await asyncio.sleep(0)
    pytest.fail("condition not reached")


def said_goodbye(session: CloudSession) -> bool:
    return session.panel is not None and session.panel.goodbye_received


@pytest.fixture
async def fake() -> AsyncIterator[FakeCloud]:
    async with FakeCloud() as cloud:
        yield cloud


@pytest.fixture
async def client(fake: FakeCloud) -> AsyncIterator[PyronixClient]:
    client = client_for(fake.connector())
    await client.connect()
    yield client
    await client.close()


async def test_connect_returns_the_full_dump(fake: FakeCloud) -> None:
    client = client_for(fake.connector())
    snapshot = await client.connect()
    assert snapshot.dump_complete
    assert len(snapshot.areas) == 6
    assert len(snapshot.inputs) == 10
    assert snapshot.info is not None
    assert snapshot.info.panel_id == dump.PANEL_ID
    assert client.connected
    assert client.snapshot is snapshot
    assert fake.sessions[0].url == "memory://"
    await client.close()
    assert not is_connected(client)
    assert said_goodbye(fake.sessions[0])
    assert fake.sessions[0].closed_by_client


async def test_listeners_get_each_change_once(
    fake: FakeCloud, client: PyronixClient
) -> None:
    seen: list[PanelSnapshot] = []
    remove = client.add_listener(seen.append)
    for text in dump.door_open_sequence():
        await fake.push(text)
    await until(lambda: len(seen) == 2)
    assert seen[0].inputs[7].state is InputState.OPEN
    assert seen[1].areas[0].status is AreaStatus.CAN_OVERRIDE
    for text in dump.door_close_sequence():
        await fake.push(text)
    await until(lambda: len(seen) == 4)
    assert seen[3].areas[0].status is AreaStatus.UNSET
    remove()
    remove()  # removing twice is harmless
    await fake.push(dump.input_update(7, open_=True))
    await until(lambda: client.snapshot.inputs[7].state is InputState.OPEN)
    assert len(seen) == 4


async def test_a_failing_listener_does_not_end_the_session(
    fake: FakeCloud, client: PyronixClient, caplog: pytest.LogCaptureFixture
) -> None:
    def broken(_snapshot: PanelSnapshot) -> None:
        raise RuntimeError

    client.add_listener(broken)
    await fake.push(dump.input_update(7, open_=True))
    await until(lambda: "snapshot listener" in caplog.text)
    assert client.connected


async def test_arm_then_disarm(fake: FakeCloud, client: PyronixClient) -> None:
    statuses: list[AreaStatus] = []
    client.add_listener(lambda s: statuses.append(s.areas[0].status))
    snapshot = await client.arm([0])
    assert snapshot.areas[0].status in {AreaStatus.SETTING, AreaStatus.SET}
    await until(lambda: client.snapshot.areas[0].status is AreaStatus.SET)
    assert statuses == [AreaStatus.SETTING, AreaStatus.SET]
    panel = fake.sessions[0].panel
    assert panel is not None
    assert panel.received_commands == [commands.set_areas([0])]
    snapshot = await client.disarm([0])
    assert snapshot.areas[0].status is AreaStatus.UNSET
    assert panel.received_commands[-1] == commands.unset_areas([0])


async def test_disarm_of_unset_areas_sends_nothing(
    fake: FakeCloud, client: PyronixClient
) -> None:
    snapshot = await client.disarm([0, 1])
    assert snapshot is client.snapshot
    panel = fake.sessions[0].panel
    assert panel is not None
    assert panel.received_commands == []


async def test_arm_of_a_set_area_does_not_wait() -> None:
    areas = {0: 1}  # area 0 is already set on the panel
    fake = FakeCloud(
        panel_factory=lambda: FakePanel(
            PanelBehaviour(confirm_commands=False), areas=areas
        )
    )
    client = client_for(fake.connector())
    await client.connect()
    snapshot = await client.arm([0])
    assert snapshot.areas[0].status is AreaStatus.SET
    await client.close()
    assert said_goodbye(fake.sessions[0])


async def test_unconfirmed_arm_times_out() -> None:
    fake = FakeCloud(panel_factory=panels(PanelBehaviour(confirm_commands=False)))
    client = client_for(fake.connector(), timeouts=NO_CONFIRM_WAIT)
    await client.connect()
    with pytest.raises(CommandNotConfirmedError):
        await client.arm([0])
    assert client.connected
    await client.close()
    assert said_goodbye(fake.sessions[0])


async def test_area_without_permission(fake: FakeCloud, client: PyronixClient) -> None:
    with pytest.raises(NotPermittedError, match=r"set areas \[3\]"):
        await client.arm([0, 3])
    with pytest.raises(NotPermittedError, match=r"unset areas \[3\]"):
        await client.disarm([3])
    panel = fake.sessions[0].panel
    assert panel is not None
    assert panel.received_commands == []


async def test_invalid_areas(client: PyronixClient) -> None:
    with pytest.raises(ValueError, match="distinct"):
        await client.arm([0, 0])


async def test_commands_before_connect(fake: FakeCloud) -> None:
    client = client_for(fake.connector())
    with pytest.raises(NotConnectedError):
        await client.arm([0])
    with pytest.raises(NotConnectedError):
        await client.disarm([0])
    assert fake.sessions == []


@pytest.mark.parametrize(
    ("letter", "error"),
    [
        ("a", AccessDeniedError),
        ("b", PanelBusyError),
        ("i", SubscriptionExpiredError),
        ("n", PanelOfflineError),
        ("q", UnknownCloudReplyError),
    ],
)
@pytest.mark.parametrize("stage", ["subscription", "connect"])
async def test_cloud_error_letters(
    letter: str, error: type[CloudError], stage: str
) -> None:
    behaviour = CloudBehaviour(error_letter=letter, error_stage=stage)  # type: ignore[arg-type]  # parametrised literal
    fake = FakeCloud(behaviour)
    client = client_for(fake.connector())
    with pytest.raises(error):
        await client.connect()
    assert not client.connected
    assert fake.sessions[0].closed_by_client


async def test_wrong_user_code() -> None:
    fake = FakeCloud(panel_factory=panels(PanelBehaviour(user_code="9999")))
    with pytest.raises(AuthenticationError):
        await client_for(fake.connector()).connect()
    assert fake.sessions[0].closed_by_client


async def test_wrong_app_password() -> None:
    fake = FakeCloud()
    wrong = Credentials(
        app_user="tester",
        panel_id=dump.PANEL_ID,
        app_password="wrong",  # noqa: S106 - fake test value
        user_code="1234",
        device_id="device-1",
    )
    with pytest.raises(AuthenticationError):
        await client_for(fake.connector(), wrong).connect()
    assert fake.sessions[0].closed_by_client


async def test_second_client_gets_busy(fake: FakeCloud, client: PyronixClient) -> None:
    second = client_for(fake.connector())
    with pytest.raises(PanelBusyError):
        await second.connect()
    assert fake.sessions[1].closed_by_client
    assert client.connected
    snapshot = await client.arm([1])
    assert snapshot.areas[1].status in {AreaStatus.SETTING, AreaStatus.SET}
    await client.close()
    assert said_goodbye(fake.sessions[0])


async def test_cloud_heartbeat_is_answered(
    fake: FakeCloud, client: PyronixClient
) -> None:
    panel = fake.sessions[0].panel
    assert panel is not None
    await fake.heartbeat()
    await until(lambda: panel.received_commands == [commands.heartbeat()])
    assert client.connected


async def test_server_drop_calls_the_disconnect_listener(
    fake: FakeCloud, client: PyronixClient
) -> None:
    errors: list[BaseException | None] = []
    client.add_disconnect_listener(errors.append)
    await fake.drop()
    await until(lambda: bool(errors))
    assert isinstance(errors[0], ConnectionClosedError)
    assert not client.connected
    assert fake.sessions[0].ended
    assert not fake.sessions[0].closed_by_client
    with pytest.raises(NotConnectedError):
        await client.arm([0])
    await client.close()


async def test_panel_goodbye_ends_cleanly(caplog: pytest.LogCaptureFixture) -> None:
    fake = FakeCloud()
    sockets: list[MemoryWebSocket] = []
    inner = fake.connector()

    async def connector(url: str) -> WebSocket:
        socket = await inner(url)
        sockets.append(socket)  # type: ignore[arg-type]  # always a MemoryWebSocket
        return socket

    client = client_for(connector)
    await client.connect()
    ended: list[BaseException | None] = []
    remove = client.add_disconnect_listener(ended.append)

    def broken(_error: BaseException | None) -> None:
        raise RuntimeError

    client.add_disconnect_listener(broken)
    panel = fake.sessions[0].panel
    assert panel is not None
    sockets[0].deliver([wrap(panel._send("e", b""))])
    await until(lambda: bool(ended))
    assert ended == [None]
    assert "disconnect listener" in caplog.text
    remove()
    remove()
    assert fake.sessions[0].closed_by_client
    # A new session can be opened after the old one ended.
    await client.connect()
    assert client.connected
    await client.close()
    assert said_goodbye(fake.sessions[1])


async def test_cancel_during_connect_says_goodbye() -> None:
    documents = dump.login_dump()[:-1]  # no user document: the dump never ends
    fake = FakeCloud(panel_factory=panels(PanelBehaviour(documents=documents)))
    client = client_for(fake.connector())
    task = asyncio.create_task(client.connect())
    await until(lambda: bool(fake.sessions) and fake.sessions[0].panel is not None)
    await until(lambda: fake.sessions[0].panel.logged_in)  # type: ignore[union-attr]  # set above
    task.cancel()
    with pytest.raises(asyncio.CancelledError):
        await task
    assert said_goodbye(fake.sessions[0])
    assert not client.connected


async def test_dump_timeout_says_goodbye() -> None:
    documents = dump.login_dump()[:-1]
    fake = FakeCloud(panel_factory=panels(PanelBehaviour(documents=documents)))
    client = PyronixClient(
        CREDENTIALS, fake.connector(), timeouts=Timeouts(dump=0.02, close=1)
    )
    with pytest.raises(PyronixTimeoutError, match="READY"):
        await client.connect()
    assert said_goodbye(fake.sessions[0])


async def test_bad_document_is_skipped(caplog: pytest.LogCaptureFixture) -> None:
    documents = [*dump.login_dump()[:-1], "{not json", dump.login_dump()[-1]]
    fake = FakeCloud(panel_factory=panels(PanelBehaviour(documents=documents)))
    client = client_for(fake.connector())
    snapshot = await client.connect()
    assert snapshot.dump_complete
    assert "did not parse" in caplog.text
    await client.close()
    assert said_goodbye(fake.sessions[0])


async def test_close_is_idempotent(fake: FakeCloud) -> None:
    client = client_for(fake.connector())
    await client.close()  # before connect: nothing to do
    await client.connect()
    await client.close()
    await client.close()
    assert said_goodbye(fake.sessions[0])
    assert len(fake.sessions) == 1


async def test_connect_twice_is_refused(fake: FakeCloud, client: PyronixClient) -> None:
    with pytest.raises(RuntimeError, match="already"):
        await client.connect()
    assert len(fake.sessions) == 1


async def test_close_wakes_a_waiting_command() -> None:
    fake = FakeCloud(panel_factory=panels(PanelBehaviour(confirm_commands=False)))
    client = PyronixClient(
        CREDENTIALS, fake.connector(), timeouts=Timeouts(command=5, close=1)
    )
    await client.connect()
    task = asyncio.create_task(client.arm([0]))
    panel = fake.sessions[0].panel
    assert panel is not None
    await until(lambda: bool(panel.received_commands))
    await client.close()
    with pytest.raises(ConnectionClosedError):
        await task
    assert said_goodbye(fake.sessions[0])


async def test_async_context_manager(fake: FakeCloud) -> None:
    async with client_for(fake.connector()) as client:
        assert client.connected
    assert not is_connected(client)
    assert said_goodbye(fake.sessions[0])


async def test_connector_failure() -> None:
    async def connector(_url: str) -> WebSocket:
        msg = "no route"
        raise CommunicationError(msg)

    client = client_for(connector)
    with pytest.raises(CommunicationError):
        await client.connect()
    assert not client.connected
    await client.close()


async def test_connector_timeout() -> None:
    async def connector(_url: str) -> WebSocket:
        await asyncio.Event().wait()
        raise AssertionError  # pragma: no cover - never reached

    client = PyronixClient(CREDENTIALS, connector, timeouts=Timeouts(connect=0.01))
    with pytest.raises(PyronixTimeoutError, match="opening"):
        await client.connect()


def test_malformed_credentials_are_rejected() -> None:
    bad_code = Credentials(
        app_user="tester",
        panel_id=dump.PANEL_ID,
        app_password="x",  # noqa: S106 - fake test value
        user_code="12ab",
        device_id="device-1",
    )
    with pytest.raises(ValueError, match="digits"):
        PyronixClient(bad_code, FakeCloud().connector())
