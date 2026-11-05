# SPDX-FileCopyrightText: 2026 Paul Fryer
# SPDX-License-Identifier: LGPL-3.0-or-later
from __future__ import annotations

import json
from typing import Any

import pytest

from pyronix_cloud import commands
from pyronix_cloud.crypto import EcdhKeyPair, derive_session_key, fixed_key
from pyronix_cloud.documents import parse_document
from pyronix_cloud.frames import (
    Frame,
    accept_panel_seq,
    build_frame,
    frame_type_of,
    header_seq,
    open_frame,
)
from pyronix_cloud.models import AreaDocument, AreaStatus, LogDocument
from pyronix_cloud.testing import dump
from pyronix_cloud.testing.fake_panel import FakePanel, PanelBehaviour

PASSWORD = "app-password"  # noqa: S105 - the fake panel's test value
WRONG_PASSWORD = "wrong"  # noqa: S105 - fake test value


class App:
    """The app side of the panel protocol, written from PROTOCOL.md §4."""

    def __init__(self, password: str = PASSWORD) -> None:
        self.fixed = fixed_key(password, dump.PANEL_ID)
        self.pair = EcdhKeyPair.generate()
        self.seq = 0
        self.rx = 0
        self.key: bytes | None = None
        self.extra = b""

    def frame(self, frame_type: str, payload: bytes = b"") -> bytes:
        self.seq += 1
        return build_frame(frame_type, self.seq, payload, self.key)

    def open(self, raw: bytes) -> Frame:
        full = accept_panel_seq(self.rx, header_seq(raw))
        assert full is not None
        frame = open_frame(raw, self.key, full)
        self.rx = full
        return frame

    def handshake(self, panel: FakePanel) -> None:
        (announce,) = panel.receive_frame(self.frame("A"))
        payload = self.open(announce).payload
        assert payload[2:10] == dump.PANEL_ID.encode()
        extra_n = len(announce) - 42
        if payload[1] & 0x80 and extra_n > 0:
            self.extra = payload[10 : 10 + min(extra_n, 4)]
        (key,) = panel.receive_frame(self.frame("B", b"\x01" + self.pair.public_bytes))
        shared = self.pair.shared_secret(self.open(key).payload[1:])
        self.key = derive_session_key(self.fixed, shared, self.extra)

    def texts(self, frames: list[bytes]) -> list[str]:
        opened = [self.open(raw) for raw in frames]
        assert {f.type for f in opened} <= {"d"}
        return [f.payload.decode() for f in opened]

    def login(self, panel: FakePanel, code: str = "1234") -> list[str]:
        self.handshake(panel)
        return self.texts(panel.receive_frame(self.frame("D", commands.login(code))))


def _areas(texts: list[str]) -> list[tuple[int, AreaStatus]]:
    docs = [parse_document(t) for t in texts]
    assert all(isinstance(d, AreaDocument) and not d.is_new for d in docs)
    return [(a.number, a.status) for d in docs for a in d.areas]  # type: ignore[union-attr]  # asserted above


def test_login_dump_arrives_in_order() -> None:
    panel, app = FakePanel(), App()
    assert app.login(panel) == dump.login_dump()
    assert panel.logged_in
    assert panel.received_commands == []
    assert app.extra == b"\x01"


@pytest.mark.parametrize(
    ("flags", "extra", "compressed", "expected_extra"),
    [
        (0x83, b"", False, b""),
        (0x83, b"\x01\x02\x03\x04", True, b"\x01\x02\x03\x04"),
        (0x03, b"\x07", True, b""),
    ],
)
def test_handshake_variants(
    flags: int, extra: bytes, *, compressed: bool, expected_extra: bytes
) -> None:
    behaviour = PanelBehaviour(
        modem_flags=flags, announce_extra=extra, compressed_key=compressed
    )
    panel, app = FakePanel(behaviour), App()
    assert app.login(panel) == dump.login_dump()
    assert app.extra == expected_extra


def test_wrong_user_code_gets_encrypted_goodbye() -> None:
    panel, app = FakePanel(), App()
    app.handshake(panel)
    (bye,) = panel.receive_frame(app.frame("D", commands.login("9999")))
    assert app.open(bye).type == "e"
    assert not panel.logged_in
    assert panel.closed
    assert panel.receive_frame(app.frame("D", commands.heartbeat())) == []


def test_wrong_app_password_login_is_nacked() -> None:
    panel, app = FakePanel(), App(WRONG_PASSWORD)
    app.handshake(panel)
    login = app.frame("D", commands.login("1234"))
    (nack,) = panel.receive_frame(login)
    assert frame_type_of(nack) == "c"
    assert app.open(nack).payload == login[:5]
    assert not panel.logged_in


def test_dump_is_undecryptable_with_the_wrong_password() -> None:
    panel, good, bad = FakePanel(), App(), App(WRONG_PASSWORD)
    good.handshake(panel)
    bad.key = derive_session_key(bad.fixed, b"\x00" * 32, b"")
    (frame, *_) = panel.receive_frame(good.frame("D", commands.login("1234")))
    with pytest.raises(Exception, match="check mismatch"):
        open_frame(frame, bad.key, header_seq(frame))


def test_heartbeat_gets_panel_heartbeat() -> None:
    panel, app = FakePanel(), App()
    app.login(panel)
    reply = panel.receive_frame(app.frame("D", commands.heartbeat()))
    assert app.texts(reply) == [" "]
    assert panel.received_commands == [commands.heartbeat()]


def test_set_pushes_setting_then_set_twice_each() -> None:
    areas: dict[int, int] = {}
    panel, app = FakePanel(areas=areas), App()
    app.login(panel)
    replies = panel.receive_frame(app.frame("D", commands.set_areas([0, 1])))
    assert _areas(app.texts(replies)) == [
        (0, AreaStatus.SETTING),
        (0, AreaStatus.SETTING),
        (1, AreaStatus.SETTING),
        (1, AreaStatus.SETTING),
        (0, AreaStatus.SET),
        (0, AreaStatus.SET),
        (1, AreaStatus.SET),
        (1, AreaStatus.SET),
    ]
    assert panel.areas == areas == {0: 1, 1: 1}
    # A later session on the same state sees the areas set in its dump.
    later, app2 = FakePanel(areas=areas), App()
    page = parse_document(app2.login(later)[2])
    assert isinstance(page, AreaDocument)
    assert [a.status for a in page.areas] == [AreaStatus.SET, AreaStatus.SET]


def test_unset_pushes_unset_and_skips_unknown_areas() -> None:
    panel, app = FakePanel(PanelBehaviour(repeat=1)), App()
    app.login(panel)
    replies = panel.receive_frame(app.frame("D", commands.unset_areas([2, 9])))
    assert _areas(app.texts(replies)) == [(2, AreaStatus.UNSET)]
    assert panel.areas == {2: 0}


def test_set_can_be_refused() -> None:
    behaviour = PanelBehaviour(set_statuses=(5,), repeat=1)
    panel, app = FakePanel(behaviour), App()
    app.login(panel)
    replies = panel.receive_frame(app.frame("D", commands.set_areas([0], force=False)))
    (text,) = app.texts(replies)
    assert _areas([text]) == [(0, AreaStatus.CAN_OVERRIDE)]
    assert json.loads(text)["Detail"][0]["S"] == "Set Delayed"
    assert panel.areas == {0: 5}


def test_ignored_commands_are_recorded() -> None:
    panel, app = FakePanel(PanelBehaviour(confirm_commands=False)), App()
    app.login(panel)
    for payload in (commands.set_areas([0]), b"\x31\x01\x01", b""):
        assert panel.receive_frame(app.frame("D", payload)) == []
    assert panel.received_commands == [commands.set_areas([0]), b"\x31\x01\x01", b""]
    assert panel.areas == {}


@pytest.mark.parametrize(
    ("payload", "count"), [(commands.request_log(200), 200), (b"\x35", 10)]
)
def test_log_request(payload: bytes, count: int) -> None:
    panel, app = FakePanel(), App()
    app.login(panel)
    (text,) = app.texts(panel.receive_frame(app.frame("D", payload)))
    doc = parse_document(text)
    assert isinstance(doc, LogDocument)
    assert len(doc.entries) == count


def test_push_only_after_login() -> None:
    panel, app = FakePanel(), App()
    assert panel.push("{}") == []
    app.login(panel)
    sequence = dump.door_open_sequence() + dump.door_close_sequence()
    pushes = [f for text in sequence for f in panel.push(text)]
    assert app.texts(pushes) == sequence


@pytest.mark.parametrize("bye", ["E", "F"])
def test_goodbye_is_recorded(bye: str) -> None:
    panel, app = FakePanel(), App()
    app.login(panel)
    assert panel.receive_frame(app.frame(bye)) == []
    assert panel.goodbye_received
    assert panel.closed


def test_nack_resends_last_frame() -> None:
    panel, app = FakePanel(), App()
    assert panel.receive_frame(app.frame("C", bytes(5))) == []
    (announce,) = panel.receive_frame(app.frame("A"))
    assert panel.receive_frame(app.frame("C", announce[:5])) == [announce]


def test_bad_frames_are_nacked_without_moving_on() -> None:
    panel, app = FakePanel(), App()
    good = app.frame("A")
    tampered = good[:-1] + bytes([good[-1] ^ 1])
    for bad, header in [
        (tampered, good[:5]),
        (b"\x41\x01", b"\x41\x01\x00\x00\x00"),
        (build_frame("A", 500, b"", None), build_frame("A", 500, b"", None)[:5]),
        (
            build_frame("D", 1, b"x", bytes(16)),
            build_frame("D", 1, b"x", bytes(16))[:5],
        ),
        (build_frame("B", 1, b"", None), build_frame("B", 1, b"", None)[:5]),
    ]:
        (nack,) = panel.receive_frame(bad)
        assert frame_type_of(nack) == "c"
        assert app.open(nack).payload == header
    # The client may still send its announce with the seq it first used.
    assert frame_type_of(panel.receive_frame(good)[0]) == "a"


def test_bad_public_key_ends_with_plain_goodbye() -> None:
    panel, app = FakePanel(), App()
    app.open(panel.receive_frame(app.frame("A"))[0])
    (bye,) = panel.receive_frame(app.frame("B", b"\x01" + bytes(65)))
    assert app.open(bye).type == "f"
    assert panel.closed


def test_custom_documents_pass_through() -> None:
    docs = ["not json", "[1]", '{"type":"area","Detail":5}', json.dumps({"type": "x"})]
    panel, app = FakePanel(PanelBehaviour(documents=docs)), App()
    assert app.login(panel) == docs


@pytest.mark.parametrize(
    "kwargs",
    [{"panel_id": "SHORT"}, {"announce_extra": bytes(5)}, {"repeat": 0}],
)
def test_behaviour_validation(kwargs: dict[str, Any]) -> None:
    with pytest.raises(ValueError, match=r"must|at most"):
        PanelBehaviour(**kwargs)


def test_behaviour_repr_hides_secrets() -> None:
    text = repr(PanelBehaviour())
    assert PASSWORD not in text
    assert "1234" not in text
