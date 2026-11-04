# SPDX-FileCopyrightText: 2026 Paul Fryer
# SPDX-License-Identifier: LGPL-3.0-or-later
"""Count consecutive NACKs on the panel link and pick the error at the limit.

PROTOCOL.md §4.3. Both directions allow :data:`MAX_CONSECUTIVE_NACK` in a row; a
verified frame clears both counts. The error depends on how far the link got: a
handshake failure, a wrong app password (data mode, but no encrypted panel frame
has verified yet) or a broken protocol.
"""

from __future__ import annotations

from typing import Final

from pyronix_cloud import errors

MAX_CONSECUTIVE_NACK: Final = 3
"""NACKs allowed in a row in either direction (``MAX_CONSECUTIVE_NACK``)."""


class NackCounter:
    """The consecutive NACK counts of one panel link."""

    def __init__(self) -> None:
        """Start with both counts at zero and no verified panel data."""
        self._sent = 0
        self._received = 0
        self._data_verified = False

    def clear(self, *, data_verified: bool = False) -> None:
        """Reset both counts after a frame verified.

        Args:
            data_verified: The frame was an encrypted panel frame; from now on
                the limit means a protocol error, not a wrong app password.
        """
        self._sent = self._received = 0
        self._data_verified = self._data_verified or data_verified

    def count_sent(self, reason: str, *, in_data: bool) -> None:
        """Count a NACK about to be sent for a rejected panel frame.

        Args:
            reason: Why the frame was rejected (no secrets).
            in_data: The link is in data mode.

        Raises:
            HandshakeError: The limit was hit during the handshake.
            AuthenticationError: The limit was hit before any panel data verified.
            ProtocolError: The limit was hit in data mode.
        """
        if self._sent >= MAX_CONSECUTIVE_NACK:
            raise self._error(reason, in_data=in_data)
        self._sent += 1

    def count_received(self, *, in_data: bool) -> None:
        """Count a NACK from the panel.

        Args:
            in_data: The link is in data mode.

        Raises:
            HandshakeError: The limit was passed during the handshake.
            AuthenticationError: The limit was passed before any panel data
                verified.
            ProtocolError: The limit was passed in data mode.
        """
        self._received += 1
        if self._received > MAX_CONSECUTIVE_NACK:
            msg = "the panel rejected our frames"
            raise self._error(msg, in_data=in_data)

    def _error(self, reason: str, *, in_data: bool) -> errors.PyronixError:
        """Return the error for hitting the limit."""
        if not in_data:
            return errors.HandshakeError(f"panel handshake failed: {reason}")
        if not self._data_verified:
            return errors.AuthenticationError("app password rejected")
        return errors.ProtocolError(f"too many consecutive NACKs: {reason}")
