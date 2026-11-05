# SPDX-FileCopyrightText: 2026 Paul Fryer
# SPDX-License-Identifier: LGPL-3.0-or-later
"""Number, seal and open the frames of one panel link.

PROTOCOL.md §4.1, §4.3. Outbound seqs count up from 1 (modulo 2**32); inbound
seqs follow the app's rules (:func:`pyronix_cloud.frames.accept_panel_seq`) and
are read unsigned, fixing the app bug.
"""

from __future__ import annotations

import logging

from pyronix_cloud import errors, frames

_LOGGER = logging.getLogger(__name__)


class LinkCodec:
    """The sequence numbers and session key of one panel link."""

    def __init__(self) -> None:
        """Start with no key and both seqs at zero."""
        self.key: bytes | None = None
        """The session key once agreed; None for plain frames."""
        self.last_sent = b""
        """The last frame sealed other than a NACK, for re-sends."""
        self._tx_seq = 0
        self._rx_seq = 0

    def seal(self, frame_type: str, payload: bytes) -> bytes:
        """Build the next outbound frame; remember it unless it is a NACK.

        Args:
            frame_type: The frame type letter.
            payload: The plaintext payload.

        Returns:
            The raw frame, encrypted if the type calls for it.
        """
        self._tx_seq = (self._tx_seq + 1) % 2**32
        raw = frames.build_frame(frame_type, self._tx_seq, payload, self.key)
        if frame_type != "C":
            self.last_sent = raw
        _LOGGER.debug("TX %s seq=%d", frame_type, self._tx_seq)
        return raw

    def open(self, raw: bytes) -> frames.Frame | None:
        """Apply the seq rules, decrypt and verify one panel frame.

        Args:
            raw: One complete raw frame.

        Returns:
            The frame, or None if it is short, out of sequence or fails its check.
        """
        seq_full = None
        if len(raw) >= frames.MIN_FRAME_LEN:
            seq_full = frames.accept_panel_seq(self._rx_seq, frames.header_seq(raw))
        if seq_full is None:
            _LOGGER.debug("RX frame of %d bytes: short or out of sequence", len(raw))
            return None
        try:
            frame = frames.open_frame(raw, self.key, seq_full)
        except errors.FrameError:
            _LOGGER.debug("RX %s seq=%d failed its check", chr(raw[0]), seq_full)
            return None
        self._rx_seq = seq_full
        _LOGGER.debug("RX %s seq=%d", frame.type, seq_full)
        return frame

    def rewind(self) -> None:
        """Step the panel seq back by one, so a NACKed frame can come again."""
        if self._rx_seq:
            self._rx_seq -= 1
