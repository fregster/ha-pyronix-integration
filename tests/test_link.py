# SPDX-FileCopyrightText: 2026 Paul Fryer
# SPDX-License-Identifier: LGPL-3.0-or-later
"""Tests for pyronix_cloud.link: handshake, data mode, NACKs, seqs and goodbye.

The panel side is :class:`pyronix_cloud.testing.fake_panel.FakePanel`. Frames the
fake never sends by itself (stray announces, NACKs, photos, bad keys, seq jumps)
come from :func:`forge`, which builds them on the fake's own seq and key.
"""

from __future__ import annotations

from dataclasses import replace
import logging
from typing import TYPE_CHECKING

from cryptography.hazmat.primitives.asymmetric import ec
from hypothesis import given
from hypothesis import strategies as st
import pytest

from pyronix_cloud.announce import Announced, ModemType
from pyronix_cloud.commands import login
from pyronix_cloud.crypto import EcdhKeyPair
from pyronix_cloud.errors import (
    AuthenticationError,
    HandshakeError,
    NotConnectedError,
    ProtocolError,
    PyronixError,
)
from pyronix_cloud.frames import (
    Frame,
    build_frame,
    frame_type_of,
    header_seq,
    open_frame,
)
from pyronix_cloud.link import (
    KeyAgreed,
    LinkState,
    PanelData,
    PanelGoodbye,
    PanelLink,
    PanelPhoto,
)
from pyronix_cloud.testing.dump import PANEL_ID, login_dump
from pyronix_cloud.testing.fake_panel import FakePanel, PanelBehaviour

if TYPE_CHECKING:
    from pyronix_cloud.link import LinkEvent

PASSWORD = "app-secret-pw"  # noqa: S105 - fake test value, not a real credential
OTHER_PASSWORD = "another-pw"  # noqa: S105 - fake test value
USER_CODE = "73519"
APP_PRIVATE = 0x5EED_0001
BEHAVIOUR = PanelBehaviour(app_password=PASSWORD, user_code=USER_CODE)


def app_keypair() -> EcdhKeyPair:
    return EcdhKeyPair(ec.derive_private_key(APP_PRIVATE, ec.SECP256R1()))


def new_link() -> PanelLink:
    return PanelLink(PANEL_ID, PASSWORD, keypair_factory=app_keypair)


def new_panel(behaviour: PanelBehaviour = BEHAVIOUR) -> FakePanel:
    return FakePanel(behaviour)


def forge(panel: FakePanel, frame_type: str, payload: bytes = b"") -> bytes:
    """Build the fake panel's next frame with any type and payload.

    The fake has no public way to misbehave, so this reaches into its private
    ``_send`` (next seq, session key once agreed).
    """
    return panel._send(frame_type, payload)


def handshake(link: PanelLink, panel: FakePanel) -> tuple[list[LinkEvent], bytes]:
    """Run the handshake; return the link's events and its ``B`` frame."""
    (announce,) = panel.receive_frame(link.start())
    (b_frame,), events = link.receive(announce)
    (key,) = panel.receive_frame(b_frame)
    out, more = link.receive(key)
    assert out == []
    return events + more, b_frame


def in_data_mode() -> tuple[PanelLink, FakePanel]:
    link, panel = new_link(), new_panel()
    handshake(link, panel)
    return link, panel


def corrupt(raw: bytes) -> bytes:
    return raw[:-1] + bytes([raw[-1] ^ 1])


def is_nack(raw: bytes, rejected: bytes) -> bool:
    frame = open_frame(raw, None, header_seq(raw))
    return frame.type == "C" and frame.payload == rejected[:5].ljust(5, b"\0")


# --------------------------------------------------------------------------
# Handshake
# --------------------------------------------------------------------------


def test_start_sends_announce_once() -> None:
    assert new_link().state is LinkState.IDLE
    link = new_link()
    assert link.start() == build_frame("A", 1, b"", None)
    assert link.state is LinkState.AWAIT_ANNOUNCE
    with pytest.raises(RuntimeError):
        link.start()


@pytest.mark.parametrize("compressed", [False, True])
@pytest.mark.parametrize(
    ("flags", "extra", "modem", "version"),
    [
        (0x83, b"", ModemType.WAN_LAN, 0),
        (0x83, b"\x02\x05", ModemType.WAN_LAN, 2),
        (0x84, b"\x01\x02\x03\x04", ModemType.GSM, 1),
        (0x01, b"\x09\x09", ModemType.GSM, 0),
    ],
)
def test_handshake_and_login(
    *, compressed: bool, flags: int, extra: bytes, modem: ModemType, version: int
) -> None:
    link = new_link()
    behaviour = replace(
        BEHAVIOUR, modem_flags=flags, announce_extra=extra, compressed_key=compressed
    )
    panel = new_panel(behaviour)
    events, b_frame = handshake(link, panel)
    assert events == [Announced(modem, version), KeyAgreed()]
    assert link.state is LinkState.DATA
    assert (frame_type_of(b_frame), header_seq(b_frame)) == ("B", 2)
    assert open_frame(b_frame, None, 2).payload == b"\x01" + app_keypair().public_bytes
    login_frame = link.send_data(login(USER_CODE))
    assert (frame_type_of(login_frame), header_seq(login_frame)) == ("D", 3)
    replies = panel.receive_frame(login_frame)
    assert panel.logged_in
    events = [ev for raw in replies for ev in link.receive(raw)[1]]
    assert events == [PanelData(doc.encode()) for doc in login_dump()]


def test_which_key_goes_into_b() -> None:
    link = PanelLink(PANEL_ID, PASSWORD, which_key=2, keypair_factory=app_keypair)
    _, b_frame = handshake(link, new_panel())
    assert open_frame(b_frame, None, 2).payload[0] == 2


@pytest.mark.parametrize(
    "raw",
    [
        b"",
        b"a\x01\x00",
        build_frame("a", 0, b"x" * 10, None),
        build_frame("a", 200, b"x" * 10, None),
        corrupt(build_frame("a", 1, b"x" * 10, None)),
    ],
)
def test_bad_frames_before_announce_are_ignored(raw: bytes) -> None:
    link, panel = new_link(), new_panel()
    a_frame = link.start()
    assert link.receive(raw) == ([], [])
    assert link.state is LinkState.AWAIT_ANNOUNCE
    (announce,) = panel.receive_frame(a_frame)
    out, events = link.receive(announce)
    assert events
    assert out


def test_unexpected_frame_before_announce_is_nacked_then_fails() -> None:
    link, panel = new_link(), new_panel()
    link.start()
    for seq in (2, 3, 4):
        raw = forge(panel, "b", b"\x01")
        (nack,) = link.receive(raw)[0]
        assert is_nack(nack, raw)
        assert header_seq(nack) == seq
    with pytest.raises(HandshakeError):
        link.receive(forge(panel, "b", b"\x01"))


@pytest.mark.parametrize("payload", [b"", b"\x07", b"\x07\x83OTHERPNL"])
def test_wrong_panel_id_is_nacked_then_fails(
    payload: bytes, caplog: pytest.LogCaptureFixture
) -> None:
    link, panel = new_link(), new_panel()
    link.start()
    for _ in range(3):
        raw = forge(panel, "a", payload)
        (nack,) = link.receive(raw)[0]
        assert is_nack(nack, raw)
    with pytest.raises(HandshakeError):
        link.receive(forge(panel, "a", payload))
    assert "OTHERPNL" not in caplog.text
    assert PANEL_ID not in caplog.text


def test_panel_goodbye_during_announce_fails() -> None:
    link, panel = new_link(), new_panel()
    link.start()
    with pytest.raises(HandshakeError):
        link.receive(forge(panel, "f"))
    assert link.state is LinkState.CLOSED


def test_panel_goodbye_during_key_exchange_fails() -> None:
    link, panel = new_link(), new_panel()
    (announce,) = panel.receive_frame(link.start())
    link.receive(announce)
    with pytest.raises(HandshakeError):
        link.receive(forge(panel, "f"))
    assert link.state is LinkState.CLOSED
    assert link.receive(forge(panel, "b")) == ([], [])


def test_panel_nack_resends_announce() -> None:
    link, panel = new_link(), new_panel()
    link.start()
    for seq in (2, 3, 4):
        (again,) = link.receive(forge(panel, "c", b"A\x01\x00\x00\x00"))[0]
        assert again == build_frame("A", seq, b"", None)
    with pytest.raises(HandshakeError):
        link.receive(forge(panel, "c", b"A\x01\x00\x00\x00"))


def test_panel_nack_resends_key_with_same_key_pair() -> None:
    link, panel = new_link(), new_panel()
    (announce,) = panel.receive_frame(link.start())
    (first_b,) = link.receive(announce)[0]
    for _ in range(3):
        (again,) = link.receive(forge(panel, "c", first_b[:5]))[0]
        assert again[5:71] == first_b[5:71]
        assert header_seq(again) > header_seq(first_b)
    with pytest.raises(HandshakeError):
        link.receive(forge(panel, "c", first_b[:5]))


def test_bad_frames_during_key_exchange_are_nacked_then_fail() -> None:
    link, panel = new_link(), new_panel()
    (announce,) = panel.receive_frame(link.start())
    link.receive(announce)
    for raw in (corrupt(forge(panel, "b")), b"", forge(panel, "a")):
        (nack,) = link.receive(raw)[0]
        assert is_nack(nack, raw)
    with pytest.raises(HandshakeError):
        link.receive(build_frame("d", 9, b"x", bytes(16)))


def test_invalid_panel_key_fails() -> None:
    link, panel = new_link(), new_panel()
    (announce,) = panel.receive_frame(link.start())
    link.receive(announce)
    with pytest.raises(HandshakeError):
        link.receive(forge(panel, "b", b"\x01\x04" + bytes(64)))


# --------------------------------------------------------------------------
# Data mode
# --------------------------------------------------------------------------


def test_data_photo_and_goodbye() -> None:
    link, panel = in_data_mode()
    assert link.receive(forge(panel, "d", b"{}")) == ([], [PanelData(b"{}")])
    assert link.receive(forge(panel, "g", b"jpg")) == ([], [PanelPhoto(b"jpg")])
    assert link.receive(forge(panel, "e")) == ([], [PanelGoodbye("e")])
    assert link.state is LinkState.CLOSED
    assert link.receive(forge(panel, "d", b"{}")) == ([], [])
    assert link.goodbye() is None


def test_plain_goodbye_in_data_mode() -> None:
    link, panel = in_data_mode()
    assert link.receive(forge(panel, "f")) == ([], [PanelGoodbye("f")])


def test_panel_nack_resends_last_frame_unchanged() -> None:
    link, panel = in_data_mode()
    sent = link.send_data(login(USER_CODE))
    link.receive(forge(panel, "d", b" "))
    for _ in range(3):
        assert link.receive(forge(panel, "c", sent[:5])) == ([sent], [])
    with pytest.raises(ProtocolError) as caught:
        link.receive(forge(panel, "c", sent[:5]))
    assert not isinstance(caught.value, AuthenticationError)


def test_panel_nacks_before_any_data_mean_wrong_password() -> None:
    link, panel = in_data_mode()
    link.send_data(login(USER_CODE))
    for _ in range(3):
        link.receive(forge(panel, "c", b"D\x03\x00\x07\x00"))
    with pytest.raises(AuthenticationError, match="app password"):
        link.receive(forge(panel, "c", b"D\x03\x00\x07\x00"))


def test_undecryptable_data_means_wrong_password() -> None:
    link = new_link()
    panel = new_panel(replace(BEHAVIOUR, app_password=OTHER_PASSWORD))
    handshake(link, panel)
    for _ in range(3):
        raw = forge(panel, "d", b"{}")
        (nack,) = link.receive(raw)[0]
        assert is_nack(nack, raw)
    with pytest.raises(AuthenticationError, match="app password"):
        link.receive(forge(panel, "d", b"{}"))


def test_bad_frames_after_good_data_raise_protocol_error() -> None:
    link, panel = in_data_mode()
    link.receive(forge(panel, "d", b"{}"))
    for _ in range(3):
        link.receive(forge(panel, "a"))
    with pytest.raises(ProtocolError) as caught:
        link.receive(forge(panel, "a"))
    assert not isinstance(caught.value, AuthenticationError)


def test_nack_count_resets_after_a_good_frame() -> None:
    link, panel = in_data_mode()
    for _ in range(3):
        link.receive(b"")
    link.receive(forge(panel, "d", b" "))
    for _ in range(3):
        link.receive(b"")
    assert link.state is LinkState.DATA


def test_nack_lets_the_panel_resend_with_the_same_seq() -> None:
    link, panel = in_data_mode()
    good = forge(panel, "d", b"{}")
    (nack,) = link.receive(corrupt(good))[0]
    assert is_nack(nack, good)
    assert link.receive(good) == ([], [PanelData(b"{}")])


def test_seq_rules_in_data_mode() -> None:
    link, panel = in_data_mode()
    first = forge(panel, "d", b"1")
    assert link.receive(first)[1] == [PanelData(b"1")]
    assert is_nack(link.receive(first)[0][0], first)  # replay: seq not ahead
    link.receive(forge(panel, "d", b"2"))  # seq 4 accepted, NACK count reset
    panel._tx_seq += 200
    jump = forge(panel, "d", b"3")
    assert is_nack(link.receive(jump)[0][0], jump)


def test_seq_wraps_at_16_bits() -> None:
    link, panel = in_data_mode()
    link._codec._rx_seq = panel._tx_seq = 0xFFFF
    assert link.receive(forge(panel, "d", b"w")) == ([], [PanelData(b"w")])
    assert link._codec._rx_seq == 0x1_0000


def test_send_data_needs_data_mode() -> None:
    link = new_link()
    with pytest.raises(NotConnectedError):
        link.send_data(b"\x20")
    link.start()
    with pytest.raises(NotConnectedError):
        link.send_data(b"\x20")


# --------------------------------------------------------------------------
# Goodbye
# --------------------------------------------------------------------------


def test_goodbye_when_idle_sends_nothing() -> None:
    link = new_link()
    assert link.goodbye() is None
    assert link.state is LinkState.CLOSED
    assert link.receive(build_frame("a", 1, b"", None)) == ([], [])


def test_goodbye_during_handshake_is_plain() -> None:
    link = new_link()
    link.start()
    assert link.goodbye() == build_frame("F", 2, b"", None)
    link, panel = new_link(), new_panel()
    (announce,) = panel.receive_frame(link.start())
    link.receive(announce)
    assert link.goodbye() == build_frame("F", 3, b"", None)


def test_goodbye_in_data_mode_is_encrypted() -> None:
    link, panel = in_data_mode()
    bye = link.goodbye()
    assert bye is not None
    assert panel.receive_frame(bye) == []
    assert panel.goodbye_received
    assert open_frame(bye, panel._key, 3) == Frame("E", 3, b"")
    assert link.state is LinkState.CLOSED
    assert link.goodbye() is None
    with pytest.raises(NotConnectedError):
        link.send_data(b"\x20")


# --------------------------------------------------------------------------
# Robustness and hygiene
# --------------------------------------------------------------------------

FRAMES = st.builds(
    build_frame,
    st.sampled_from("abcdefgABCxyz"),
    st.integers(0, 300),
    st.binary(max_size=80),
    st.just(bytes(16)),
)


def link_in(state: LinkState) -> PanelLink:
    """Return a link driven by a fake panel to ``state``."""
    link, panel = new_link(), new_panel()
    pending = [] if state is LinkState.IDLE else [link.start()]
    while link.state is not state:
        (reply,) = panel.receive_frame(pending.pop())
        pending = link.receive(reply)[0]
    return link


@pytest.mark.parametrize("state", list(LinkState)[:4])
@given(chunks=st.lists(st.one_of(st.binary(max_size=120), FRAMES), max_size=6))
def test_arbitrary_frames_only_raise_library_errors(
    state: LinkState, chunks: list[bytes]
) -> None:
    link = link_in(state)
    for raw in chunks:
        try:
            link.receive(raw)
        except PyronixError:
            return


def test_logs_hold_no_secrets(caplog: pytest.LogCaptureFixture) -> None:
    caplog.set_level(logging.DEBUG)
    link, panel = in_data_mode()
    for raw in panel.receive_frame(link.send_data(login(USER_CODE))):
        link.receive(raw)
    link.receive(b"")
    assert caplog.records
    assert PASSWORD not in caplog.text
    assert USER_CODE not in caplog.text
    assert panel._key is not None
    assert panel._key.hex() not in caplog.text
