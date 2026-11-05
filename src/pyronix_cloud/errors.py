# SPDX-FileCopyrightText: 2026 Paul Fryer
# SPDX-License-Identifier: LGPL-3.0-or-later
"""Exception hierarchy raised by the pyronix_cloud library.

Every error the library raises derives from :class:`PyronixError`. Messages never
contain the app password, the user code or session keys.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from pyronix_cloud.models import Area
    from pyronix_cloud.panel_state import PanelSnapshot


class PyronixError(Exception):
    """Base class for every error raised by this library."""


class CloudError(PyronixError):
    """The cloud relay answered a control message with an error letter (§3.4)."""

    letter: str = "?"


class AccessDeniedError(CloudError):
    """Cloud reply ``a``: wrong System ID or app password, or access not allowed."""

    letter = "a"


class PanelBusyError(CloudError):
    """Cloud reply ``b``: another client holds the panel's single remote session."""

    letter = "b"


class SubscriptionExpiredError(CloudError):
    """Cloud reply ``i``: the panel's Pyronix Cloud subscription has lapsed."""

    letter = "i"


class PanelOfflineError(CloudError):
    """Cloud reply ``n``: the panel is not polling the cloud."""

    letter = "n"


class UnknownCloudReplyError(CloudError):
    """The cloud sent a reply letter this library does not know."""


class ProtocolError(PyronixError):
    """The peer broke the protocol (bad frame, too many NACKs, oversized data)."""


class FrameError(ProtocolError):
    """A single frame is malformed or failed its integrity check."""


class DocumentError(ProtocolError):
    """A panel JSON document could not be parsed into a typed model."""


class HandshakeError(ProtocolError):
    """The encrypted panel handshake failed (announce or key exchange)."""


class AuthenticationError(PyronixError):
    """The panel rejected the credentials.

    Raised when the panel ends the session right after login (wrong user code), or
    when its frames cannot be decrypted (wrong app password).
    """


class NotPermittedError(PyronixError):
    """The logged-in user may not set or unset the requested areas."""


class SetRefusedError(PyronixError):
    """The panel will not set an area: it is not ready, or it cancelled the set.

    Attributes:
        area: The area as the panel last reported it, with its faults.
    """

    def __init__(self, area: Area) -> None:
        """Record the refused area; the message names it and the reason."""
        self.area = area
        super().__init__(
            f"area {area.number} ({area.name}) cannot be set: {self.reason}"
        )

    @property
    def reason(self) -> str:
        """Why: the area's fault texts, else its status text or status name."""
        return (
            ", ".join(self.area.faults)
            or self.area.status_text
            or self.area.status.name
        )


class NotConnectedError(PyronixError):
    """A command was issued while no panel session is in data mode."""


class ConnectionClosedError(PyronixError):
    """The WebSocket or the panel session closed unexpectedly."""


class ConnectionSilentError(ConnectionClosedError):
    """Nothing arrived from the cloud relay for too long; the socket is presumed dead.

    A half-open socket raises no error of its own, so the client gives up after
    ``Timeouts.silence`` seconds without any inbound message.
    """


class CommunicationError(PyronixError):
    """The cloud relay could not be reached (DNS, TCP, TLS or HTTP upgrade)."""


class PyronixTimeoutError(PyronixError):
    """An expected reply did not arrive in time."""


class CommandInProgressError(PyronixError):
    """Another set/unset command is still running on the same client.

    Nothing was sent. The client runs one command at a time and refuses, rather
    than queues, a second one.
    """


class CommandNotConfirmedError(PyronixError):
    """A set/unset command may have reached the panel, but its outcome is unknown.

    Raised for any failure after the command was handed to the socket: the
    panel did not confirm in time, the session ended, or the send failed part
    way. The cause is chained (``__cause__``).

    **The command may have taken effect.** Callers must read the panel state
    (reconnect if needed) before acting again, and must not retry
    automatically: a repeated set or unset can arm or disarm a home the user
    did not mean to.

    Attributes:
        snapshot: The panel state when the wait ended, or None.
    """

    def __init__(self, message: str, snapshot: PanelSnapshot | None = None) -> None:
        """Record the message and the last known panel state."""
        super().__init__(message)
        self.snapshot = snapshot
