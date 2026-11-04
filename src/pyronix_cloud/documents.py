# SPDX-FileCopyrightText: 2026 Paul Fryer
# SPDX-License-Identifier: LGPL-3.0-or-later
"""Parse untrusted panel JSON text into typed documents (PROTOCOL.md §6).

This is the only module that touches raw panel JSON. Each field is read through a
small typed reader that raises :class:`~pyronix_cloud.errors.DocumentError` when the
field is missing or has the wrong JSON type, so nothing downstream sees raw dicts.
Flags (``isnew``, ``O``, ``B``, ``P``, ``E``) accept ints or booleans, because the
panel sends ints where the app's models declare booleans (§6.3 observations).
"""

from __future__ import annotations

from collections.abc import Callable, Mapping
from datetime import datetime
import json
from typing import Final

from pyronix_cloud import models
from pyronix_cloud.errors import DocumentError

type _Object = Mapping[str, object]

_DATE_FORMAT: Final = "%d/%m/%y %H:%M:%S"


def _get(obj: _Object, key: str) -> object:
    """Return a required field, or raise if it is missing."""
    if key not in obj:
        msg = f"missing field {key!r}"
        raise DocumentError(msg)
    return obj[key]


def _wrong(key: str, expected: str) -> DocumentError:
    """Return the error for a field of the wrong JSON type."""
    return DocumentError(f"field {key!r} is not {expected}")


def _as_int(value: object, key: str) -> int:
    """Return ``value`` as an integer (booleans are rejected), or raise."""
    if isinstance(value, bool) or not isinstance(value, int):
        raise _wrong(key, "an integer")
    return value


def _int(obj: _Object, key: str) -> int:
    """Read a required integer field."""
    return _as_int(_get(obj, key), key)


def _str(obj: _Object, key: str, default: str | None = None) -> str:
    """Read a string field with surrounding padding removed."""
    if default is not None and key not in obj:
        return default
    value = _get(obj, key)
    if not isinstance(value, str):
        raise _wrong(key, "a string")
    return value.strip()


def _flag(obj: _Object, key: str, *, default: bool | None = None) -> bool:
    """Read a flag sent as an int or a boolean; any non-zero int is true."""
    if default is not None and key not in obj:
        return default
    value = _get(obj, key)
    if not isinstance(value, int):  # bool is a subclass of int
        raise _wrong(key, "an integer or boolean")
    return value != 0


def _as_object(value: object, key: str) -> _Object:
    """Return ``value`` as a JSON object, or raise."""
    if not isinstance(value, dict):
        raise _wrong(key, "an object")
    return value


def _optional_object(obj: _Object, key: str) -> _Object | None:
    """Read an optional object field."""
    return _as_object(obj[key], key) if key in obj else None


def _list(obj: _Object, key: str) -> list[object]:
    """Read a required list field."""
    value = _get(obj, key)
    if not isinstance(value, list):
        raise _wrong(key, "a list")
    return value


def _objects(obj: _Object, key: str, *, required: bool = True) -> tuple[_Object, ...]:
    """Read a list of objects; an optional missing list is empty."""
    if not required and key not in obj:
        return ()
    return tuple(_as_object(item, key) for item in _list(obj, key))


def _numbers(obj: _Object, key: str, *, required: bool = True) -> tuple[int, ...]:
    """Read a ``{"Q": count, "R": [numbers]}`` block; ``R`` may be absent."""
    block = _as_object(_get(obj, key), key) if required else _optional_object(obj, key)
    if block is None or "R" not in block:
        return ()
    return tuple(_as_int(item, key) for item in _list(block, "R"))


def _date(text: str) -> datetime | None:
    """Parse a panel-local ``dd/MM/yy HH:mm:ss`` date; ``None`` if never or invalid."""
    try:
        return datetime.strptime(text, _DATE_FORMAT)  # noqa: DTZ007 - panel-local time
    except ValueError:
        return None


def _event(obj: _Object, key: str) -> models.AreaEvent | None:
    """Read an optional ``{"U": user, "D": date}`` block."""
    block = _optional_object(obj, key)
    if block is None:
        return None
    return models.AreaEvent(_str(block, "U"), _date(_str(block, "D")))


def _area_faults(obj: _Object) -> tuple[str, ...]:
    """Read an area's ``Faults`` block; ``List`` is absent when there are none."""
    block = _optional_object(obj, "Faults")
    if block is None:
        return ()
    return tuple(_str(e, "I") for e in _objects(block, "List", required=False))


def _area(obj: _Object) -> models.Area:
    """Build the model from one ``area`` ``Detail`` entry."""
    return models.Area(
        number=_int(obj, "R"),
        letter=_str(obj, "C"),
        name=_str(obj, "N"),
        status=models.AreaStatus.from_wire(_int(obj, "V")),
        status_text=_str(obj, "S", ""),
        last_set=_event(obj, "LastSet"),
        last_unset=_event(obj, "LastUnSet"),
        faults=_area_faults(obj),
    )


def _input(obj: _Object) -> models.Input:
    """Build the model from one ``input`` ``Detail`` entry."""
    return models.Input(
        number=_int(obj, "R"),
        name=_str(obj, "N"),
        location=_str(obj, "L", ""),
        input_type=_str(obj, "T", ""),
        areas=_numbers(obj, "A"),
        state=models.InputState.from_wire(_int(obj, "V")),
        status_text=_str(obj, "S", ""),
        omittable=_flag(obj, "O"),
        omitted=_flag(obj, "B"),
    )


def _output(obj: _Object) -> models.Output:
    """Build the model from one ``output`` ``Detail`` entry."""
    return models.Output(
        number=_int(obj, "R"),
        name=_str(obj, "N"),
        output_type=_str(obj, "T", ""),
        pulsed=_flag(obj, "P"),
        status_text=_str(obj, "S", ""),
        enabled=_flag(obj, "E"),
    )


def _set_type(obj: _Object) -> models.SetType:
    """Read ``SetType``; a missing or unknown value is ``UNKNOWN``."""
    try:
        return models.SetType(_str(obj, "SetType", models.SetType.UNKNOWN.value))
    except ValueError:
        return models.SetType.UNKNOWN


def _panel_document(obj: _Object) -> models.PanelDocument:
    """Parse a ``panel`` document."""
    firmware = f"{_int(obj, 'VersionHi')}.{_int(obj, 'VersionLo'):02d}"
    return models.PanelDocument(
        models.PanelInfo(
            panel_id=_str(obj, "Panel"),
            site_name=_str(obj, "SiteName", ""),
            firmware=firmware,
            area_capacity=_int(obj, "Areas"),
            input_capacity=_int(obj, "Inputs"),
            output_capacity=_int(obj, "Outputs"),
        )
    )


def _area_document(obj: _Object) -> models.AreaDocument:
    """Parse an ``area`` document."""
    areas = tuple(_area(d) for d in _objects(obj, "Detail"))
    return models.AreaDocument(
        _flag(obj, "isnew", default=False), _set_type(obj), areas
    )


def _input_document(obj: _Object) -> models.InputDocument:
    """Parse an ``input`` document."""
    inputs = tuple(_input(d) for d in _objects(obj, "Detail"))
    return models.InputDocument(_flag(obj, "isnew", default=False), inputs)


def _output_document(obj: _Object) -> models.OutputDocument:
    """Parse an ``output`` document."""
    outputs = tuple(_output(d) for d in _objects(obj, "Detail"))
    return models.OutputDocument(_flag(obj, "isnew", default=False), outputs)


def _user_document(obj: _Object) -> models.UserDocument:
    """Parse a ``user`` document."""
    return models.UserDocument(
        models.User(
            name=_str(obj, "U"),
            set_areas=frozenset(_numbers(obj, "SetAreas")),
            unset_areas=frozenset(_numbers(obj, "UnsetAreas")),
        )
    )


def _fault_document(obj: _Object) -> models.FaultDocument:
    """Parse a ``fault`` document; ``Detail`` and each ``A`` block are optional."""
    entries = _objects(obj, "Detail", required=False)
    faults = (
        models.PanelFault(_str(e, "I"), _numbers(e, "A", required=False))
        for e in entries
    )
    return models.FaultDocument(tuple(faults))


def _log_document(obj: _Object) -> models.LogDocument:
    """Parse a ``log`` document."""
    entries = _objects(obj, "Detail")
    return models.LogDocument(
        tuple(models.LogEntry(_date(_str(e, "D")), _str(e, "I")) for e in entries)
    )


_PARSERS: Final[Mapping[str, Callable[[_Object], models.Document]]] = {
    "panel": _panel_document,
    "area": _area_document,
    "input": _input_document,
    "output": _output_document,
    "user": _user_document,
    "fault": _fault_document,
    "log": _log_document,
}


def _load_object(text: str) -> _Object:
    """Decode JSON text that must hold an object."""
    try:
        value: object = json.loads(text)
    except (ValueError, RecursionError) as err:
        # ValueError covers JSONDecodeError and over-long integer literals.
        msg = "panel document is not valid JSON"
        raise DocumentError(msg) from err
    return _as_object(value, "document")


def parse_document(text: str) -> models.Document:
    """Parse one panel JSON document into a typed model.

    Args:
        text: The decrypted ``d`` payload, decoded as UTF-8.

    Returns:
        The typed document. A ``type`` this library does not model gives an
        :class:`~pyronix_cloud.models.UnknownDocument`.

    Raises:
        DocumentError: The text is not a JSON object, has no string ``type``, or a
            known document has a required field missing or of the wrong JSON type.
    """
    obj = _load_object(text)
    doc_type = _str(obj, "type")
    parser = _PARSERS.get(doc_type)
    if parser is None:
        return models.UnknownDocument(doc_type)
    return parser(obj)
