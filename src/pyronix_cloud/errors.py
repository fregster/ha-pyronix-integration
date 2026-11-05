# SPDX-FileCopyrightText: 2026 Paul Fryer
# SPDX-License-Identifier: LGPL-3.0-or-later
"""Exception hierarchy raised by the pyronix_cloud library.

Every error the library raises derives from :class:`PyronixError`. Messages never
contain the app password, the user code or session keys.
"""

from __future__ import annotations


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


class NotConnectedError(PyronixError):
    """A command was issued while no panel session is in data mode."""


class ConnectionClosedError(PyronixError):
    """The WebSocket or the panel session closed unexpectedly."""


class CommunicationError(PyronixError):
    """The cloud relay could not be reached (DNS, TCP, TLS or HTTP upgrade)."""


class PyronixTimeoutError(PyronixError):
    """An expected reply did not arrive in time."""


class CommandNotConfirmedError(PyronixTimeoutError):
    """A set/unset command was sent but the panel did not report the change."""
