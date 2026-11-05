# SPDX-FileCopyrightText: 2026 Paul Fryer
# SPDX-License-Identifier: LGPL-3.0-or-later
"""Stateful tests: the data-mode panel link and frame reassembly on a hostile network.

PROTOCOL.md §3.2, §4.1-§4.3, §4.7. :class:`LinkMachine` drives a
:class:`FrameReassembler` feeding a :class:`PanelLink`, the way
``session.py`` composes them, with in-order, lost, duplicated, reordered, replayed,
forged, corrupted and junk frames, cut into WebSocket messages at random byte
boundaries. :class:`LinkModel` is a reference model written from the spec text; the
machine checks every output frame, event and error of the link against it.
"""

from __future__ import annotations

import base64
import binascii
from collections import deque
from dataclasses import dataclass

from cryptography.hazmat.primitives.asymmetric import ec
from hypothesis import event, given, settings
from hypothesis import strategies as st
from hypothesis.stateful import (
    RuleBasedStateMachine,
    initialize,
    invariant,
    precondition,
    rule,
    run_state_machine_as_test,
)
import pytest

from pyronix_cloud.crypto import EcdhKeyPair
from pyronix_cloud.errors import AuthenticationError, ProtocolError, PyronixError
from pyronix_cloud.frames import (
    ENCRYPTED_TYPES,
    HEADER_LEN,
    MIN_FRAME_LEN,
    accept_panel_seq,
    build_frame,
)
from pyronix_cloud.link import (
    LinkEvent,
    LinkState,
    PanelData,
    PanelGoodbye,
    PanelLink,
    PanelPhoto,
)
from pyronix_cloud.link_nacks import MAX_CONSECUTIVE_NACK
from pyronix_cloud.testing.dump import PANEL_ID
from pyronix_cloud.testing.fake_panel import FakePanel, PanelBehaviour
from pyronix_cloud.transport import FrameReassembler, wrap

PASSWORD = "app-secret-pw"  # noqa: S105 - fake test value, not a real credential
SPAN16 = 0x1_0000
WRAP_GAP = 128  # RX_SEQ_WRAP_GAP, §4.3
# Bytes that can never start a frame (§4.2: the receiver skips to "(" or a-f).
SKIPPABLE = bytes(b for b in range(256) if b != ord("(") and not 0x61 <= b <= 0x66)
B64_ALPHABET = "ABCDEFGHIJKLMNOPQRSTUVWXYZabcdefghijklmnopqrstuvwxyz0123456789+/="
CORRUPTIONS = ("check", "ciphertext", "seq", "length", "type", "truncate", "extend")

type Outcome = tuple[list[bytes], list[LinkEvent], type[PyronixError] | None]


def reference_accept(last: int, seq16: int) -> int | None:
    """§4.3 seq acceptance, written from the spec text (not from frames.py)."""
    last16 = last % SPAN16
    if last == 0:  # first panel frame: 1..127; seq 0 is rejected
        return seq16 if 0 < seq16 < WRAP_GAP else None
    if seq16 < last16:  # backwards only as a 16-bit wrap; only it keeps high bits
        if seq16 < WRAP_GAP and last16 > SPAN16 - WRAP_GAP:
            return ((last // SPAN16 + 1) * SPAN16 + seq16) % 2**32
        return None
    return seq16 if 0 < seq16 - last16 < WRAP_GAP else None


@dataclass(frozen=True, slots=True)
class Sent:
    """A frame the panel built: its bytes, the nonce seq it used and its identity."""

    raw: bytes
    nonce: int
    ident: int
    payload: bytes


@dataclass(frozen=True, slots=True)
class OnWire:
    """A frame on the wire: where it ends in the stream and what must come out."""

    end: int
    raw: bytes
    sent: Sent | None  # None if corrupted or junk


@dataclass(slots=True)
class LinkModel:
    """Reference model of the app side in data mode (§4.3, §4.8)."""

    key: bytes
    last_sent: bytes
    last: int = 2  # after the handshake: panel sent a (1) and b (2)
    tx: int = 2  # app sent A (1) and B (2)
    sent_nacks: int = 0
    recv_nacks: int = 0
    data_verified: bool = False
    closed: bool = False
    rewinds: int = 0

    def expect(self, raw: bytes, sent: Sent | None) -> Outcome:
        """Return what the link must send, report or raise for one frame."""
        if self.closed:
            return [], [], None
        full = None
        if len(raw) >= MIN_FRAME_LEN:
            full = reference_accept(self.last, raw[1] | raw[2] << 8)
        if sent is None or full is None or not self._nonce_ok(raw, sent, full):
            return self._reject(raw)
        self.last = full
        return self._dispatch(chr(raw[0]), sent)

    @staticmethod
    def _nonce_ok(raw: bytes, sent: Sent, full: int) -> bool:
        """§4.7: only encrypted frames depend on the full seq for their nonce."""
        return chr(raw[0]) not in ENCRYPTED_TYPES or full == sent.nonce

    def _error(self) -> type[PyronixError]:
        """The error at the NACK limit in data mode (§4.3)."""
        return ProtocolError if self.data_verified else AuthenticationError

    def _reject(self, raw: bytes) -> Outcome:
        """NACK with the rejected header and step the panel seq back (§4.3)."""
        if self.sent_nacks >= MAX_CONSECUTIVE_NACK:
            return [], [], self._error()
        self.sent_nacks += 1
        self.rewinds += 1
        self.last = max(self.last - 1, 0)
        self.tx += 1
        header = raw[:HEADER_LEN].ljust(HEADER_LEN, b"\0")
        return [build_frame("C", self.tx, header, None)], [], None

    def _dispatch(self, frame_type: str, sent: Sent) -> Outcome:
        """Act on a verified frame."""
        if frame_type == "c":  # resend the last non-NACK frame unchanged
            self.recv_nacks += 1
            if self.recv_nacks > MAX_CONSECUTIVE_NACK:
                return [], [], self._error()
            return [self.last_sent], [], None
        if frame_type == "e":
            self.closed = True
            return [], [PanelGoodbye("e")], None
        self.sent_nacks = self.recv_nacks = 0
        self.data_verified = True
        event = PanelData if frame_type == "d" else PanelPhoto
        return [], [event(sent.payload)], None


def _link_in_data_mode() -> tuple[PanelLink, bytes, bytes]:
    """Run the handshake with a fake panel; return the link, key and ``B`` frame."""
    private = ec.derive_private_key(0x5EED_0001, ec.SECP256R1())
    link = PanelLink(PANEL_ID, PASSWORD, keypair_factory=lambda: EcdhKeyPair(private))
    panel = FakePanel(PanelBehaviour(app_password=PASSWORD, user_code="73519"))
    (announce,) = panel.receive_frame(link.start())
    (b_frame,), _ = link.receive(announce)
    (key_frame,) = panel.receive_frame(b_frame)
    link.receive(key_frame)
    assert link.state is LinkState.DATA
    assert panel._key is not None
    return link, panel._key, b_frame


def _corrupt(raw: bytes, how: str, pos: int, flip: int) -> bytes:
    """Damage one part of a frame; every result fails check16 (§4.1)."""
    body = bytearray(raw)
    payload_len = len(raw) - MIN_FRAME_LEN
    offsets = {
        "check": len(raw) - 16 + pos % 16,
        "ciphertext": HEADER_LEN + pos % max(payload_len, 1),
        "seq": 1 + pos % 2,
        "length": 3 + pos % 2,
        "type": 0,
    }
    if how == "truncate":
        return raw[: pos % len(raw)]
    if how == "extend":
        return raw + bytes([flip]) * (1 + pos % 8)
    body[offsets[how]] ^= flip
    return bytes(body)


def _describe(out: list[bytes], events: list[LinkEvent], error: object) -> str:
    """Name an outcome for ``--hypothesis-show-statistics``."""
    if error is not None:
        return f"link raised {error}"
    if events:
        return f"delivered {type(events[0]).__name__}"
    if not out:
        return "ignored after goodbye"
    return "NACK sent" if out[0][0] == ord("C") else "resent after a panel NACK"


def _on_wire(raw: bytes, *, as_text: bool) -> bytes:
    """Encode a frame as ``(base64)`` text, or raw if §4.2 allows that type raw.

    The receiver only treats ``a``-``f`` as the start of a raw frame, so ``g``
    (take-photo) frames can only arrive as text.
    """
    if as_text or not 0x61 <= raw[0] <= 0x66:
        return wrap(raw).encode()
    return raw


class LinkMachine(RuleBasedStateMachine):
    """A data-mode link fed by a panel over a lossy, hostile relay."""

    def __init__(self) -> None:
        super().__init__()
        self.link, key, b_frame = _link_in_data_mode()
        self.reassembler = FrameReassembler()
        self.model = LinkModel(key=key, last_sent=b_frame)
        self.panel_tx = self.panel_view = 2
        self.created: list[Sent] = []
        self.pending: deque[OnWire] = deque()
        self.unfed = bytearray()
        self.fed = 0
        self.delivered: dict[int, int] = {}  # ident -> rewinds at delivery
        self.near_wrap = False
        self.dead = False  # the link raised; a session would close here

    @initialize(start=st.sampled_from([0, 0xFFFE, 0, 0xFFF0, 0xFF7E, 0x1FFFD]))
    def start_at(self, start: int) -> None:
        """Optionally resume a long session just before a 16-bit seq wrap (§4.3)."""
        if start:
            self.near_wrap = True
            self.link._codec._rx_seq = self.model.last = start
            self.panel_tx = self.panel_view = start

    # --- the panel ---------------------------------------------------------------

    def _seal(self, kind: str, nonce: int) -> Sent:
        ident = len(self.created)
        payload = b"" if kind == "e" else f'{{"id":{ident}}}'.encode()
        if kind == "c":
            payload = b"D\x05\x00\x00\x00"
        key = self.model.key if kind in ENCRYPTED_TYPES else None
        sent = Sent(build_frame(kind, nonce, payload, key), nonce, ident, payload)
        self.created.append(sent)
        return sent

    def _enqueue(self, wire: bytes, raw: bytes, sent: Sent | None) -> None:
        self.unfed += wire
        self.pending.append(OnWire(self.fed + len(self.unfed), raw, sent))

    def _send(self, sent: Sent, *, as_text: bool) -> None:
        self._enqueue(_on_wire(sent.raw, as_text=as_text), sent.raw, sent)

    def _panel_next(self, kind: str, gap: int) -> Sent:
        """Build the panel's next frame, ``gap`` seqs on (§4.3, §4.7)."""
        self.panel_tx = (self.panel_tx + gap) % 2**32
        nonce = reference_accept(self.panel_view, self.panel_tx % SPAN16)
        assert nonce is not None
        self.panel_view = nonce
        return self._seal(kind, nonce)

    @precondition(lambda self: not self.dead)
    @rule(gap=st.integers(1, 3), how=st.sampled_from(["text", "raw", "lost", "text"]))
    def panel_sends_data(self, gap: int, how: str) -> None:
        """The panel's next ``d`` frame, in order; it may be lost on the way (§4.3)."""
        self._panel_sends("d", gap, how)

    @precondition(lambda self: not self.dead)
    @rule(kind=st.sampled_from("cg"), how=st.sampled_from(["text", "raw", "lost"]))
    def panel_sends_nack_or_photo(self, kind: str, how: str) -> None:
        """The panel NACKs our last frame, or sends a photo (§4.1, §4.3)."""
        self._panel_sends(kind, 1, how)

    def _panel_sends(self, kind: str, gap: int, how: str) -> None:
        sent = self._panel_next(kind, gap)
        if how != "lost":
            self._send(sent, as_text=how == "text")

    @precondition(lambda self: not self.dead)
    @rule(dice=st.integers(0, 15))
    def panel_says_goodbye(self, dice: int) -> None:
        """Rarely, the panel's ``e``; the link reports it and closes (§4.8)."""
        if dice == 9:  # one in 16; Hypothesis favours 0 and the bounds
            self._send(self._panel_next("e", 1), as_text=True)

    @precondition(lambda self: not self.dead)
    @rule(ahead=st.sampled_from([0, 127, 128, 129, 255, 300, 0x8000]))
    def panel_jumps(self, ahead: int) -> None:
        """A frame outside the window: same seq, far ahead, or a wide wrap."""
        sent = self._seal("d", (self.panel_view + ahead) % 2**32)
        self._send(sent, as_text=True)

    @precondition(lambda self: not self.dead and bool(self.created))
    @rule(back=st.integers(0, 8), as_text=st.booleans())
    def relay_replays(self, back: int, *, as_text: bool) -> None:
        """A retransmit, duplicate, late (reordered) or replayed frame."""
        self._send(self.created[-1 - back % len(self.created)], as_text=as_text)

    @precondition(lambda self: not self.dead and bool(self.created))
    @rule(
        back=st.integers(0, 8),
        how=st.sampled_from(CORRUPTIONS),
        pos=st.integers(0, 2**16),
        flip=st.integers(1, 255),
    )
    def relay_corrupts(self, back: int, how: str, pos: int, flip: int) -> None:
        """A frame damaged in its check, length, ciphertext, header or size."""
        sent = self.created[-1 - back % len(self.created)]
        raw = _corrupt(sent.raw, how, pos, flip)
        # Raw binary framing trusts the header length (§4.2), so only damage that
        # keeps the length and type intact may travel unwrapped.
        as_text = how not in {"check", "ciphertext", "seq"} or flip % 2 == 0
        self._enqueue(_on_wire(raw, as_text=as_text), raw, None)

    @rule(junk=st.lists(st.sampled_from(SKIPPABLE), max_size=12).map(bytes))
    def relay_adds_junk(self, junk: bytes) -> None:
        """Bytes between frames that cannot start one (§4.2, live: leading space)."""
        self.unfed += junk

    @precondition(lambda self: not self.dead)
    @rule(text=st.text(B64_ALPHABET, max_size=24))
    def relay_sends_bad_text(self, text: str) -> None:
        """A ``(...)`` frame that is not base64, or too short to be a frame."""
        try:
            raw = base64.b64decode(text, validate=True)
        except binascii.Error:
            raw = b""
        self._enqueue(b"(" + text.encode() + b")", raw, None)

    # --- the relay delivers ------------------------------------------------------

    @precondition(lambda self: bool(self.unfed))
    @rule(
        upto=st.one_of(st.none(), st.integers(0, 2**16)),
        cuts=st.lists(st.integers(0, 2**16), max_size=4),
    )
    def relay_delivers(self, upto: int | None, cuts: list[int]) -> None:
        """Deliver some or all buffered bytes, cut into messages anywhere (§4.2)."""
        size = len(self.unfed) if upto is None else upto % (len(self.unfed) + 1)
        points = sorted({cut % (size + 1) for cut in cuts})
        bounds = zip([0, *points], [*points, size], strict=True)
        chunks = [bytes(self.unfed[start:end]) for start, end in bounds]
        del self.unfed[:size]
        for chunk in chunks:
            self._feed(chunk)

    def _feed(self, chunk: bytes) -> None:
        self.fed += len(chunk)
        got = self.reassembler.feed(chunk)
        done: list[OnWire] = []
        while self.pending and self.pending[0].end <= self.fed:
            done.append(self.pending.popleft())
        # Reassembly yields exactly the frames on the wire, in order (§4.2).
        assert got == [frame.raw for frame in done]
        for frame in done:
            self._receive(frame)

    def _receive(self, frame: OnWire) -> None:
        if self.dead:
            return
        out, events, error = self.model.expect(frame.raw, frame.sent)
        event(_describe(out, events, error))
        if error is not None:
            with pytest.raises(PyronixError) as caught:
                self.link.receive(frame.raw)
            assert type(caught.value) is error
            self.dead = True
            return
        assert self.link.receive(frame.raw) == (out, events)
        if any(isinstance(event, PanelData | PanelPhoto) for event in events):
            self._delivered(frame.sent)

    def _delivered(self, sent: Sent | None) -> None:
        assert sent is not None, "a corrupted frame was delivered"
        if sent.ident in self.delivered:
            # The only way a frame comes up twice: §4.3 steps the panel seq back
            # after every NACK, reopening the window for the last accepted frame.
            assert self.model.rewinds > self.delivered[sent.ident]
        self.delivered[sent.ident] = self.model.rewinds

    # --- the app -----------------------------------------------------------------

    @precondition(lambda self: not self.dead and not self.model.closed)
    @rule(payload=st.binary(max_size=24))
    def app_sends(self, payload: bytes) -> None:
        """Outgoing ``D`` frames count up by one from the last frame sent (§4.3)."""
        raw = self.link.send_data(payload)
        self.model.tx += 1
        assert raw == build_frame("D", self.model.tx, payload, self.model.key)
        self.model.last_sent = raw

    @precondition(lambda self: not self.dead and not self.model.closed)
    @rule(dice=st.integers(0, 15))
    def app_says_goodbye(self, dice: int) -> None:
        """``E`` takes the next seq; the link then ignores the panel (§4.8)."""
        if dice != 9:  # one in 16: a closed link ignores everything after it
            return
        self.model.tx += 1
        expected = build_frame("E", self.model.tx, b"", self.model.key)
        assert self.link.goodbye() == expected
        self.model.closed = True

    @precondition(
        lambda self: (
            not (self.dead or self.model.closed or self.near_wrap)
            and not self.unfed
            and 0 <= self.panel_tx - self.model.last < WRAP_GAP - 1
        )
    )
    @rule()
    def panel_recovers(self) -> None:
        """After any recoverable fault, the panel's next frame gets through."""
        self._send(self._panel_next("d", 1), as_text=True)
        self.relay_delivers(None, [])
        assert self.delivered.get(len(self.created) - 1) == self.model.rewinds

    @invariant()
    def receive_state_matches_the_model(self) -> None:
        """Seqs never drift from the spec, in either direction (§4.3)."""
        assert self.link._codec._rx_seq == self.model.last
        assert self.link._codec._tx_seq == self.model.tx
        assert (self.link.state is LinkState.CLOSED) == self.model.closed


def test_link_on_a_hostile_network() -> None:
    # Examples come from the loaded profile (conftest.py); only the run length is set.
    run_state_machine_as_test(LinkMachine, settings=settings(stateful_step_count=100))


@given(
    last=st.one_of(
        st.integers(0, 300),
        st.integers(SPAN16 - 300, SPAN16 + 300),
        st.integers(0, 2**32 - 1),
    ),
    seq16=st.one_of(st.integers(0, 300), st.integers(0, SPAN16 - 1)),
)
def test_seq_rule_matches_the_spec_text(last: int, seq16: int) -> None:
    assert accept_panel_seq(last, seq16) == reference_accept(last, seq16)


@given(chunks=st.lists(st.binary(max_size=200), max_size=8))
def test_any_bytes_raise_only_library_errors(chunks: list[bytes]) -> None:
    """Arbitrary relay bytes, junk that looks like a frame start included (§4.2)."""
    link, _, _ = _link_in_data_mode()
    reassembler = FrameReassembler()
    try:
        for chunk in chunks:
            for raw in reassembler.feed(chunk):
                link.receive(raw)
    except PyronixError:
        pass


@pytest.mark.xfail(
    strict=True,
    reason="§4.3 steps the panel seq back after each NACK, so a relay that injects "
    "junk can replay the last frames forever; needs a spec decision",
)
def test_junk_does_not_reopen_the_window_for_replays() -> None:
    link, key, _ = _link_in_data_mode()
    sent = [build_frame("d", seq, b'{"n":%d}' % seq, key) for seq in (3, 4, 5)]
    for raw in sent:
        link.receive(raw)
    for _ in range(MAX_CONSECUTIVE_NACK):
        link.receive(b"")  # three NACKs: the panel seq steps back from 5 to 2
    replayed = [event for raw in sent for event in link.receive(raw)[1]]
    assert replayed == []
