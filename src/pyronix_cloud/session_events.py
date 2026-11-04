# SPDX-FileCopyrightText: 2026 Paul Fryer
# SPDX-License-Identifier: LGPL-3.0-or-later
"""Stages, events and outputs a protocol session reports to its owner.

PROTOCOL.md §7. :class:`pyronix_cloud.session.ProtocolSession` returns a
:class:`SessionOutput` from every call.
"""

from __future__ import annotations

from dataclasses import dataclass
import enum


class Stage(enum.Enum):
    """Where the session is, from the first cloud request to data mode."""

    IDLE = enum.auto()
    SUBSCRIBING = enum.auto()
    REQUESTING_PASSWORD = enum.auto()
    CONNECTING_PANEL = enum.auto()
    HANDSHAKE = enum.auto()
    LOGGING_IN = enum.auto()
    READY = enum.auto()
    CLOSED = enum.auto()


@dataclass(frozen=True, slots=True)
class StageChanged:
    """The session moved to a new stage."""

    stage: Stage


@dataclass(frozen=True, slots=True)
class DocumentReceived:
    """The panel sent one JSON document (not yet parsed)."""

    text: str


@dataclass(frozen=True, slots=True)
class PanelHeartbeat:
    """The panel sent its heartbeat (a ``d`` frame holding one space)."""


@dataclass(frozen=True, slots=True)
class SessionEnded:
    """The panel ended the session after login."""

    reason: str


type SessionEvent = StageChanged | DocumentReceived | PanelHeartbeat | SessionEnded


@dataclass(frozen=True, slots=True)
class SessionOutput:
    """What to send, and what happened, after one call."""

    outbound: tuple[str, ...] = ()
    """WebSocket text messages to send, in order."""

    events: tuple[SessionEvent, ...] = ()
    """Events, in order."""
