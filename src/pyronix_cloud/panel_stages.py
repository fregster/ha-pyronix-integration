# SPDX-FileCopyrightText: 2026 Paul Fryer
# SPDX-License-Identifier: LGPL-3.0-or-later
"""Advance the panel stages from link events, through the login to the session end.

PROTOCOL.md §4.8, §7. Sans-IO: :func:`on_link_event` maps one event from the
panel link to the next stage, the session events and whether to send the login.
"""

from __future__ import annotations

from dataclasses import dataclass
import logging
from typing import Final

from pyronix_cloud import errors
from pyronix_cloud.announce import Announced
from pyronix_cloud.link_events import KeyAgreed, LinkEvent, PanelData, PanelGoodbye
from pyronix_cloud.session_events import (
    DocumentReceived,
    PanelHeartbeat,
    SessionEnded,
    SessionEvent,
    Stage,
    StageChanged,
)

_LOGGER = logging.getLogger(__name__)

_PANEL_HEARTBEAT: Final = b" "
_DOCUMENT_START: Final = b"{"


@dataclass(frozen=True, slots=True)
class PanelStep:
    """What one link event leads to."""

    stage: Stage
    """The stage after the event (unchanged if there is no StageChanged)."""

    events: tuple[SessionEvent, ...] = ()
    """Session events, in order."""

    send_login: bool = False
    """Send the login command now (the session key was just agreed)."""


def on_link_event(stage: Stage, event: LinkEvent) -> PanelStep:
    """Return what a link event leads to in a panel stage.

    Args:
        stage: The current session stage.
        event: One event from :class:`pyronix_cloud.link.PanelLink`.

    Returns:
        The next stage, the events and whether to send the login.

    Raises:
        AuthenticationError: The panel said goodbye in reply to the login (the
            user code is wrong).
    """
    match event:
        case Announced(modem=modem, json_version=version):
            _LOGGER.debug("Panel announced: %s, JSON version %d", modem, version)
        case KeyAgreed():
            _LOGGER.debug("Session key agreed; logging in")
            return _move(Stage.LOGGING_IN, send_login=True)
        case PanelData(payload=payload):
            return _on_panel_data(stage, payload)
        case PanelGoodbye(frame_type=frame_type):
            if stage is Stage.LOGGING_IN:
                msg = "user code rejected"
                raise errors.AuthenticationError(msg)
            ended = SessionEnded(f"panel sent goodbye {frame_type!r}")
            return _move(Stage.CLOSED, ended)
        case _:
            _LOGGER.debug("Ignored a photo frame from the panel")
    return PanelStep(stage)


def _on_panel_data(stage: Stage, payload: bytes) -> PanelStep:
    """Turn a decrypted ``d`` payload into a heartbeat or document event."""
    if payload == _PANEL_HEARTBEAT:
        return PanelStep(stage, (PanelHeartbeat(),))
    if not payload.startswith(_DOCUMENT_START):
        _LOGGER.debug("Ignored %d bytes of non-JSON panel data", len(payload))
        return PanelStep(stage)
    document = DocumentReceived(payload.decode("utf-8", errors="replace"))
    if stage is Stage.LOGGING_IN:
        return _move(Stage.READY, document)
    return PanelStep(stage, (document,))


def _move(stage: Stage, *events: SessionEvent, send_login: bool = False) -> PanelStep:
    """Return a move to ``stage``, its StageChanged first, then ``events``."""
    return PanelStep(stage, (StageChanged(stage), *events), send_login=send_login)
