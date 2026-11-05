# SPDX-FileCopyrightText: 2026 Paul Fryer
# SPDX-License-Identifier: LGPL-3.0-or-later
"""Sans-IO fake of one cloud relay session: the cloud stages, then the panel relay.

The replies follow the live captures of 2026-11-04 rather than the app's source:
the cloud answers ``MSG_SUBSCRIPTION`` with ``<e>j </e>`` (no ``w``), and
``MSG_CONN_TO_CLOUD`` with ``<e>u</e>``. Panel frames go back as ``(base64)``
text that may be batched, split into small messages and led by a space, as seen
live. Error replies do not close the socket (PROTOCOL.md §3.4).
"""

from __future__ import annotations

from dataclasses import dataclass
import re
from typing import TYPE_CHECKING, Final, Literal

from pyronix_cloud.cloud import CloudRequest
from pyronix_cloud.errors import ProtocolError
from pyronix_cloud.transport import FrameReassembler, android_b64decode, wrap

if TYPE_CHECKING:
    from collections.abc import Callable

    from pyronix_cloud.testing.fake_panel import FakePanel

REPLY_J: Final = "<e>j </e>"
REPLY_U: Final = "<e>u</e>"
UNKNOWN_REQUEST: Final = "unknown"
"""Recorded in :attr:`CloudSession.requests` for text that is not a request."""

_MIN_CHUNK: Final = 2
_KIND_TAG: Final = re.compile(r"<([wja])>")
_PASSWORD_TAG: Final = re.compile(r"<z>(.*?)</z>", re.DOTALL)


@dataclass(frozen=True, slots=True, kw_only=True)
class CloudBehaviour:
    """How the fake cloud relay answers and delivers.

    Attributes:
        error_letter: Reply ``<e>{letter} </e>`` instead of the normal reply at
            ``error_stage``; None for no error.
        error_stage: ``"subscription"`` or ``"connect"`` (``MSG_CONN_TO_CLOUD``).
        reject_password: Reply ``a`` when ``<z>`` differs from the panel's app
            password. Off by default: the real cloud's check is unknown.
        chunk_size: Split each outbound panel text into messages of this many
            characters (at least 2, so no piece looks like a heartbeat); None
            for no splitting.
        batch_frames: Join frames produced together into one text first.
        leading_space: Prefix the first panel message of a session with ``" "``.
        not_polling_after_end: Answer ``MSG_CONN_TO_CLOUD`` with ``n`` this many
            times after a session that reached the panel ends, as the live cloud
            sometimes does for ~35 s after a goodbye (reported by aiopyronix).

    Raises:
        ValueError: ``chunk_size`` is below 2 or ``error_letter`` is not one
            character.
    """

    error_letter: str | None = None
    error_stage: Literal["subscription", "connect"] = "subscription"
    reject_password: bool = False
    chunk_size: int | None = 7
    batch_frames: bool = True
    leading_space: bool = True
    not_polling_after_end: int = 0

    def __post_init__(self) -> None:
        """Validate the chunk size and the error letter."""
        if self.chunk_size is not None and self.chunk_size < _MIN_CHUNK:
            msg = "chunk_size must be at least 2"
            raise ValueError(msg)
        if self.error_letter is not None and len(self.error_letter) != 1:
            msg = "error_letter must be one character"
            raise ValueError(msg)


class SessionSlot:
    """The panel's single remote session, shared by every cloud session."""

    def __init__(self) -> None:
        """Start with the slot free."""
        self.holder: CloudSession | None = None
        self.refusals = 0
        """Connections still to refuse with ``n`` since the last holder left."""


def _request(message: str) -> tuple[CloudRequest | None, str]:
    """Return the kind of a ``(…)`` request and its XML text (§3.2-3.3)."""
    body = message.strip()
    if not (body.startswith("(") and body.endswith(")")):
        return None, ""
    body = body[1:-1]
    raw = body.startswith("<p>")
    if not raw:
        try:
            body = android_b64decode(body).decode("utf-8", "replace")
        except ValueError:
            return None, ""
    match = _KIND_TAG.search(body)
    if match is None:
        return None, body
    kinds = {"w": CloudRequest.SUBSCRIPTION, "j": CloudRequest.REQ_PWD}
    default = CloudRequest.CONN_TO_PANEL if raw else CloudRequest.CONN_TO_CLOUD
    return kinds.get(match.group(1), default), body


class CloudSession:
    """One client WebSocket session on the fake cloud relay."""

    def __init__(
        self,
        behaviour: CloudBehaviour,
        panel_factory: Callable[[], FakePanel],
        slot: SessionSlot,
        url: str,
    ) -> None:
        """Create a session in the cloud stages.

        Args:
            behaviour: How to answer and deliver.
            panel_factory: Makes the panel this session connects to.
            slot: The panel's single remote session, shared between sessions.
            url: The URL the client connected to.
        """
        self.behaviour = behaviour
        self.url = url
        self.requests: list[CloudRequest | str] = []
        self.panel: FakePanel | None = None
        self.closed_by_client = False
        self.ended = False
        self._factory = panel_factory
        self._slot = slot
        self._linked: FakePanel | None = None
        self._spoken = False
        self._reassembler = FrameReassembler()

    @property
    def relaying(self) -> bool:
        """Whether the session is in panel mode and still open."""
        return self._linked is not None and not self.ended

    def receive(self, message: str) -> list[str]:
        """Handle one text message from the client.

        Args:
            message: The WebSocket text.

        Returns:
            The text messages to send back, in order.
        """
        if self.ended:
            return []
        if self._linked is not None:
            return self._relay(self._linked, message)
        kind, xml = _request(message)
        self.requests.append(kind or UNKNOWN_REQUEST)
        if kind is CloudRequest.SUBSCRIPTION:
            return [self._error("subscription") or REPLY_J]
        if kind is CloudRequest.REQ_PWD:
            return [REPLY_J]
        if kind is CloudRequest.CONN_TO_CLOUD:
            return [self._connect(xml)]
        if kind is CloudRequest.CONN_TO_PANEL and self._slot.holder is self:
            self._linked = self.panel
        return []

    def push(self, text: str) -> list[str]:
        """Return the messages that carry a panel push of ``text``."""
        if self._linked is None or self.ended:
            return []
        return self._deliver(self._linked.push(text))

    def heartbeat(self) -> list[str]:
        """Return the cloud heartbeat ``" "`` for a session in panel mode."""
        return [" "] if self.relaying else []

    def end(self, *, by_client: bool) -> None:
        """Mark the session closed and free the panel. Idempotent.

        Args:
            by_client: True if the client closed the socket.
        """
        if self.ended:
            return
        self.ended = True
        self.closed_by_client = by_client
        if self._slot.holder is self:
            self._slot.holder = None
            self._slot.refusals = self.behaviour.not_polling_after_end

    def _error(self, stage: str) -> str | None:
        """Return the configured error reply for ``stage``, if any."""
        letter = self.behaviour.error_letter
        if letter is None or self.behaviour.error_stage != stage:
            return None
        return f"<e>{letter} </e>"

    def _connect(self, xml: str) -> str:
        """Answer ``MSG_CONN_TO_CLOUD``: an error, not polling, busy, or ``u``."""
        error = self._error("connect")
        if error is not None:
            return error
        if self._slot.refusals > 0:
            self._slot.refusals -= 1
            return "<e>n </e>"
        if self.panel is None:
            self.panel = self._factory()
        match = _PASSWORD_TAG.search(xml)
        password = match.group(1) if match else None
        if (
            self.behaviour.reject_password
            and password != self.panel.behaviour.app_password
        ):
            return "<e>a </e>"
        if self._slot.holder is not None and self._slot.holder is not self:
            return "<e>b </e>"
        self._slot.holder = self
        return REPLY_U

    def _relay(self, panel: FakePanel, message: str) -> list[str]:
        """Feed client text to the panel and return its replies."""
        try:
            frames = self._reassembler.feed(message.encode("utf-8"))
        except ProtocolError:
            return []
        replies = [out for raw in frames for out in panel.receive_frame(raw)]
        return self._deliver(replies)

    def _deliver(self, frames: list[bytes]) -> list[str]:
        """Encode panel frames the way the live cloud delivered them."""
        texts = [wrap(raw) for raw in frames]
        if self.behaviour.batch_frames and texts:
            texts = ["".join(texts)]
        if self.behaviour.leading_space and texts and not self._spoken:
            texts[0] = " " + texts[0]
        self._spoken = self._spoken or bool(texts)
        size = self.behaviour.chunk_size
        if size is None:
            return texts
        return [text[i : i + size] for text in texts for i in range(0, len(text), size)]
