# SPDX-FileCopyrightText: 2026 Paul Fryer
# SPDX-License-Identifier: LGPL-3.0-or-later
"""Panel frames: build, encrypt, open and verify them, and the receive sequence rules.

PROTOCOL.md §4.1, §4.3.

A frame is ``type(1) ‖ seq(2, LE) ‖ len(2, LE) ‖ payload ‖ check(16)``. For the
encrypted types everything after the 5-byte header, check included, is
AES-CFB encrypted.
"""

from __future__ import annotations

from dataclasses import dataclass
import struct

from .crypto import CHECK_LEN, cfb_decrypt, cfb_encrypt, check16
from .errors import FrameError

HEADER_LEN = 5
"""Bytes of the clear-text header: type, seq (LE16), len (LE16)."""

MIN_FRAME_LEN = HEADER_LEN + CHECK_LEN
"""Length of a frame with an empty payload (21)."""

RX_SEQ_WRAP_GAP = 128
"""How far ahead of the last panel seq a new one may be (``RX_SEQ_WRAP_GAP``)."""

ENCRYPTED_TYPES: frozenset[str] = frozenset("DdEeGg")
"""Frame types whose payload and check are encrypted."""

_MAX_PAYLOAD = 0xFFFF
_SEQ16_MASK = 0xFFFF
_SEQ32_MASK = 0xFFFF_FFFF
_SEQ_HIGH_MASK = 0xFFFF_0000
_SEQ16_SPAN = 0x1_0000
_ASCII_LIMIT = 0x80


@dataclass(frozen=True, slots=True)
class Frame:
    """An opened and verified frame."""

    type: str
    """The frame type, one character, e.g. ``"d"``."""

    seq: int
    """The full sequence number that was used for the nonce."""

    payload: bytes
    """The plaintext payload."""


def _type_byte(frame_type: str) -> int:
    """Return the type byte of a one-letter ASCII frame type, or raise ValueError."""
    if len(frame_type) != 1 or ord(frame_type) >= _ASCII_LIMIT:
        msg = f"frame type must be one ASCII character, got {frame_type!r}"
        raise ValueError(msg)
    return ord(frame_type)


def build_frame(frame_type: str, seq: int, payload: bytes, key: bytes | None) -> bytes:
    """Build a frame, encrypting bytes 5 onwards for the encrypted types.

    Args:
        frame_type: One ASCII letter, e.g. ``"D"``. Lower-case types (panel to
            app) are allowed so a fake panel can use this too.
        seq: The sender's full sequence counter, taken modulo 2**32. The header
            holds its low 16 bits; the nonce uses all 32.
        payload: The plaintext payload, at most 65535 bytes.
        key: The 16-byte session key; required for ``D d E e G g`` only.

    Returns:
        The raw frame, ``len(payload) + 21`` bytes long.

    Raises:
        ValueError: Bad type, oversized payload, or no key for an encrypted type.
    """
    type_byte = _type_byte(frame_type)
    if len(payload) > _MAX_PAYLOAD:
        msg = f"payload of {len(payload)} bytes exceeds {_MAX_PAYLOAD}"
        raise ValueError(msg)
    header = struct.pack("<BHH", type_byte, seq & _SEQ16_MASK, len(payload))
    body = payload + check16(header + payload)
    if frame_type not in ENCRYPTED_TYPES:
        return header + body
    if key is None:
        msg = f"frame type {frame_type!r} is encrypted but no key was given"
        raise ValueError(msg)
    return header + cfb_encrypt(key, type_byte, len(payload), seq, body)


def _require_min_len(raw: bytes) -> None:
    """Raise FrameError if ``raw`` is shorter than an empty frame."""
    if len(raw) < MIN_FRAME_LEN:
        msg = f"frame too short: {len(raw)} bytes, need {MIN_FRAME_LEN}"
        raise FrameError(msg)


def frame_type_of(raw: bytes) -> str:
    """Return the type letter of a raw frame.

    Args:
        raw: The raw frame bytes.

    Returns:
        The type as a one-character string (the first byte, Latin-1).

    Raises:
        FrameError: The frame is shorter than 21 bytes.
    """
    _require_min_len(raw)
    return chr(raw[0])


def header_seq(raw: bytes) -> int:
    """Return the low 16 bits of the sender's seq from the header, read unsigned.

    The app reads these bytes signed (an app bug, PROTOCOL.md §4.3); this does not.

    Args:
        raw: The raw frame bytes.

    Returns:
        The 16-bit sequence number (0..65535).

    Raises:
        FrameError: The frame is shorter than 21 bytes.
    """
    _require_min_len(raw)
    return raw[1] | (raw[2] << 8)


def open_frame(raw: bytes, key: bytes | None, seq_full: int) -> Frame:
    """Decrypt (for the encrypted types) and verify a raw frame.

    Like the Java, the payload length comes from the frame's actual length
    (``len(raw) - 21``), not from the header's len field.

    Args:
        raw: The raw frame bytes.
        key: The 16-byte session key, or None before key agreement.
        seq_full: The full sequence number for the nonce (see
            :func:`accept_panel_seq`).

    Returns:
        The verified frame.

    Raises:
        FrameError: The frame is too short, is encrypted but no key is known, or
            its check does not match.
    """
    _require_min_len(raw)
    frame_type = chr(raw[0])
    payload_len = len(raw) - MIN_FRAME_LEN
    body = raw[HEADER_LEN:]
    if frame_type in ENCRYPTED_TYPES:
        if key is None:
            msg = f"encrypted frame {frame_type!r} received before key agreement"
            raise FrameError(msg)
        body = cfb_decrypt(key, raw[0], payload_len, seq_full, body)
    payload, check = body[:payload_len], body[payload_len:]
    if check16(raw[:HEADER_LEN] + payload) != check:
        msg = f"check mismatch on {frame_type!r} frame of {len(raw)} bytes"
        raise FrameError(msg)
    return Frame(type=frame_type, seq=seq_full, payload=payload)


def accept_panel_seq(last: int, seq16: int) -> int | None:
    """Apply the app's panel-seq rules (``checkDecryptMessage``, §4.3), exactly.

    The first frame (``last == 0``) must carry 1..127. After that a seq must be
    ahead of the last one by less than 128, or wrap from above 65408 to below
    128. Only the wrapping frame gets the high 16 bits added; in the normal
    branch the full seq is just ``seq16``, as in the Java.

    Args:
        last: The last accepted full panel seq (0 before any frame).
        seq16: The 16-bit seq from the new frame's header (0..65535).

    Returns:
        The full seq to use for the nonce and to store as the new ``last``, or
        None to reject the frame.
    """
    last &= _SEQ32_MASK
    if last == 0 and seq16 != 0:
        return seq16 if seq16 < RX_SEQ_WRAP_GAP else None
    last16 = last & _SEQ16_MASK
    if seq16 < last16:
        if seq16 >= RX_SEQ_WRAP_GAP or last16 <= _SEQ16_SPAN - RX_SEQ_WRAP_GAP:
            return None
        return (seq16 | ((last & _SEQ_HIGH_MASK) + _SEQ16_SPAN)) & _SEQ32_MASK
    if seq16 <= last16 or seq16 - last16 >= RX_SEQ_WRAP_GAP:
        return None
    return seq16
