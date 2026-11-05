# SPDX-FileCopyrightText: 2026 Paul Fryer
# SPDX-License-Identifier: LGPL-3.0-or-later
"""JSON documents a fake panel sends, kept in step with its current area states.

The login dump is replayed as given, except that area entries changed by set and
unset commands carry their new ``V``/``S``. Documents that are not valid JSON are
passed through untouched, so tests can feed a client malformed input.
"""

from __future__ import annotations

import json
from typing import TYPE_CHECKING, Any, Final

from pyronix_cloud.testing import dump

if TYPE_CHECKING:
    from collections.abc import Sequence

UNSET: Final = 0
SET: Final = 1
SETTING: Final = 3
STATUS_TEXT: Final = {
    UNSET: "Unset",
    SET: "Set",
    SETTING: "Setting",
    4: "Cannot Set",
    5: "Set Delayed",
    6: "Set Cancelled",
}
"""Display text sent with each area status the fake pushes."""

_DEFAULT_LOG_COUNT: Final = 10

type _Json = dict[str, Any]


def _json_object(text: str) -> _Json | None:
    """Return ``text`` parsed as a JSON object, or None."""
    try:
        value = json.loads(text)
    except ValueError:
        return None
    return value if isinstance(value, dict) else None


def _area_entries(obj: _Json | None) -> list[tuple[int, _Json]]:
    """Return ``(R, entry)`` for each well-formed ``Detail`` entry of an area doc."""
    entries = (
        obj.get("Detail") if obj is not None and obj.get("type") == "area" else None
    )
    if not isinstance(entries, list):
        return []
    return [
        (entry["R"], entry)
        for entry in entries
        if isinstance(entry, dict) and isinstance(entry.get("R"), int)
    ]


def _serialise(obj: _Json) -> str:
    """Serialise like the panel: compact separators, non-ASCII kept."""
    return json.dumps(obj, separators=(",", ":"), ensure_ascii=False)


def _status_text(status: int) -> str:
    """Return the display text for an area status."""
    return STATUS_TEXT.get(status, str(status))


class PanelDocuments:
    """The documents of one panel, with area overrides applied."""

    def __init__(self, documents: Sequence[str], areas: dict[int, int]) -> None:
        """Index the area entries of ``documents``.

        Args:
            documents: The login dump, in order.
            areas: Area status overrides (area number to ``V``); updated in
                place by :meth:`change_areas`.
        """
        self._documents = tuple(documents)
        self.areas = areas
        self._details = {
            number: entry
            for text in self._documents
            for number, entry in _area_entries(_json_object(text))
        }

    def login_dump(self) -> list[str]:
        """Return the login dump with the current area states."""
        return [self._current(text) for text in self._documents]

    def change_areas(self, areas: bytes, statuses: Sequence[int]) -> list[str]:
        """Apply each status to each known area and return one update per change.

        Args:
            areas: Area numbers from a set/unset command; unknown ones are
                skipped.
            statuses: The statuses to go through, in order.

        Returns:
            The pushed ``area`` documents, status by status, area by area.
        """
        updates: list[str] = []
        for status in statuses:
            for area in (a for a in areas if a in self._details):
                self.areas[area] = status
                entry = dict(self._details[area], V=status, S=_status_text(status))
                updates.append(dump.area_document([entry], is_new=0))
        return updates

    @staticmethod
    def log(count: int) -> str:
        """Return a ``log`` document with ``count`` invented entries (§6.6).

        Args:
            count: Entries requested; 0 means the app's default of 10.

        Returns:
            The document text.
        """
        count = count or _DEFAULT_LOG_COUNT
        entries = [
            {"D": f"04/11/26 08:{i % 60:02d}:00", "I": f"Test event {i + 1}"}
            for i in range(count)
        ]
        return _serialise({"type": "log", "Entries": count, "Detail": entries})

    def _current(self, text: str) -> str:
        """Return a dump document with the area overrides applied."""
        obj = _json_object(text)
        changed = False
        for number, entry in _area_entries(obj):
            if number in self.areas:
                status = self.areas[number]
                entry.update(V=status, S=_status_text(status))
                changed = True
        return _serialise(obj) if obj is not None and changed else text
