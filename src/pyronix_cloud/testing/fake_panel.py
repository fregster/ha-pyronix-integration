# SPDX-FileCopyrightText: 2026 Paul Fryer
# SPDX-License-Identifier: LGPL-3.0-or-later
"""Sans-IO fake Pyronix panel: the panel side of the handshake, documents and commands.

The fake follows PROTOCOL.md §4 and §5 from the panel's side and assumes nothing
about the client beyond the protocol. Every inbound frame is checked (sequence
number and check value); a frame that fails is answered with a ``c`` NACK whose
payload is the rejected header. The session key comes from the panel's own app
password, so a client with the wrong password cannot get its login accepted.

Area states changed by set/unset commands are kept in :attr:`FakePanel.areas`;
pass the same dict to several panels to keep them across sessions. The type of
every authenticated frame and the payload of every data-mode command are recorded,
so tests can prove which commands a flow sent.
"""

from __future__ import annotations

from dataclasses import dataclass, field
import enum
from typing import TYPE_CHECKING, Final

from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric import ec

from pyronix_cloud import commands
from pyronix_cloud.crypto import EcdhKeyPair, derive_session_key, fixed_key
from pyronix_cloud.errors import FrameError, HandshakeError
from pyronix_cloud.frames import (
    HEADER_LEN,
    Frame,
    accept_panel_seq,
    build_frame,
    header_seq,
    open_frame,
)
from pyronix_cloud.testing import dump
from pyronix_cloud.testing.panel_documents import SET, SETTING, UNSET, PanelDocuments

if TYPE_CHECKING:
    from collections.abc import Callable, Sequence

_PANEL_ID_LEN: Final = 8
_MAX_EXTRA: Final = 4
_EXTRA_FLAG: Final = 0x80
# The app reads extra bytes only when the frame is 42 + n bytes long (§4.4), so a
# real announce with n extra bytes carries 11 more bytes after them.
_ANNOUNCE_PAD: Final = bytes(11)
_KEY_INDEX: Final = b"\x01"
_AREA_COUNT_MASK: Final = 0x7F


@dataclass(frozen=True, slots=True, kw_only=True)
class PanelBehaviour:
    """How a :class:`FakePanel` identifies itself and responds.

    Raises:
        ValueError: The panel ID is not 8 ASCII characters, ``announce_extra``
            is longer than 4 bytes or ``repeat`` is below 1.
    """

    panel_id: str = dump.PANEL_ID
    app_password: str = field(default="app-password", repr=False)
    user_code: str = field(default="1234", repr=False)
    documents: Sequence[str] = field(default_factory=dump.login_dump)
    modem_flags: int = 0x83
    announce_extra: bytes = b"\x01"
    compressed_key: bool = False
    confirm_commands: bool = True
    set_statuses: tuple[int, ...] = (SETTING, SET)
    repeat: int = 2

    def __post_init__(self) -> None:
        """Validate the values the announce and pushes depend on."""
        if len(self.panel_id.encode("utf-8")) != _PANEL_ID_LEN:
            msg = "panel_id must be 8 ASCII characters"
            raise ValueError(msg)
        if len(self.announce_extra) > _MAX_EXTRA:
            msg = "announce_extra holds at most 4 bytes"
            raise ValueError(msg)
        if self.repeat < 1:
            msg = "repeat must be at least 1"
            raise ValueError(msg)


class _Stage(enum.Enum):
    """Where the panel is in the session."""

    ANNOUNCE = enum.auto()
    KEY = enum.auto()
    LOGIN = enum.auto()
    DATA = enum.auto()
    CLOSED = enum.auto()


class FakePanel:
    """The panel end of one encrypted session (PROTOCOL.md §4.4, §5, §6)."""

    def __init__(
        self,
        behaviour: PanelBehaviour | None = None,
        *,
        areas: dict[int, int] | None = None,
    ) -> None:
        """Create a panel waiting for the client's ``A`` announce.

        Args:
            behaviour: Identity and responses; the defaults match :mod:`dump`.
            areas: Area status overrides (area number to ``V``), shared and
                updated by set/unset commands. A new dict if None.
        """
        self.behaviour = behaviour or PanelBehaviour()
        self.documents = PanelDocuments(
            self.behaviour.documents, {} if areas is None else areas
        )
        self.received_commands: list[bytes] = []
        self.received_frame_types: list[str] = []
        self.goodbye_received = False
        self.logged_in = False
        self._stage = _Stage.ANNOUNCE
        self._rx_seq = 0
        self._tx_seq = 0
        self._key: bytes | None = None
        self._extra = b""
        self._last_tx: bytes | None = None
        self._private_key = ec.generate_private_key(ec.SECP256R1())

    @property
    def areas(self) -> dict[int, int]:
        """Area status overrides set by commands (area number to ``V``)."""
        return self.documents.areas

    @property
    def session_key(self) -> bytes | None:
        """The AES session key once agreed, so tests can check it never leaks."""
        return self._key

    @property
    def closed(self) -> bool:
        """Whether the session has ended (goodbye sent or received)."""
        return self._stage is _Stage.CLOSED

    def receive_frame(self, raw: bytes) -> list[bytes]:
        """Handle one raw frame from the client.

        Args:
            raw: The frame, already unwrapped from ``(base64)``.

        Returns:
            The raw frames the panel sends in reply, in order.
        """
        if self.closed:
            return []
        previous_seq = self._rx_seq
        try:
            frame = self._open(raw)
        except FrameError:
            return [self._send("c", (raw + bytes(HEADER_LEN))[:HEADER_LEN])]
        self.received_frame_types.append(frame.type)
        if frame.type == "C":
            return [self._last_tx] if self._last_tx is not None else []
        if frame.type in {"E", "F"}:
            self.goodbye_received = True
            self._stage = _Stage.CLOSED
            return []
        handler = self._HANDLERS.get((self._stage, frame.type))
        if handler is None:
            # Like the app after a NACK: expect this seq again (PROTOCOL.md §4.3).
            self._rx_seq = previous_seq
            return [self._send("c", raw[:HEADER_LEN])]
        return handler(self, frame.payload)

    def push(self, text: str) -> list[bytes]:
        """Build a ``d`` frame carrying ``text``, as the panel pushes a change.

        Args:
            text: Any document (or ``" "``, the panel heartbeat).

        Returns:
            One frame, or none if the client is not logged in.
        """
        if self._stage is not _Stage.DATA:
            return []
        return [self._data(text)]

    def _open(self, raw: bytes) -> Frame:
        """Check the seq and the check value of an inbound frame."""
        full = accept_panel_seq(self._rx_seq, header_seq(raw))
        if full is None:
            msg = "client sequence number out of order"
            raise FrameError(msg)
        frame = open_frame(raw, self._key, full)
        self._rx_seq = full
        return frame

    def _send(self, frame_type: str, payload: bytes) -> bytes:
        """Build the next outbound frame and remember it for NACK re-sends."""
        self._tx_seq += 1
        self._last_tx = build_frame(frame_type, self._tx_seq, payload, self._key)
        return self._last_tx

    def _data(self, text: str) -> bytes:
        """Build a ``d`` frame with UTF-8 ``text``."""
        return self._send("d", text.encode("utf-8"))

    def _on_announce(self, _payload: bytes) -> list[bytes]:
        """Answer ``A`` with ``a``: flags, panel ID and the extra bytes (§4.4)."""
        b = self.behaviour
        extra = b.announce_extra
        payload = bytes([0, b.modem_flags]) + b.panel_id.encode("utf-8") + extra
        if extra:
            payload += _ANNOUNCE_PAD
        # The app only mixes the extra bytes into the key when flag 0x80 is set.
        self._extra = extra if b.modem_flags & _EXTRA_FLAG else b""
        self._stage = _Stage.KEY
        return [self._send("a", payload)]

    def _on_key(self, payload: bytes) -> list[bytes]:
        """Answer ``B`` with ``b`` and derive the session key."""
        try:
            shared = EcdhKeyPair(self._private_key).shared_secret(payload[1:])
        except HandshakeError:
            self._stage = _Stage.CLOSED
            return [self._send("f", b"")]
        b = self.behaviour
        encoding = (
            serialization.PublicFormat.CompressedPoint
            if b.compressed_key
            else serialization.PublicFormat.UncompressedPoint
        )
        public = self._private_key.public_key().public_bytes(
            serialization.Encoding.X962, encoding
        )
        frame = self._send("b", _KEY_INDEX + public)
        self._key = derive_session_key(
            fixed_key(b.app_password, b.panel_id), shared, self._extra
        )
        self._stage = _Stage.LOGIN
        return [frame]

    def _on_login(self, payload: bytes) -> list[bytes]:
        """Check the user code: send the dump, or an encrypted goodbye ``e``."""
        if payload != b"," + self.behaviour.user_code.encode("utf-8") + b"\n":
            self._stage = _Stage.CLOSED
            return [self._send("e", b"")]
        self.logged_in = True
        self._stage = _Stage.DATA
        return [self._data(text) for text in self.documents.login_dump()]

    def _on_command(self, payload: bytes) -> list[bytes]:
        """Record and answer one application command (§5)."""
        self.received_commands.append(payload)
        opcode = payload[0] if payload else -1
        argument = payload[1] if len(payload) > 1 else 0
        if opcode == commands.HEARTBEAT:
            return [self._data(" ")]
        if opcode == commands.REQUEST_LOG:
            return [self._data(self.documents.log(argument))]
        statuses = {
            commands.SET_AREAS: self.behaviour.set_statuses,
            commands.UNSET_AREAS: (UNSET,),
        }.get(opcode)
        if statuses is None or not self.behaviour.confirm_commands:
            return []
        areas = payload[2 : 2 + (argument & _AREA_COUNT_MASK)]
        texts = self.documents.change_areas(areas, statuses)
        return [self._data(t) for t in texts for _ in range(self.behaviour.repeat)]

    _HANDLERS: Final[
        dict[tuple[_Stage, str], Callable[[FakePanel, bytes], list[bytes]]]
    ] = {
        (_Stage.ANNOUNCE, "A"): _on_announce,
        (_Stage.KEY, "B"): _on_key,
        (_Stage.LOGIN, "D"): _on_login,
        (_Stage.DATA, "D"): _on_command,
    }
