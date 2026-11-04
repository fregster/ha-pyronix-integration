# SPDX-FileCopyrightText: 2026 Paul Fryer
# SPDX-License-Identifier: LGPL-3.0-or-later
"""Build application-layer command payloads carried in `D` frames (PROTOCOL.md §5).

These are plaintext payloads; :mod:`pyronix_cloud.frames` encrypts and frames
them. Only the commands this library uses are defined here.
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Final

if TYPE_CHECKING:
    from collections.abc import Iterable

HEARTBEAT: Final = 0x20
SET_AREAS: Final = 0x2D
UNSET_AREAS: Final = 0x2F
REQUEST_LOG: Final = 0x35

_MAX_CODE_LEN: Final = 8
_MAX_AREAS: Final = 127
_MAX_BYTE: Final = 0xFF
_FORCE_OMITS: Final = 0x80


def login(user_code: str) -> bytes:
    r"""Build the login payload ``"," + code + "\n"``.

    Args:
        user_code: The keypad user code, 1 to 8 ASCII digits.

    Returns:
        The payload. It contains the user code; never log it.

    Raises:
        ValueError: The code is not 1 to 8 ASCII digits. The message does not
            contain the code.
    """
    if not (
        0 < len(user_code) <= _MAX_CODE_LEN
        and user_code.isascii()
        and user_code.isdigit()
    ):
        msg = f"user code must be 1 to {_MAX_CODE_LEN} ASCII digits"
        raise ValueError(msg)
    return b"," + user_code.encode("ascii") + b"\n"


def heartbeat() -> bytes:
    """Build the panel heartbeat payload, sent in reply to a cloud heartbeat.

    Returns:
        ``[0x20]``.
    """
    return bytes([HEARTBEAT])


def _area_bytes(areas: Iterable[int]) -> bytes:
    """Validate area numbers and return them sorted, one byte each."""
    values = list(areas)
    unique = sorted(set(values))
    if not 0 < len(values) <= _MAX_AREAS:
        msg = f"between 1 and {_MAX_AREAS} areas are required, got {len(values)}"
        raise ValueError(msg)
    if len(unique) != len(values):
        msg = "area numbers must be distinct"
        raise ValueError(msg)
    if not all(0 <= area <= _MAX_BYTE for area in unique):
        msg = f"area numbers must be between 0 and {_MAX_BYTE}"
        raise ValueError(msg)
    return bytes(unique)


def set_areas(areas: Iterable[int], *, force: bool = True) -> bytes:
    """Build the set (arm) payload ``[0x2D, n | 0x80 if force, areas…]``.

    Args:
        areas: 1 to 127 distinct area numbers, each 0 to 255.
        force: Set even if inputs are open, omitting them (the app always does).

    Returns:
        The payload, with the areas sorted.

    Raises:
        ValueError: The areas are invalid.
    """
    numbers = _area_bytes(areas)
    flags = _FORCE_OMITS if force else 0
    return bytes([SET_AREAS, len(numbers) | flags]) + numbers


def unset_areas(areas: Iterable[int]) -> bytes:
    """Build the unset (disarm) payload ``[0x2F, n, areas…]``.

    Args:
        areas: 1 to 127 distinct area numbers, each 0 to 255.

    Returns:
        The payload, with the areas sorted.

    Raises:
        ValueError: The areas are invalid.
    """
    numbers = _area_bytes(areas)
    return bytes([UNSET_AREAS, len(numbers)]) + numbers


def request_log(count: int) -> bytes:
    """Build the event-log request payload ``[0x35, count]``.

    Args:
        count: How many events to request, 1 to 255.

    Returns:
        The payload.

    Raises:
        ValueError: The count is out of range.
    """
    if not 0 < count <= _MAX_BYTE:
        msg = f"log count must be between 1 and {_MAX_BYTE}"
        raise ValueError(msg)
    return bytes([REQUEST_LOG, count])
