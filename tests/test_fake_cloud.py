# SPDX-FileCopyrightText: 2026 Paul Fryer
# SPDX-License-Identifier: LGPL-3.0-or-later
from __future__ import annotations

import asyncio
from typing import TYPE_CHECKING

import aiohttp
import pytest

from pyronix_cloud import commands
from pyronix_cloud.cloud import (
    CloudIdentity,
    CloudRequest,
    build_request,
    check_reply,
    is_heartbeat,
    parse_reply,
)
from pyronix_cloud.crypto import EcdhKeyPair, derive_session_key, fixed_key
from pyronix_cloud.errors import CloudError, ConnectionClosedError
from pyronix_cloud.frames import (
    Frame,
    accept_panel_seq,
    build_frame,
    header_seq,
    open_frame,
)
from pyronix_cloud.testing import __main__ as cli
from pyronix_cloud.testing import dump
from pyronix_cloud.testing.fake_cloud import CloudBehaviour, FakeCloud, _ServedEndpoint
from pyronix_cloud.testing.fake_panel import FakePanel, PanelBehaviour
from pyronix_cloud.transport import FrameReassembler, wrap
from pyronix_cloud.websocket import aiohttp_connector

if TYPE_CHECKING:
    from pyronix_cloud.websocket import WebSocket

PASSWORD = "app-password"  # noqa: S105 - the fake panel's test value
WRONG_PASSWORD = "wrong"  # noqa: S105 - fake test value
REQUESTS = [
    CloudRequest.SUBSCRIPTION,
    CloudRequest.CONN_TO_CLOUD,
    CloudRequest.CONN_TO_PANEL,
]


def identity(password: str = PASSWORD) -> CloudIdentity:
    return CloudIdentity(
        app_user="tester", panel_id=dump.PANEL_ID, app_password=password, device_id="d1"
    )


class Client:
    """A minimal app over one WebSocket, written from PROTOCOL.md §3-§4."""

    def __init__(self, ws: WebSocket, password: str = PASSWORD) -> None:
        self.ws = ws
        self.identity = identity(password)
        self.fixed = fixed_key(password, dump.PANEL_ID)
        self.pair = EcdhKeyPair.generate()
        self.seq = self.rx = 0
        self.key: bytes | None = None
        self.messages: list[str] = []
        self._reassembler = FrameReassembler()
        self._pending: list[bytes] = []

    async def message(self) -> str | bytes | None:
        return await asyncio.wait_for(self.ws.receive(), 2)

    async def ask(self, kind: CloudRequest) -> str:
        await self.ws.send_text(build_request(self.identity, kind))
        reply = await self.message()
        assert isinstance(reply, str)
        return reply

    async def send(self, frame_type: str, payload: bytes = b"") -> None:
        self.seq += 1
        await self.ws.send_text(
            wrap(build_frame(frame_type, self.seq, payload, self.key))
        )

    async def frames(self, count: int) -> list[Frame]:
        while len(self._pending) < count:
            message = await self.message()
            assert isinstance(message, str)
            self.messages.append(message)
            if not is_heartbeat(message):
                self._pending += self._reassembler.feed(message.encode())
        raws, self._pending = self._pending[:count], self._pending[count:]
        return [self._open(raw) for raw in raws]

    async def texts(self, count: int) -> list[str]:
        opened = await self.frames(count)
        assert {f.type for f in opened} == {"d"}
        return [f.payload.decode() for f in opened]

    def _open(self, raw: bytes) -> Frame:
        full = accept_panel_seq(self.rx, header_seq(raw))
        assert full is not None
        self.rx = full
        return open_frame(raw, self.key, full)

    async def connect(self) -> None:
        assert await self.ask(CloudRequest.SUBSCRIPTION) == "<e>j </e>"
        assert await self.ask(CloudRequest.CONN_TO_CLOUD) == "<e>u</e>"
        await self.ws.send_text(
            build_request(self.identity, CloudRequest.CONN_TO_PANEL)
        )

    async def handshake(self) -> None:
        await self.connect()
        await self.send("A")
        (announce,) = await self.frames(1)
        extra = announce.payload[10:11]
        await self.send("B", b"\x01" + self.pair.public_bytes)
        (key,) = await self.frames(1)
        shared = self.pair.shared_secret(key.payload[1:])
        self.key = derive_session_key(self.fixed, shared, extra)

    async def login(self, code: str = "1234") -> list[str]:
        await self.handshake()
        await self.send("D", commands.login(code))
        return await self.texts(len(dump.login_dump()))


async def client(cloud: FakeCloud, password: str = PASSWORD) -> Client:
    return Client(await cloud.connector()("wss://fake"), password)


@pytest.mark.parametrize(
    "behaviour",
    [
        CloudBehaviour(),
        CloudBehaviour(chunk_size=2, batch_frames=False),
        CloudBehaviour(chunk_size=None, batch_frames=False, leading_space=False),
        CloudBehaviour(chunk_size=None, batch_frames=True),
    ],
)
async def test_login_dump_through_the_relay(behaviour: CloudBehaviour) -> None:
    async with FakeCloud(behaviour) as cloud:
        app = await client(cloud)
        assert await app.login() == dump.login_dump()
        first = app.messages[0]
        assert first.startswith(" (") is behaviour.leading_space
        size = behaviour.chunk_size
        assert all(len(m) <= (size or len(m)) for m in app.messages)
        if size is None and not behaviour.batch_frames:
            assert all(m.strip().count("(") == 1 for m in app.messages)
        (session,) = cloud.sessions
        assert session.requests == REQUESTS
        assert session.url == "wss://fake"
        assert session.panel is not None
        assert session.panel.logged_in


async def test_single_remote_session() -> None:
    async with FakeCloud() as cloud:
        first, second = await client(cloud), await client(cloud)
        await first.handshake()
        assert await second.ask(CloudRequest.SUBSCRIPTION) == "<e>j </e>"
        busy = await second.ask(CloudRequest.CONN_TO_CLOUD)
        assert busy == "<e>b </e>"
        with pytest.raises(CloudError):
            check_reply(parse_reply(busy))  # type: ignore[arg-type]  # parsed above
        await first.ws.close()
        assert await second.ask(CloudRequest.CONN_TO_CLOUD) == "<e>u</e>"


@pytest.mark.parametrize("letter", ["a", "b", "i", "n", "q"])
@pytest.mark.parametrize("stage", ["subscription", "connect"])
async def test_error_letters(letter: str, stage: str) -> None:
    behaviour = CloudBehaviour(error_letter=letter, error_stage=stage)  # type: ignore[arg-type]  # parametrised literal
    async with FakeCloud(behaviour) as cloud:
        app = await client(cloud)
        reply = await app.ask(CloudRequest.SUBSCRIPTION)
        if stage == "connect":
            assert reply == "<e>j </e>"
            reply = await app.ask(CloudRequest.CONN_TO_CLOUD)
        assert reply == f"<e>{letter} </e>"
        parsed = parse_reply(reply)
        assert parsed is not None
        with pytest.raises(CloudError):
            check_reply(parsed)
        assert not app.ws.closed


async def test_reject_password() -> None:
    async with FakeCloud(CloudBehaviour(reject_password=True)) as cloud:
        wrong = await client(cloud, WRONG_PASSWORD)
        await wrong.ask(CloudRequest.SUBSCRIPTION)
        assert await wrong.ask(CloudRequest.CONN_TO_CLOUD) == "<e>a </e>"
        right = await client(cloud)
        assert await right.login() == dump.login_dump()


async def test_wrong_password_is_accepted_by_default_and_login_nacked() -> None:
    async with FakeCloud() as cloud:
        app = await client(cloud, WRONG_PASSWORD)
        await app.handshake()
        await app.send("D", commands.login("1234"))
        (nack,) = await app.frames(1)
        assert nack.type == "c"


async def test_wrong_user_code_gets_goodbye() -> None:
    async with FakeCloud() as cloud:
        app = await client(cloud)
        await app.handshake()
        await app.send("D", commands.login("0000"))
        (bye,) = await app.frames(1)
        assert bye.type == "e"


async def test_commands_pushes_and_heartbeats() -> None:
    async with FakeCloud() as cloud:
        app = await client(cloud)
        await app.login()
        await app.send("D", commands.set_areas([0]))
        assert len(await app.texts(4)) == 4
        await app.send("D", commands.unset_areas([0]))
        assert len(await app.texts(2)) == 2
        await app.send("D", commands.request_log(3))
        assert '"type":"log"' in (await app.texts(1))[0]
        await cloud.push(dump.input_update(1, open_=True))
        assert await app.texts(1) == [dump.input_update(1, open_=True)]
        await cloud.heartbeat()
        assert await app.message() == " "
        await app.send("D", commands.heartbeat())
        assert await app.texts(1) == [" "]
        panel = cloud.sessions[0].panel
        assert panel is not None
        assert panel.received_commands[0] == commands.set_areas([0])
        await app.send("E")
        assert panel.goodbye_received
        await app.ws.close()
        assert cloud.sessions[0].closed_by_client


async def test_area_state_survives_sessions() -> None:
    async with FakeCloud(CloudBehaviour(chunk_size=None)) as cloud:
        app = await client(cloud)
        await app.login()
        await app.send("D", commands.set_areas([1]))
        await app.texts(4)
        await app.ws.close()
        later = await client(cloud)
        assert '"V":1' in (await later.login())[2]


async def test_pushes_skip_sessions_not_logged_in() -> None:
    panels: list[FakePanel] = []

    def factory() -> FakePanel:
        panels.append(FakePanel(PanelBehaviour(user_code="42")))
        return panels[-1]

    async with FakeCloud(panel_factory=factory) as cloud:
        idle = await client(cloud)
        await cloud.push("{}")
        await cloud.heartbeat()
        app = await client(cloud)
        await app.login("42")
        await cloud.push("{}")
        assert await app.texts(1) == ["{}"]
        await idle.ws.send_text(build_request(idle.identity, CloudRequest.REQ_PWD))
        assert await idle.message() == "<e>j </e>"
        assert len(panels) == 1


async def test_drop_closes_from_the_server() -> None:
    cloud = FakeCloud()
    app = await client(cloud)
    await app.login()
    await cloud.drop()
    assert await app.message() is None
    assert app.ws.closed
    with pytest.raises(ConnectionClosedError):
        await app.ws.send_text("(x)")
    assert not cloud.sessions[0].closed_by_client
    await cloud.stop()


@pytest.mark.usefixtures("socket_enabled")
async def test_served_over_loopback() -> None:
    async with FakeCloud() as cloud, aiohttp.ClientSession() as http:
        url = await cloud.serve()
        assert url.startswith("ws://127.0.0.1:")
        with pytest.raises(RuntimeError, match="already serving"):
            await cloud.serve()
        app = Client(await aiohttp_connector(http, timeout=5)(url))
        assert await app.login() == dump.login_dump()
        await cloud.push("{}")
        assert await app.texts(1) == ["{}"]
        await cloud.stop()
        assert await app.message() is None
        assert not cloud.sessions[0].closed_by_client
        url = await cloud.serve()
        quitter = Client(await aiohttp_connector(http, timeout=5)(url))
        await quitter.connect()
        await quitter.ws.close()
        async with http.ws_connect(url) as raw:
            await raw.send_bytes(b"ignored")
            await raw.send_str(build_request(identity(), CloudRequest.SUBSCRIPTION))
            assert await raw.receive_str() == "<e>j </e>"
    assert cloud.sessions[1].closed_by_client


async def test_served_endpoint_stops_sending_once_closed() -> None:
    class ClosedSocket:
        closed = True

    session = FakeCloud()._new_session("u")
    endpoint = _ServedEndpoint(ClosedSocket(), session)  # type: ignore[arg-type]  # stub
    await endpoint.send_all(["never sent"])


class Stdout:
    """Captures what the CLI prints and tells when it has printed."""

    def __init__(self) -> None:
        self.text = ""
        self.printed = asyncio.Event()

    def write(self, text: str) -> None:
        self.text += text
        self.printed.set()

    def flush(self) -> None:
        pass


@pytest.mark.usefixtures("socket_enabled")
async def test_cli_run_prints_url_and_fake_credentials(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    stdout = Stdout()
    monkeypatch.setattr("sys.stdout", stdout)
    stop = asyncio.Event()
    task = asyncio.create_task(cli.run("127.0.0.1", 0, stop))
    try:
        await asyncio.wait_for(stdout.printed.wait(), 5)
        url = stdout.text.split()[5]
        async with aiohttp.ClientSession() as http:
            app = Client(await aiohttp_connector(http, timeout=5)(url))
            assert await app.ask(CloudRequest.SUBSCRIPTION) == "<e>j </e>"
            stop.set()
            await task
            assert await app.message() is None
    finally:
        stop.set()
        await task
    assert url.startswith("ws://127.0.0.1:")
    assert "TESTPANL" in stdout.text
    assert PASSWORD in stdout.text


@pytest.mark.usefixtures("socket_enabled")
def test_cli_main_stops_when_told(capsys: pytest.CaptureFixture[str]) -> None:
    stop = asyncio.Event()
    stop.set()
    assert cli.main(["--port", "0"], stop) == 0
    assert "System ID:    TESTPANL" in capsys.readouterr().out
