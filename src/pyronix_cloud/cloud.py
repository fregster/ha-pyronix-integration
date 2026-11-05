# SPDX-FileCopyrightText: 2026 Paul Fryer
# SPDX-License-Identifier: LGPL-3.0-or-later
"""Cloud relay control language: build `<p>` requests and parse `<e>` replies.

PROTOCOL.md §3.3-3.4.

Requests are ported from ``HomeControl.makeCloudMessage`` (field order exact, no
XML escaping, ``SaveAppPwd = 0`` so the app password travels in plain text inside
``<z>``). Replies are parsed from the ``<e>`` position and tolerate the short
forms seen live (``<e>u</e>``, ``<e>j </e>``).
"""

from __future__ import annotations

from dataclasses import dataclass, field, fields
import enum
import re
from typing import Final

from pyronix_cloud.errors import (
    AccessDeniedError,
    CloudError,
    PanelBusyError,
    PanelOfflineError,
    SubscriptionExpiredError,
    UnknownCloudReplyError,
)
from pyronix_cloud.transport import wrap

LIVE_URL: Final = "wss://app.pyronixcloud.com:443"
TEST_URL: Final = "wss://apptestlb.pyronixcloud.com:443"
APP_VERSION: Final = "6.03.001"
APP_TYPE: Final = 16
OS_NAME: Final = "Android"
OS_VERSION: Final = "14"
NETWORK_TYPE: Final = "Wifi"
APP_LANG: Final = "uk"
DEFAULT_PUSH_ID: Final = "ha-pyronix"

_REQUIRED_FIELDS: Final = ("app_user", "panel_id", "device_id", "push_id")
_FORBIDDEN_CHARS: Final = frozenset("<>&")
_REPLY_START: Final = "<e>"
_REPLY_END: Final = "</e>"
_SECRET_TAG: Final = re.compile(r"<z>.*?(?:</z>|\Z)", re.DOTALL)
_REDACTED: Final = "<z>[REDACTED]</z>"

_ERROR_LETTERS: Final[dict[str, type[CloudError]]] = {
    error.letter: error
    for error in (
        AccessDeniedError,
        PanelBusyError,
        SubscriptionExpiredError,
        PanelOfflineError,
    )
}
_OK_LETTERS: Final = frozenset("wmjuox")


class CloudRequest(enum.Enum):
    """The cloud control messages this library sends (``CloudMsgType``)."""

    SUBSCRIPTION = enum.auto()
    REQ_PWD = enum.auto()
    CONN_TO_CLOUD = enum.auto()
    CONN_TO_PANEL = enum.auto()


@dataclass(frozen=True, slots=True, kw_only=True)
class CloudIdentity:
    """The values that identify the app and the panel to the cloud relay.

    Raises:
        ValueError: A required field is empty, or a field contains ``<``, ``>``
            or ``&`` (the protocol does no XML escaping). The message names the
            field, never its value.
    """

    app_user: str
    panel_id: str
    app_password: str = field(repr=False)
    device_id: str
    push_id: str = DEFAULT_PUSH_ID
    system_name: str = "Home"

    def __post_init__(self) -> None:
        """Validate every field."""
        for name in _REQUIRED_FIELDS:
            if not getattr(self, name):
                msg = f"{name} must not be empty"
                raise ValueError(msg)
        for item in fields(self):
            if _FORBIDDEN_CHARS.intersection(getattr(self, item.name)):
                msg = f"{item.name} must not contain '<', '>' or '&'"
                raise ValueError(msg)


def _type_tags(identity: CloudIdentity, kind: CloudRequest) -> str:
    """Return the type-specific tags of a request (PROTOCOL.md §3.3)."""
    if kind is CloudRequest.SUBSCRIPTION:
        return f"<w>{identity.panel_id}</w>"
    if kind is CloudRequest.REQ_PWD:
        return f"<j>{identity.panel_id}</j>"
    return (
        f"<a>{identity.panel_id}</a><b>{identity.system_name}</b>"
        f"<z>{identity.app_password}</z>"
    )


def build_request(identity: CloudIdentity, kind: CloudRequest) -> str:
    """Build the WebSocket text of a cloud control request.

    The result contains the app password for the two connect requests; log it
    only through :func:`redact`, and never log the base64 form.

    Args:
        identity: The app and panel identity.
        kind: Which request to build.

    Returns:
        ``"(" + base64 + ")"`` for the first three kinds, ``"(" + XML + ")"``
        for :attr:`CloudRequest.CONN_TO_PANEL`.
    """
    xml = (
        f"<p><n>{identity.app_user}</n><u>{identity.push_id}</u>"
        f"{_type_tags(identity, kind)}"
        f"<i>{identity.device_id}</i><l>{APP_VERSION}</l><t>{APP_TYPE}</t>"
        f"<m>{OS_NAME}</m><v>{OS_VERSION}</v><c>{NETWORK_TYPE}</c>"
        f"<lang>{APP_LANG}</lang></p>"
    )
    if kind is CloudRequest.CONN_TO_PANEL:
        return f"({xml})"
    return wrap(xml.encode("utf-8"))


def redact(text: str) -> str:
    """Replace the content of every ``<z>…</z>`` tag with ``[REDACTED]``.

    An unterminated ``<z>`` is redacted to the end of the text.

    Args:
        text: Plain (not base64) request text.

    Returns:
        The text, safe to log.
    """
    return _SECRET_TAG.sub(_REDACTED, text)


@dataclass(frozen=True, slots=True)
class CloudReply:
    """A parsed ``<e>`` reply from the cloud relay."""

    letter: str
    data: str = field(repr=False)


def is_heartbeat(message: str) -> bool:
    """Tell whether a text message is the cloud heartbeat.

    Args:
        message: One inbound WebSocket text message.

    Returns:
        True only for a single whitespace character. Any other one-character
        message (such as a split-off ``)``) is frame data.
    """
    return len(message) == 1 and message.isspace()


def parse_reply(message: str) -> CloudReply | None:
    """Parse a cloud ``<e>`` reply.

    Parsing starts at ``<e>``, wherever it is. The letter is the next character;
    the data is what follows the letter and one separator character, up to
    ``</e>`` (or the end of the text if ``</e>`` is missing).

    Args:
        message: One inbound WebSocket text message.

    Returns:
        The reply (with an empty letter if nothing follows ``<e>``), or None if
        the message holds no ``<e>``.
    """
    start = message.find(_REPLY_START)
    if start < 0:
        return None
    start += len(_REPLY_START)
    end = message.find(_REPLY_END, start)
    body = message[start:] if end < 0 else message[start:end]
    return CloudReply(letter=body[:1], data=body[2:])


def check_reply(reply: CloudReply) -> None:
    """Raise the error that a cloud reply letter stands for.

    Args:
        reply: A parsed reply.

    Raises:
        CloudError: The matching subclass for ``a``, ``b``, ``i`` and ``n``.
        UnknownCloudReplyError: The letter is not one of ``a b i n w m j u o x``.
    """
    error = _ERROR_LETTERS.get(reply.letter)
    if error is not None:
        raise error(error.__doc__)
    if reply.letter not in _OK_LETTERS:
        msg = f"unknown cloud reply letter {reply.letter!r}"
        raise UnknownCloudReplyError(msg)
