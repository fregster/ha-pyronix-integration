# SPDX-FileCopyrightText: 2026 Paul Fryer
# SPDX-License-Identifier: LGPL-3.0-or-later
"""Find what fake panels received beyond the read-only frames and commands (§4.1, §5).

The check is an allow-list: anything not known to be read-only counts as a state
change, so a new opcode cannot slip through unnoticed. Read-only are the handshake
(``A``, ``B``), NACKs (``C``), the login and command carrier ``D``, goodbyes
(``E``, ``F``), and the heartbeat (``0x20``) and log request (``0x35``) commands.
Set/unset areas (``0x2D``/``0x2F``), output operate (``0x31``), input omit
(``0x34``) and the take-photo ``G`` frame are state changes.
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Final

from pyronix_cloud import commands

if TYPE_CHECKING:
    from collections.abc import Iterable

    from pyronix_cloud.testing.fake_panel import FakePanel

READ_ONLY_FRAME_TYPES: Final = frozenset("ABCDEF")
READ_ONLY_OPCODES: Final = frozenset({commands.HEARTBEAT, commands.REQUEST_LOG})


def is_read_only(payload: bytes) -> bool:
    """Whether a data-mode command payload is on the read-only allow-list."""
    return bool(payload) and payload[0] in READ_ONLY_OPCODES


def state_commands(panels: Iterable[FakePanel]) -> list[bytes]:
    """Return every command payload the panels received that is not read-only."""
    return [
        payload
        for panel in panels
        for payload in panel.received_commands
        if not is_read_only(payload)
    ]


def state_changes(panels: Iterable[FakePanel]) -> list[str]:
    """Describe every frame type and command that is not read-only, in order.

    Args:
        panels: The fake panels to audit, e.g. :attr:`FakeCloud.panels`.

    Returns:
        One description per offending frame or command; empty if all were
        read-only.
    """
    found: list[str] = []
    for panel in panels:
        found += [
            f"frame {frame_type!r}"
            for frame_type in panel.received_frame_types
            if frame_type not in READ_ONLY_FRAME_TYPES
        ]
        found += [
            f"command 0x{payload[0]:02X}" if payload else "empty command"
            for payload in state_commands([panel])
        ]
    return found
