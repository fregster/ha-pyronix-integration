# SPDX-FileCopyrightText: 2026 Paul Fryer
# SPDX-License-Identifier: LGPL-3.0-or-later
"""Sans-IO encrypted panel link: the handshake, data mode and goodbye state machine.

PROTOCOL.md §4.

A port of ``PanelEncrypt`` that fixes the app bugs listed in the spec: NACKs are
real ``C`` frames (payload = the rejected 5-byte header, plain check) and panel
seqs are read unsigned. The link takes whole raw frames (see
:class:`pyronix_cloud.transport.FrameReassembler`) and returns raw frames to send.
Frame numbering and sealing live in :mod:`pyronix_cloud.link_codec`, NACK limits
in :mod:`pyronix_cloud.link_nacks` and the events in
:mod:`pyronix_cloud.link_events`.
"""

from __future__ import annotations

import enum
import logging
from typing import TYPE_CHECKING, Final

from pyronix_cloud import errors, frames
from pyronix_cloud.announce import parse_announce
from pyronix_cloud.crypto import EcdhKeyPair, derive_session_key, fixed_key
from pyronix_cloud.link_codec import LinkCodec
from pyronix_cloud.link_events import (
    KeyAgreed,
    LinkEvent,
    PanelData,
    PanelGoodbye,
    PanelPhoto,
)
from pyronix_cloud.link_nacks import NackCounter

if TYPE_CHECKING:
    from collections.abc import Callable

__all__ = [
    "KeyAgreed",
    "LinkEvent",
    "LinkState",
    "PanelData",
    "PanelGoodbye",
    "PanelLink",
    "PanelPhoto",
]

_LOGGER = logging.getLogger(__name__)


class LinkState(enum.Enum):
    """Where the link is in its life cycle."""

    IDLE = enum.auto()
    AWAIT_ANNOUNCE = enum.auto()
    AWAIT_KEY = enum.auto()
    DATA = enum.auto()
    CLOSED = enum.auto()


_GOODBYE_TYPES: Final = {
    LinkState.AWAIT_ANNOUNCE: "F",
    LinkState.AWAIT_KEY: "F",
    LinkState.DATA: "E",
}

type _Result = tuple[list[bytes], list[LinkEvent]]


class PanelLink:
    """The app side of the encrypted panel session, without I/O."""

    def __init__(
        self,
        panel_id: str,
        app_password: str,
        *,
        which_key: int = 1,
        keypair_factory: Callable[[], EcdhKeyPair] = EcdhKeyPair.generate,
    ) -> None:
        """Prepare a link; nothing is sent until :meth:`start`.

        Args:
            panel_id: The System ID the announce must carry.
            app_password: The panel app password (for the fixed key).
            which_key: The key index sent in ``B`` (the app uses 1).
            keypair_factory: Makes the ephemeral ECDH key pair.
        """
        self._panel_id = panel_id.encode("utf-8")
        self._fixed = fixed_key(app_password, panel_id)
        self._which_key = which_key & 0xFF
        self._keypair_factory = keypair_factory
        self._keypair: EcdhKeyPair | None = None
        self._state = LinkState.IDLE
        self._codec = LinkCodec()
        self._nacks = NackCounter()
        self._announce_extra = b""

    @property
    def state(self) -> LinkState:
        """The current state."""
        return self._state

    def start(self) -> bytes:
        """Begin the handshake.

        Returns:
            The ``A`` (announce) frame.

        Raises:
            RuntimeError: The link was already started.
        """
        if self._state is not LinkState.IDLE:
            msg = "panel link already started"
            raise RuntimeError(msg)
        self._state = LinkState.AWAIT_ANNOUNCE
        return self._codec.seal("A", b"")

    def receive(self, raw: bytes) -> _Result:
        """Handle one raw frame from the panel.

        Args:
            raw: One complete frame (``b""`` for one that could not be decoded).

        Returns:
            The raw frames to send, and the events, in order.

        Raises:
            HandshakeError: The announce or key exchange failed, or the panel
                sent ``f`` during it.
            AuthenticationError: The NACK limit was hit before any encrypted
                panel frame verified (the app password is wrong).
            ProtocolError: The NACK limit was hit in data mode.
        """
        if self._state in {LinkState.IDLE, LinkState.CLOSED}:
            _LOGGER.debug("Ignored a panel frame in state %s", self._state.name)
            return [], []
        frame = self._codec.open(raw)
        if frame is not None:
            return self._dispatch(frame, raw)
        if self._state is LinkState.AWAIT_ANNOUNCE:
            return [], []  # as the app: no NACK for a bad frame before the announce
        return self._nack(raw, f"bad frame in state {self._state.name}")

    def send_data(self, payload: bytes) -> bytes:
        """Build an encrypted ``D`` frame.

        Args:
            payload: The plaintext command payload.

        Returns:
            The raw frame.

        Raises:
            NotConnectedError: The link is not in data mode.
        """
        if self._state is not LinkState.DATA:
            msg = "the panel link is not in data mode"
            raise errors.NotConnectedError(msg)
        return self._codec.seal("D", payload)

    def goodbye(self) -> bytes | None:
        """Close the link.

        Returns:
            ``E`` in data mode, ``F`` during the handshake, None otherwise.
        """
        frame_type = _GOODBYE_TYPES.get(self._state)
        frame = self._codec.seal(frame_type, b"") if frame_type else None
        self._state = LinkState.CLOSED
        self._codec.key = None
        self._keypair = None
        return frame

    def _nack(self, raw: bytes, reason: str) -> _Result:
        """Send a ``C`` frame for a rejected frame, or raise at the NACK limit."""
        self._nacks.count_sent(reason, in_data=self._state is LinkState.DATA)
        self._codec.rewind()
        header = raw[: frames.HEADER_LEN].ljust(frames.HEADER_LEN, b"\0")
        return [self._codec.seal("C", header)], []

    def _dispatch(self, frame: frames.Frame, raw: bytes) -> _Result:
        """Act on a verified frame according to the state."""
        match (self._state, frame.type):
            case (_, "c"):
                return self._panel_nacked()
            case (LinkState.DATA, "d" | "g"):
                self._nacks.clear(data_verified=True)
                data = frame.payload
                return [], [PanelData(data) if frame.type == "d" else PanelPhoto(data)]
            case (LinkState.DATA, "e" | "f"):
                self._state = LinkState.CLOSED
                self._codec.key = None
                return [], [PanelGoodbye(frame.type)]
            case (_, "f"):
                self._state = LinkState.CLOSED
                msg = "the panel closed the link during the handshake"
                raise errors.HandshakeError(msg)
            case (LinkState.AWAIT_ANNOUNCE, "a"):
                return self._on_announce(frame.payload, raw)
            case (LinkState.AWAIT_KEY, "b"):
                return self._on_key(frame.payload)
        return self._nack(raw, f"unexpected {frame.type!r} in state {self._state.name}")

    def _panel_nacked(self) -> _Result:
        """Count a NACK from the panel and send the frame it rejected again.

        During the handshake the ``A`` or ``B`` frame is rebuilt (as the app
        does for ``B``); in data mode the last frame goes out unchanged.
        """
        self._nacks.count_received(in_data=self._state is LinkState.DATA)
        _LOGGER.debug("Panel NACK in state %s; sending again", self._state.name)
        if self._state is LinkState.AWAIT_ANNOUNCE:
            return [self._codec.seal("A", b"")], []
        if self._state is LinkState.AWAIT_KEY:
            return [self._send_key()], []
        return [self._codec.last_sent], []

    def _on_announce(self, payload: bytes, raw: bytes) -> _Result:
        """Check the announced System ID, keep the key extra, send ``B``."""
        announce = parse_announce(payload, len(raw))
        if announce.panel_id != self._panel_id:
            _LOGGER.warning("The panel announced a different System ID")
            return self._nack(raw, "the panel announced a different System ID")
        self._announce_extra = announce.key_extra
        self._state = LinkState.AWAIT_KEY
        self._nacks.clear()
        return [self._send_key()], [announce.announced]

    def _send_key(self) -> bytes:
        """Build a ``B`` frame, keeping one key pair for re-sends."""
        if self._keypair is None:
            self._keypair = self._keypair_factory()
        return self._codec.seal(
            "B", bytes([self._which_key]) + self._keypair.public_bytes
        )

    def _on_key(self, payload: bytes) -> _Result:
        """Derive the session key from the panel's ``b`` frame."""
        keypair = self._keypair or self._keypair_factory()
        shared = keypair.shared_secret(payload[1:])
        self._codec.key = derive_session_key(self._fixed, shared, self._announce_extra)
        self._keypair = None
        self._state = LinkState.DATA
        self._nacks.clear()
        return [], [KeyAgreed()]
