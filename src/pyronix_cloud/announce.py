# SPDX-FileCopyrightText: 2026 Paul Fryer
# SPDX-License-Identifier: LGPL-3.0-or-later
"""Decode the panel's ``a`` announce: System ID, modem type and key extra bytes.

PROTOCOL.md §4.4 (step 3), §4.6.

The modem byte ``p[1]`` is read unsigned, fixing the app bug that left 0x81-0x86
as ``UNKNOWN``.
"""

from __future__ import annotations

from dataclasses import dataclass
import enum
from typing import Final

_FLAGS_AT: Final = 1
_ID_START: Final = 2
_EXTRA_START: Final = 10
_EXTRA_MAX: Final = 4
_EXTRA_BASE_LEN: Final = 42
_EXTENDED: Final = 0x80


class ModemType(enum.Enum):
    """The panel's uplink. Values are the extended codes of §4.6 (0 = unknown)."""

    UNKNOWN = 0
    GSM = 0x84
    GPRS = 0x85
    WAN_LAN = 0x83
    USB = 0x81
    PSTN = 0x86
    RS232 = 0x82


_MODEM_FLAGS: Final = (
    (0x60, ModemType.WAN_LAN),
    (0x02, ModemType.GPRS),
    (0x01, ModemType.GSM),
    (0x04, ModemType.PSTN),
)


@dataclass(frozen=True, slots=True)
class Announced:
    """What the panel announced about itself, as reported to callers."""

    modem: ModemType
    """The uplink the panel reports."""

    json_version: int
    """The first key extra byte, or 0 if there is none (``getJsonVersion``)."""


@dataclass(frozen=True, slots=True)
class Announce:
    """The fields of an ``a`` announce frame."""

    panel_id: bytes
    """Payload bytes 2-9, to compare with the configured System ID."""

    key_extra: bytes
    """Up to 4 bytes mixed into the session key (``announceExtra``)."""

    announced: Announced
    """The part reported to callers."""


def modem_type(flags: int) -> ModemType:
    """Decode the announce modem byte.

    Args:
        flags: ``p[1]``, read unsigned (0..255).

    Returns:
        The extended code if bit 7 is set, else the first matching flag bit.
    """
    if flags & _EXTENDED:
        return next((m for m in ModemType if m.value == flags), ModemType.UNKNOWN)
    return next((m for bit, m in _MODEM_FLAGS if flags & bit), ModemType.UNKNOWN)


def parse_announce(payload: bytes, frame_len: int) -> Announce:
    """Decode a verified ``a`` frame's payload.

    The extra bytes are ``p[10 : 10 + min(frame_len - 42, 4)]``, kept only when
    bit 7 of ``p[1]`` is set, exactly as ``initialKeyMessages`` does.

    Args:
        payload: The frame payload (after the 5-byte header).
        frame_len: The length of the whole raw frame.

    Returns:
        The decoded announce. A payload too short for a field yields a short
        ``panel_id`` (which will not match) and flags of 0.
    """
    flags = payload[_FLAGS_AT] if len(payload) > _FLAGS_AT else 0
    extra_len = min(frame_len - _EXTRA_BASE_LEN, _EXTRA_MAX)
    extra = b""
    if flags & _EXTENDED and extra_len > 0:
        extra = payload[_EXTRA_START : _EXTRA_START + extra_len]
    return Announce(
        panel_id=payload[_ID_START:_EXTRA_START],
        key_extra=extra,
        announced=Announced(modem_type(flags), extra[0] if extra else 0),
    )
