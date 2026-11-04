# SPDX-FileCopyrightText: 2026 Paul Fryer
# SPDX-License-Identifier: LGPL-3.0-or-later
"""Fold parsed panel documents into a de-duplicated snapshot of panel state.

The panel sends its state as a paged login dump and then pushes single-item
updates, repeating each change two or three times (PROTOCOL.md §6.3). The tracker
merges areas, inputs and outputs by number and reports a change only when the
resulting snapshot differs from the previous one.
"""

from __future__ import annotations

from dataclasses import dataclass, field, replace
from types import MappingProxyType
from typing import TYPE_CHECKING, Protocol

from pyronix_cloud.models import (
    Area,
    AreaDocument,
    FaultDocument,
    Input,
    InputDocument,
    Output,
    OutputDocument,
    PanelDocument,
    PanelFault,
    PanelInfo,
    SetType,
    User,
    UserDocument,
)

if TYPE_CHECKING:
    from collections.abc import Iterable, Mapping

    from pyronix_cloud.models import Document


class _Numbered(Protocol):
    """Anything identified by a panel number."""

    @property
    def number(self) -> int:
        """The panel's number for this item."""
        ...  # pragma: no cover - protocol stub


def _empty[T]() -> Mapping[int, T]:
    """Return a new, read-only empty mapping."""
    return MappingProxyType({})


def _merge[T: _Numbered](old: Mapping[int, T], items: Iterable[T]) -> Mapping[int, T]:
    """Return a read-only copy of ``old`` with ``items`` added or replaced by number."""
    merged = dict(old)
    merged.update((item.number, item) for item in items)
    return MappingProxyType(merged)


@dataclass(frozen=True, slots=True, kw_only=True)
class PanelSnapshot:
    """Immutable view of everything known about the panel."""

    info: PanelInfo | None = None
    set_type: SetType = SetType.UNKNOWN
    areas: Mapping[int, Area] = field(default_factory=_empty)
    inputs: Mapping[int, Input] = field(default_factory=_empty)
    outputs: Mapping[int, Output] = field(default_factory=_empty)
    user: User | None = None
    faults: tuple[PanelFault, ...] = ()
    dump_complete: bool = False


def _fold_area(snapshot: PanelSnapshot, document: AreaDocument) -> PanelSnapshot:
    """Merge an ``area`` document; an unknown set type keeps the previous one."""
    set_type = snapshot.set_type
    if document.set_type is not SetType.UNKNOWN:
        set_type = document.set_type
    areas = _merge(snapshot.areas, document.areas)
    return replace(snapshot, set_type=set_type, areas=areas)


def _fold(snapshot: PanelSnapshot, document: Document) -> PanelSnapshot:
    """Return ``snapshot`` updated with ``document``."""
    updated = snapshot
    match document:
        case PanelDocument():
            updated = replace(snapshot, info=document.info)
        case AreaDocument():
            updated = _fold_area(snapshot, document)
        case InputDocument():
            updated = replace(snapshot, inputs=_merge(snapshot.inputs, document.inputs))
        case OutputDocument():
            outputs = _merge(snapshot.outputs, document.outputs)
            updated = replace(snapshot, outputs=outputs)
        case UserDocument():
            updated = replace(snapshot, user=document.user, dump_complete=True)
        case FaultDocument():
            updated = replace(snapshot, faults=document.faults)
    return updated


class PanelStateTracker:
    """Accumulate panel documents into successive :class:`PanelSnapshot` values."""

    def __init__(self) -> None:
        """Start with an empty snapshot."""
        self._snapshot = PanelSnapshot()

    @property
    def snapshot(self) -> PanelSnapshot:
        """The current snapshot; never mutated by later calls to :meth:`apply`."""
        return self._snapshot

    def apply(self, document: Document) -> bool:
        """Fold one document into the state.

        Args:
            document: A parsed panel document.

        Returns:
            True only if the snapshot changed. Identical re-sends, ``log`` and
            unknown documents return False.
        """
        updated = _fold(self._snapshot, document)
        if updated == self._snapshot:
            return False
        self._snapshot = updated
        return True
