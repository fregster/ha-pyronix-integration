# SPDX-FileCopyrightText: 2026 Paul Fryer
# SPDX-License-Identifier: LGPL-3.0-or-later
"""WebSocket text encoding of frames and messages, and reassembly of panel frames.

PROTOCOL.md §3.2, §4.2.

Outbound data is wrapped as ``"(" + base64 + ")"`` using Android's
``Base64.DEFAULT`` layout. Inbound panel frames arrive as ``(base64)`` text or as
raw binary starting with ``a``-``f``; one WebSocket message may hold several
frames, and one frame may be split across messages, so :class:`FrameReassembler`
buffers across feeds.
"""

from __future__ import annotations

import base64
import binascii
import logging
import re
from typing import Final

from pyronix_cloud.errors import ProtocolError

_LOGGER = logging.getLogger(__name__)

MAX_BUFFER: Final = 64 * 1024
"""Most bytes held without a complete frame before the stream is rejected."""

_HEADER_LEN: Final = 5
_CHECK_LEN: Final = 16
_OPEN: Final = ord("(")
_CLOSE: Final = b")"
_FRAME_START: Final = re.compile(rb"[(a-f]")
_NON_ALPHABET: Final = re.compile(rb"[^A-Za-z0-9+/=]")


def android_b64encode(data: bytes) -> str:
    """Encode bytes like Android ``Base64.encodeToString(data, Base64.DEFAULT)``.

    Standard alphabet, ``=`` padding, a newline after every 76 characters and a
    trailing newline; byte-identical to :func:`base64.encodebytes`.

    Args:
        data: The bytes to encode.

    Returns:
        The encoded text (empty for empty input).
    """
    return base64.encodebytes(data).decode("ascii")


def android_b64decode(text: bytes | str) -> bytes:
    """Decode base64 leniently, like Android ``Base64.decode(text, Base64.DEFAULT)``.

    Characters outside the base64 alphabet (whitespace, newlines) are skipped.

    Args:
        text: The base64 text.

    Returns:
        The decoded bytes.

    Raises:
        ValueError: The text has an invalid length or padding.
    """
    raw = text.encode("ascii", errors="ignore") if isinstance(text, str) else text
    try:
        return base64.b64decode(_NON_ALPHABET.sub(b"", raw), validate=True)
    except binascii.Error as err:
        msg = "invalid base64 data"
        raise ValueError(msg) from err


def wrap(data: bytes) -> str:
    """Wrap bytes for the WebSocket as ``"(" + base64 + ")"``.

    Args:
        data: A panel frame or the UTF-8 bytes of a cloud message.

    Returns:
        The WebSocket text message.
    """
    return "(" + android_b64encode(data) + ")"


class FrameReassembler:
    """Reassemble panel frames from a stream of WebSocket message bytes."""

    def __init__(self) -> None:
        """Start with an empty buffer."""
        self._buffer = bytearray()

    def feed(self, data: bytes) -> list[bytes]:
        """Append data and return every complete frame now available, in order.

        A ``(base64)`` frame whose text does not decode yields ``b""`` so that the
        caller can NACK it.

        Args:
            data: The bytes of one inbound WebSocket message.

        Returns:
            The complete frames, oldest first; possibly empty.

        Raises:
            ProtocolError: More than :data:`MAX_BUFFER` bytes are buffered without
                a complete frame. The buffer is discarded.
        """
        self._buffer += data
        frames: list[bytes] = []
        while (frame := self._next_frame()) is not None:
            frames.append(frame)
        if len(self._buffer) > MAX_BUFFER:
            size = len(self._buffer)
            self._buffer.clear()
            msg = f"{size} bytes buffered without a complete panel frame"
            raise ProtocolError(msg)
        return frames

    def _next_frame(self) -> bytes | None:
        """Remove and return the next complete frame, or None if more is needed."""
        self._skip_to_frame_start()
        if not self._buffer:
            return None
        if self._buffer[0] == _OPEN:
            return self._next_text_frame()
        return self._next_raw_frame()

    def _skip_to_frame_start(self) -> None:
        """Drop leading bytes until one is ``(`` or ``a``-``f``."""
        match = _FRAME_START.search(self._buffer)
        skip = match.start() if match else len(self._buffer)
        if skip:
            _LOGGER.debug("Skipped %d byte(s) before a panel frame", skip)
            del self._buffer[:skip]

    def _next_text_frame(self) -> bytes | None:
        """Take a ``(base64)`` frame from the buffer once its ``)`` has arrived."""
        end = self._buffer.find(_CLOSE)
        if end < 0:
            return None
        text = bytes(self._buffer[1:end])
        del self._buffer[: end + 1]
        try:
            frame = android_b64decode(text)
        except ValueError:
            _LOGGER.debug("Panel frame is not valid base64")
            return b""
        _LOGGER.debug("Received a base64 panel frame of %d bytes", len(frame))
        return frame

    def _next_raw_frame(self) -> bytes | None:
        """Take a raw binary frame once its full length (header + 21) has arrived."""
        if len(self._buffer) < _HEADER_LEN:
            return None
        size = (
            int.from_bytes(self._buffer[3:_HEADER_LEN], "little")
            + _HEADER_LEN
            + _CHECK_LEN
        )
        if len(self._buffer) < size:
            return None
        frame = bytes(self._buffer[:size])
        del self._buffer[:size]
        _LOGGER.debug("Received a raw binary panel frame of %d bytes", size)
        return frame
