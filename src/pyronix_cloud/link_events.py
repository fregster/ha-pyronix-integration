# SPDX-FileCopyrightText: 2026 Paul Fryer
# SPDX-License-Identifier: LGPL-3.0-or-later
"""Events the encrypted panel link reports to its owner.

PROTOCOL.md §4.4, §4.8. :class:`pyronix_cloud.link.PanelLink` returns these from
``receive``, alongside the raw frames to send.
"""

from __future__ import annotations

from dataclasses import dataclass

from pyronix_cloud.announce import Announced


@dataclass(frozen=True, slots=True)
class KeyAgreed:
    """The session key is agreed; the link is in data mode."""


@dataclass(frozen=True, slots=True)
class PanelData:
    """A verified ``d`` frame."""

    payload: bytes


@dataclass(frozen=True, slots=True)
class PanelPhoto:
    """A verified ``g`` (PIR camera) frame."""

    payload: bytes


@dataclass(frozen=True, slots=True)
class PanelGoodbye:
    """The panel ended the session with ``e`` or ``f``."""

    frame_type: str


type LinkEvent = Announced | KeyAgreed | PanelData | PanelPhoto | PanelGoodbye
