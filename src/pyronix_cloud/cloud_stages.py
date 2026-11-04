# SPDX-FileCopyrightText: 2026 Paul Fryer
# SPDX-License-Identifier: LGPL-3.0-or-later
"""Advance the cloud relay stages from ``<e>`` replies to the panel relay.

PROTOCOL.md §3.4. Sans-IO: each function maps a parsed reply to what the session
does next, and raises the cloud error for an error letter.
"""

from __future__ import annotations

import contextlib
from typing import Final

from pyronix_cloud import cloud, errors
from pyronix_cloud.cloud import CloudReply, CloudRequest
from pyronix_cloud.session_events import Stage

_NEXT: Final[dict[str, tuple[Stage, CloudRequest]]] = {
    "w": (Stage.REQUESTING_PASSWORD, CloudRequest.REQ_PWD),
    "m": (Stage.REQUESTING_PASSWORD, CloudRequest.REQ_PWD),
    "j": (Stage.CONNECTING_PANEL, CloudRequest.CONN_TO_CLOUD),
    "u": (Stage.HANDSHAKE, CloudRequest.CONN_TO_PANEL),
}


def next_step(reply: CloudReply) -> tuple[Stage, CloudRequest] | None:
    """Return the stage and request a reply leads to in the cloud stages.

    Args:
        reply: A parsed ``<e>`` reply received before the panel relay.

    Returns:
        The next stage and the request to send, or None for a reply that only
        acknowledges (``o``, ``x``).

    Raises:
        CloudError: The reply is an error letter or an unknown one.
    """
    cloud.check_reply(reply)
    return _NEXT.get(reply.letter)


def check_relay_reply(reply: CloudReply) -> None:
    """Check a reply that arrives once the panel relay is up.

    Only error letters matter here; unknown letters and stage letters are
    ignored, since the session has moved past them.

    Args:
        reply: A parsed ``<e>`` reply received in a panel stage.

    Raises:
        CloudError: The reply is a known error letter.
    """
    with contextlib.suppress(errors.UnknownCloudReplyError):
        cloud.check_reply(reply)
