# SPDX-FileCopyrightText: 2026 Paul Fryer
# SPDX-License-Identifier: LGPL-3.0-or-later
"""Settings of a client session: the panel credentials and the phase timeouts."""

from __future__ import annotations

from dataclasses import dataclass, field

from pyronix_cloud.cloud import DEFAULT_PUSH_ID


@dataclass(frozen=True, slots=True, kw_only=True)
class Credentials:
    """What the client needs to reach and log in to one panel.

    The app password and the user code are left out of ``repr``.
    """

    app_user: str
    panel_id: str
    app_password: str = field(repr=False)
    user_code: str = field(repr=False)
    device_id: str
    push_id: str = DEFAULT_PUSH_ID


@dataclass(frozen=True, slots=True, kw_only=True)
class Timeouts:
    """Seconds allowed for each phase of a session."""

    connect: float = 15.0
    """Opening the WebSocket."""
    step: float = 15.0
    """Each cloud and handshake stage."""
    dump: float = 10.0
    """The login dump, from the first document to the ``user`` document."""
    command: float = 15.0
    """The panel's confirmation of a set or unset command."""
    close: float = 5.0
    """The goodbye and the socket close."""
    silence: float = 130.0
    """Any inbound message, before the session is treated as dead.

    The cloud sends its heartbeat about every 51 s (PROTOCOL.md §3.4), so a
    healthy session is never this quiet: 130 s allows for about two missed or
    late heartbeats before a half-open socket is given up on. This is the
    library's own watchdog; the app only answers heartbeats and sends none.
    """
