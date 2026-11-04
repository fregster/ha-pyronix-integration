# SPDX-FileCopyrightText: 2026 Paul Fryer
# SPDX-License-Identifier: LGPL-3.0-or-later
"""Canned panel JSON documents, shaped exactly like a live Euro 46 V10.26 capture.

The structure, field types, paging and ordering follow a real login dump and real
pushed updates (PROTOCOL.md §6, observed 2026-11-04). Identifiers, names and dates
are invented. Quirks kept on purpose:

* ``R`` numbers start at 0; area letters run A, B, C, D, 0, 1.
* ``isnew`` is an int; input ``O``/``B`` are ints, not booleans.
* ``panel.Inputs``/``input.Inputs`` give the panel's capacity, not the count sent.
* ``Faults`` may be ``{"Q": 0}`` with no ``List``; dates may be ``00/00/00 ...``.
* The dump order is panel, applevel, area x3 (two areas per page), fault, input
  pages, output, user. ``user`` always comes last.
"""

from __future__ import annotations

import json
from typing import Any

PANEL_ID = "TESTPANL"
NEVER = "00/00/00 00:00:00"

_AREAS: list[dict[str, Any]] = [
    {"C": "A", "N": "Full Set", "R": 0, "S": "Unset", "V": 0},
    {"C": "B", "N": "Night Set", "R": 1, "S": "Unset", "V": 0},
    {"C": "C", "N": "Garage", "R": 2, "S": "Unset", "V": 0},
    {"C": "D", "N": "Area D", "R": 3, "S": "Cannot Set", "V": 4},
    {"C": "0", "N": "Area 0", "R": 4, "S": "Cannot Set", "V": 4},
    {"C": "1", "N": "Area 1", "R": 5, "S": "Cannot Set", "V": 4},
]

_INPUTS: list[tuple[int, str, str, list[int], int]] = [
    # Columns: input number R, name N, type T, area numbers, omittable O.
    (0, "Front Door", "Final Exit", [0, 1], 0),
    (1, "Hall DT", "ER (Area FX)", [0], 1),
    (2, "Garage DT", "Intruder", [0, 1, 2], 1),
    (3, "Lounge DT", "Intruder", [0], 1),
    (4, "Kitchen DT", "Intruder", [0], 1),
    (5, "Back Door", "Intruder", [0, 1], 1),
    (6, "Dining DT", "Intruder", [0], 1),
    (7, "Landing DT", "Intruder", [0], 1),
    (8, "Bedroom Shock", "Intruder", [0, 1, 2], 1),
    (9, "Garage Heat", "Fire", [0, 1, 2], 0),
]


def _text(doc: dict[str, Any]) -> str:
    """Serialise like the panel: compact separators, non-ASCII kept."""
    return json.dumps(doc, separators=(",", ":"), ensure_ascii=False)


def area_detail(
    number: int,
    *,
    status: int | None = None,
    text: str | None = None,
    faults: list[str] | None = None,
) -> dict[str, Any]:
    """Return one ``Detail`` entry for area ``number``, optionally changed."""
    detail = dict(_AREAS[number])
    if status is not None:
        detail["V"] = status
    if text is not None:
        detail["S"] = text
    detail["LastSet"] = {"U": "User 1", "D": "05/08/25 09:17:59"}
    detail["LastUnSet"] = {"U": "Manager", "D": "14/06/26 14:20:04"}
    if number >= 3:  # noqa: PLR2004 - unused areas carry an empty fault block
        detail["LastSet"] = {"U": "Engineer", "D": NEVER}
        detail["Faults"] = {"Q": 0}
    if faults:
        detail["Faults"] = {"Q": len(faults), "List": [{"I": f} for f in faults]}
    return detail


def input_detail(number: int, *, open_: bool = False) -> dict[str, Any]:
    """Return one ``Detail`` entry for input ``number``."""
    r, name, kind, areas, omittable = _INPUTS[number]
    return {
        "R": r,
        "N": name,
        "L": " ",
        "T": kind,
        "A": {"Q": len(areas), "R": areas},
        "O": omittable,
        "B": 0,
        "S": "Open" if open_ else "Closed",
        "V": 1 if open_ else 0,
    }


def area_document(details: list[dict[str, Any]], *, is_new: int) -> str:
    """Return an ``area`` document (a dump page or a pushed update)."""
    return _text(
        {
            "type": "area",
            "isnew": is_new,
            "Areas": len(details),
            "SetType": "Level",
            "Detail": details,
        }
    )


def input_update(number: int, *, open_: bool) -> str:
    """Return a pushed ``input`` update for one input."""
    detail = input_detail(number, open_=open_)
    return _text({"type": "input", "isnew": 0, "Inputs": 1, "Detail": [detail]})


def area_update(
    number: int, *, status: int, text: str, faults: list[str] | None = None
) -> str:
    """Return a pushed ``area`` update for one area."""
    detail = area_detail(number, status=status, text=text, faults=faults)
    return area_document([detail], is_new=0)


def login_dump(panel_id: str = PANEL_ID) -> list[str]:
    """Return the documents a panel sends after a successful login, in order."""
    pages = [[area_detail(i), area_detail(i + 1)] for i in range(0, 6, 2)]
    inputs = [input_detail(i) for i in range(len(_INPUTS))]
    return [
        _text(
            {
                "type": "panel",
                "Panel": panel_id,
                "SiteName": " ",
                "Areas": 6,
                "Inputs": 76,
                "Outputs": 30,
                "VersionHi": 10,
                "VersionLo": 26,
                "Options": 1,
            }
        ),
        _text(
            {
                "type": "applevel",
                "level": 1,
                "lang": "GB",
                "panel": "Rev V10.26c     -000000000-46    ",
            }
        ),
        *(area_document(page, is_new=1) for page in pages),
        _text({"type": "fault", "Faults": 0, "Detail": []}),
        _text({"type": "input", "isnew": 1, "Inputs": 76, "Detail": inputs[:8]}),
        _text({"type": "input", "isnew": 1, "Inputs": 76, "Detail": inputs[8:]}),
        _text({"type": "output", "isnew": 1, "Outputs": 10, "Detail": []}),
        _text(
            {
                "type": "user",
                "Flexiset": 1,
                "U": "Manager",
                "SetAreas": {"Q": 3, "R": [0, 1, 2]},
                "UnsetAreas": {"Q": 3, "R": [0, 1, 2]},
            }
        ),
    ]


def door_open_sequence(input_number: int = 7) -> list[str]:
    """Return the pushes seen when an input in area A opens (each sent twice)."""
    name = _INPUTS[input_number][1]
    opened = input_update(input_number, open_=True)
    not_ready = area_update(0, status=5, text="Set Delayed", faults=[f"{name} Open"])
    return [opened, not_ready, not_ready]


def door_close_sequence(input_number: int = 7) -> list[str]:
    """Return the pushes seen when that input closes again (area re-sent twice)."""
    closed = input_update(input_number, open_=False)
    ready = area_update(0, status=0, text="Unset")
    return [closed, ready, ready]
