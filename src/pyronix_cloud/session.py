# SPDX-FileCopyrightText: 2026 Paul Fryer
# SPDX-License-Identifier: LGPL-3.0-or-later
"""Sans-IO protocol session from the cloud stages to panel data mode.

PROTOCOL.md §3, §7.

:class:`ProtocolSession` turns inbound WebSocket messages into outbound messages
and events. It drives the cloud relay stages, then the encrypted panel link
(:mod:`pyronix_cloud.link`), the login and the stream of panel documents. It does
no I/O and never logs credentials, the login frame or cloud request text. The
stage logic is in :mod:`pyronix_cloud.cloud_stages` and
:mod:`pyronix_cloud.panel_stages`; the events in :mod:`pyronix_cloud.session_events`.
"""

from __future__ import annotations

import logging
from typing import TYPE_CHECKING, Final

from pyronix_cloud import cloud, cloud_stages, commands, errors, panel_stages
from pyronix_cloud.cloud import CloudRequest
from pyronix_cloud.link import PanelLink
from pyronix_cloud.session_events import (
    DocumentReceived,
    PanelHeartbeat,
    SessionEnded,
    SessionEvent,
    SessionOutput,
    Stage,
    StageChanged,
)
from pyronix_cloud.transport import FrameReassembler, wrap

if TYPE_CHECKING:
    from collections.abc import Callable

    from pyronix_cloud.cloud import CloudIdentity

__all__ = [
    "DocumentReceived",
    "PanelHeartbeat",
    "ProtocolSession",
    "SessionEnded",
    "SessionEvent",
    "SessionOutput",
    "Stage",
    "StageChanged",
]

_LOGGER = logging.getLogger(__name__)

_PANEL_STAGES: Final = frozenset({Stage.HANDSHAKE, Stage.LOGGING_IN, Stage.READY})


class ProtocolSession:
    """One protocol session over the cloud relay, without I/O."""

    def __init__(
        self,
        identity: CloudIdentity,
        user_code: str,
        *,
        link_factory: Callable[[str, str], PanelLink] | None = None,
    ) -> None:
        """Prepare a session; nothing is sent until :meth:`start`.

        Args:
            identity: The app and panel identity for the cloud relay.
            user_code: The keypad user code for the login.
            link_factory: Builds the panel link from the System ID and the app
                password; :class:`PanelLink` by default.

        Raises:
            ValueError: The user code is not 1 to 8 digits (not echoed).
        """
        self._identity = identity
        self._login = commands.login(user_code)
        factory = link_factory or PanelLink
        self._link = factory(identity.panel_id, identity.app_password)
        self._reassembler = FrameReassembler()
        self._stage = Stage.IDLE

    @property
    def stage(self) -> Stage:
        """The current stage."""
        return self._stage

    def start(self) -> SessionOutput:
        """Send the subscription request.

        Returns:
            The request, and the move to :attr:`Stage.SUBSCRIBING`.

        Raises:
            RuntimeError: The session was already started.
        """
        if self._stage is not Stage.IDLE:
            msg = "protocol session already started"
            raise RuntimeError(msg)
        return self._advance(Stage.SUBSCRIBING, CloudRequest.SUBSCRIPTION)

    def receive(self, message: str | bytes) -> SessionOutput:
        """Handle one inbound WebSocket message.

        Args:
            message: A text or binary message, as received.

        Returns:
            Messages to send and events, in order.

        Raises:
            CloudError: The cloud answered with an error letter (or, before the
                panel stages, an unknown one).
            AuthenticationError: The panel rejected the app password or the user
                code.
            ProtocolError: The panel broke the protocol.
        """
        if self._stage in {Stage.IDLE, Stage.CLOSED}:
            _LOGGER.debug("Ignored a message in stage %s", self._stage.name)
            return SessionOutput()
        in_panel = self._stage in _PANEL_STAGES
        if isinstance(message, bytes) and not in_panel:
            message = message.decode("utf-8", errors="replace")
        if isinstance(message, str):
            if cloud.is_heartbeat(message):
                return self._on_cloud_heartbeat()
            reply = cloud.parse_reply(message)
            if reply is not None:
                return self._on_reply(reply, in_panel=in_panel)
            if not in_panel:
                _LOGGER.debug("Ignored a non-reply message of %d chars", len(message))
                return SessionOutput()
            message = message.encode("utf-8", errors="replace")
        return self._on_panel_bytes(message)

    def send_command(self, payload: bytes) -> SessionOutput:
        """Send an application command to the panel.

        Args:
            payload: A payload from :mod:`pyronix_cloud.commands`.

        Returns:
            The ``D`` frame to send.

        Raises:
            NotConnectedError: The session is not :attr:`Stage.READY`.
        """
        if self._stage is not Stage.READY:
            msg = f"cannot send a command in stage {self._stage.name}"
            raise errors.NotConnectedError(msg)
        return SessionOutput(outbound=(wrap(self._link.send_data(payload)),))

    def close(self) -> SessionOutput:
        """End the session; safe to call in any stage, and more than once.

        Returns:
            The goodbye frame (``E`` after key agreement, ``F`` during the
            handshake, none otherwise) and the move to :attr:`Stage.CLOSED`.
        """
        if self._stage is Stage.CLOSED:
            return SessionOutput()
        goodbye = self._link.goodbye()
        self._stage = Stage.CLOSED
        outbound = () if goodbye is None else (wrap(goodbye),)
        return SessionOutput(outbound, (StageChanged(Stage.CLOSED),))

    def _advance(self, stage: Stage, request: CloudRequest) -> SessionOutput:
        """Send a cloud request and move to ``stage``."""
        self._stage = stage
        _LOGGER.debug("Sending cloud request %s", request.name)
        message = cloud.build_request(self._identity, request)
        return SessionOutput((message,), (StageChanged(stage),))

    def _on_cloud_heartbeat(self) -> SessionOutput:
        """Answer a cloud heartbeat with an encrypted panel heartbeat in READY."""
        if self._stage is not Stage.READY:
            _LOGGER.debug("Cloud heartbeat in stage %s ignored", self._stage.name)
            return SessionOutput()
        return self.send_command(commands.heartbeat())

    def _on_reply(self, reply: cloud.CloudReply, *, in_panel: bool) -> SessionOutput:
        """Act on a cloud ``<e>`` reply."""
        if in_panel:
            cloud_stages.check_relay_reply(reply)
            _LOGGER.debug("Ignored cloud reply %r in a panel stage", reply.letter)
            return SessionOutput()
        step = cloud_stages.next_step(reply)
        if step is None:
            _LOGGER.debug("Ignored cloud reply %r", reply.letter)
            return SessionOutput()
        output = self._advance(*step)
        if self._stage is not Stage.HANDSHAKE:
            return output
        announce = wrap(self._link.start())
        return SessionOutput((*output.outbound, announce), output.events)

    def _on_panel_bytes(self, data: bytes) -> SessionOutput:
        """Feed panel bytes through the reassembler and the link."""
        outbound: list[str] = []
        events: list[SessionEvent] = []
        for raw in self._reassembler.feed(data):
            if self._stage is Stage.CLOSED:
                break
            frames, link_events = self._link.receive(raw)
            outbound += (wrap(frame) for frame in frames)
            for event in link_events:
                step = panel_stages.on_link_event(self._stage, event)
                self._stage = step.stage
                if step.send_login:
                    outbound.append(wrap(self._link.send_data(self._login)))
                events += step.events
        return SessionOutput(tuple(outbound), tuple(events))
