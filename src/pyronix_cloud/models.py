# SPDX-FileCopyrightText: 2026 Paul Fryer
# SPDX-License-Identifier: LGPL-3.0-or-later
"""Typed, immutable models of panel state (PROTOCOL.md §6).

Every model is a frozen dataclass built by :mod:`pyronix_cloud.documents` from one
panel JSON document. Dates are panel-local and naive; a date the panel reports as
``00/00/00 00:00:00`` (never happened) is ``None``.
"""

from __future__ import annotations

from dataclasses import dataclass
import enum
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from datetime import datetime


class AreaStatus(enum.IntEnum):
    """Area state (``Detail[].V`` of an ``area`` document, ``OcpAreaStatus``)."""

    UNKNOWN = -1
    UNSET = 0
    SET = 1
    ALARMED = 2
    SETTING = 3
    CANNOT_SET = 4
    CAN_OVERRIDE = 5
    SET_CANCELLED = 6

    @classmethod
    def from_wire(cls, value: int) -> AreaStatus:
        """Return the member for a wire value, or ``UNKNOWN`` if there is none."""
        try:
            return cls(value)
        except ValueError:
            return cls.UNKNOWN


class InputState(enum.IntEnum):
    """Input state (``Detail[].V`` of an ``input`` document, ``OcpInput.State``)."""

    UNKNOWN = -1
    CLOSED = 0
    OPEN = 1
    PROBLEM = 2

    @classmethod
    def from_wire(cls, value: int) -> InputState:
        """Return the member for a wire value, or ``UNKNOWN`` if there is none."""
        try:
            return cls(value)
        except ValueError:
            return cls.UNKNOWN


class SetType(enum.StrEnum):
    """How the panel sets: by levels or by individual areas."""

    LEVEL = "Level"
    AREA = "Area"
    UNKNOWN = "Unknown"


@dataclass(frozen=True, slots=True)
class AreaEvent:
    """Who last set or unset an area, and when (``None`` if never)."""

    user: str
    when: datetime | None


@dataclass(frozen=True, slots=True, kw_only=True)
class PanelInfo:
    """Panel identity and capacity (``panel`` document)."""

    panel_id: str
    site_name: str
    firmware: str
    area_capacity: int
    input_capacity: int
    output_capacity: int


@dataclass(frozen=True, slots=True, kw_only=True)
class Area:
    """One area (``area`` document ``Detail`` entry)."""

    number: int
    letter: str
    name: str
    status: AreaStatus
    status_text: str
    last_set: AreaEvent | None
    last_unset: AreaEvent | None
    faults: tuple[str, ...]

    @property
    def not_ready(self) -> bool:
        """Whether setting would need an override or is blocked by faults."""
        return self.status is AreaStatus.CAN_OVERRIDE or bool(self.faults)

    @property
    def in_use(self) -> bool:
        """Whether the area is configured (unused areas report ``CANNOT_SET``)."""
        return self.status is not AreaStatus.CANNOT_SET


@dataclass(frozen=True, slots=True, kw_only=True)
class Input:
    """One input, or zone (``input`` document ``Detail`` entry)."""

    number: int
    name: str
    location: str
    input_type: str
    areas: tuple[int, ...]
    state: InputState
    status_text: str
    omittable: bool
    omitted: bool


@dataclass(frozen=True, slots=True, kw_only=True)
class Output:
    """One output, or automated control (``output`` document ``Detail`` entry)."""

    number: int
    name: str
    output_type: str
    pulsed: bool
    status_text: str
    enabled: bool


@dataclass(frozen=True, slots=True, kw_only=True)
class User:
    """The logged-in user and the areas it may set and unset (``user`` document)."""

    name: str
    set_areas: frozenset[int]
    unset_areas: frozenset[int]


@dataclass(frozen=True, slots=True)
class PanelFault:
    """One panel-wide fault and the areas it affects (``fault`` document)."""

    text: str
    areas: tuple[int, ...]


@dataclass(frozen=True, slots=True)
class LogEntry:
    """One event-log line (``log`` document ``Detail`` entry)."""

    when: datetime | None
    text: str


@dataclass(frozen=True, slots=True)
class PanelDocument:
    """A parsed ``panel`` document."""

    info: PanelInfo


@dataclass(frozen=True, slots=True)
class AreaDocument:
    """A parsed ``area`` document: a dump page or a pushed update."""

    is_new: bool
    set_type: SetType
    areas: tuple[Area, ...]


@dataclass(frozen=True, slots=True)
class InputDocument:
    """A parsed ``input`` document: a dump page or a pushed update."""

    is_new: bool
    inputs: tuple[Input, ...]


@dataclass(frozen=True, slots=True)
class OutputDocument:
    """A parsed ``output`` document."""

    is_new: bool
    outputs: tuple[Output, ...]


@dataclass(frozen=True, slots=True)
class UserDocument:
    """A parsed ``user`` document; the panel sends it last in the login dump."""

    user: User


@dataclass(frozen=True, slots=True)
class FaultDocument:
    """A parsed ``fault`` document."""

    faults: tuple[PanelFault, ...]


@dataclass(frozen=True, slots=True)
class LogDocument:
    """A parsed ``log`` document."""

    entries: tuple[LogEntry, ...]


@dataclass(frozen=True, slots=True)
class UnknownDocument:
    """A document whose ``type`` this library does not model, such as ``applevel``."""

    type: str


type Document = (
    PanelDocument
    | AreaDocument
    | InputDocument
    | OutputDocument
    | UserDocument
    | FaultDocument
    | LogDocument
    | UnknownDocument
)
