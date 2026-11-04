# SPDX-FileCopyrightText: 2026 Paul Fryer
# SPDX-License-Identifier: LGPL-3.0-or-later
from __future__ import annotations

import copy
from datetime import datetime
import json
from typing import TYPE_CHECKING, Any

from hypothesis import given
from hypothesis import strategies as st
import pytest

from pyronix_cloud.documents import parse_document
from pyronix_cloud.errors import DocumentError
from pyronix_cloud.models import (
    Area,
    AreaDocument,
    AreaEvent,
    AreaStatus,
    FaultDocument,
    Input,
    InputDocument,
    InputState,
    LogDocument,
    LogEntry,
    Output,
    OutputDocument,
    PanelDocument,
    PanelFault,
    PanelInfo,
    SetType,
    UnknownDocument,
    User,
    UserDocument,
)
from pyronix_cloud.testing import dump

if TYPE_CHECKING:
    from collections.abc import Iterator

    from pyronix_cloud.models import Document

type Path = tuple[str | int, ...]

ALL_DUMP_TEXTS = [
    *dump.login_dump(),
    *dump.door_open_sequence(),
    *dump.door_close_sequence(),
]

LOG_TEXT = (
    '{"type":"log","Entries":2,"Detail":['
    '{"D":"04/11/26 08:00:00","I":"Area A set by Paul"},'
    '{"D":"00/00/00 00:00:00","I":"Power up"}]}'
)
OUTPUT_TEXT = (
    '{"type":"output","isnew":1,"Outputs":2,'
    '"Detail":[{"R":1,"N":"Garage Door","T":"Pulse","P":1,"S":"Off","E":0}]}'
)


def _dt(text: str) -> datetime:
    return datetime.strptime(text, "%d/%m/%y %H:%M:%S")  # noqa: DTZ007 - panel time is naive


def _parse_all(texts: list[str]) -> list[Document]:
    return [parse_document(text) for text in texts]


# --------------------------------------------------------------------------- dump


def test_login_dump_document_types() -> None:
    kinds = [type(doc) for doc in _parse_all(dump.login_dump())]
    assert kinds == [
        PanelDocument,
        UnknownDocument,
        AreaDocument,
        AreaDocument,
        AreaDocument,
        FaultDocument,
        InputDocument,
        InputDocument,
        OutputDocument,
        UserDocument,
    ]


def test_panel_document() -> None:
    doc = parse_document(dump.login_dump()[0])
    assert doc == PanelDocument(
        PanelInfo(
            panel_id="TESTPANL",
            site_name="",
            firmware="10.26",
            area_capacity=6,
            input_capacity=76,
            output_capacity=30,
        )
    )


def test_applevel_is_unknown() -> None:
    assert parse_document(dump.login_dump()[1]) == UnknownDocument("applevel")


def test_first_area_page() -> None:
    doc = parse_document(dump.login_dump()[2])
    assert doc == AreaDocument(
        is_new=True,
        set_type=SetType.LEVEL,
        areas=(
            Area(
                number=0,
                letter="A",
                name="Full Set",
                status=AreaStatus.UNSET,
                status_text="Unset",
                last_set=AreaEvent("User 1", _dt("05/08/25 09:17:59")),
                last_unset=AreaEvent("Manager", _dt("14/06/26 14:20:04")),
                faults=(),
            ),
            Area(
                number=1,
                letter="B",
                name="Night Set",
                status=AreaStatus.UNSET,
                status_text="Unset",
                last_set=AreaEvent("User 1", _dt("05/08/25 09:17:59")),
                last_unset=AreaEvent("Manager", _dt("14/06/26 14:20:04")),
                faults=(),
            ),
        ),
    )


def test_unused_areas_cannot_set_and_never_set() -> None:
    docs = _parse_all(dump.login_dump()[3:5])
    areas = [
        area for doc in docs if isinstance(doc, AreaDocument) for area in doc.areas
    ]
    assert [(a.number, a.letter) for a in areas] == [
        (2, "C"),
        (3, "D"),
        (4, "0"),
        (5, "1"),
    ]
    area3 = areas[1]
    assert area3.status is AreaStatus.CANNOT_SET
    assert area3.status_text == "Cannot Set"
    assert area3.last_set == AreaEvent("Engineer", None)
    assert area3.faults == ()
    assert not area3.in_use


def test_fault_document_empty() -> None:
    assert parse_document(dump.login_dump()[5]) == FaultDocument(())


def test_input_pages() -> None:
    first, second = _parse_all(dump.login_dump()[6:8])
    assert isinstance(first, InputDocument)
    assert isinstance(second, InputDocument)
    assert first.is_new
    assert [i.number for i in first.inputs] == list(range(8))
    assert [i.number for i in second.inputs] == [8, 9]
    assert first.inputs[0] == Input(
        number=0,
        name="Front Door",
        location="",
        input_type="Final Exit",
        areas=(0, 1),
        state=InputState.CLOSED,
        status_text="Closed",
        omittable=False,
        omitted=False,
    )
    assert first.inputs[2].areas == (0, 1, 2)
    assert first.inputs[2].omittable


def test_output_and_user_documents() -> None:
    output, user = _parse_all(dump.login_dump()[8:])
    assert output == OutputDocument(is_new=True, outputs=())
    assert user == UserDocument(
        User(
            name="Manager",
            set_areas=frozenset({0, 1, 2}),
            unset_areas=frozenset({0, 1, 2}),
        )
    )


def test_door_open_sequence() -> None:
    opened, not_ready, again = _parse_all(dump.door_open_sequence())
    assert opened == InputDocument(
        is_new=False,
        inputs=(
            Input(
                number=7,
                name="Landing DT",
                location="",
                input_type="Intruder",
                areas=(0,),
                state=InputState.OPEN,
                status_text="Open",
                omittable=True,
                omitted=False,
            ),
        ),
    )
    assert isinstance(not_ready, AreaDocument)
    assert not not_ready.is_new
    (area,) = not_ready.areas
    assert area.number == 0
    assert area.status is AreaStatus.CAN_OVERRIDE
    assert area.status_text == "Set Delayed"
    assert area.faults == ("Landing DT Open",)
    assert area.not_ready
    assert again == not_ready


def test_door_close_sequence() -> None:
    closed, ready, _ = _parse_all(dump.door_close_sequence())
    assert isinstance(closed, InputDocument)
    assert closed.inputs[0].state is InputState.CLOSED
    assert isinstance(ready, AreaDocument)
    (area,) = ready.areas
    assert area.status is AreaStatus.UNSET
    assert area.faults == ()
    assert not area.not_ready


# --------------------------------------------------------------------------- inline


def test_log_document() -> None:
    assert parse_document(LOG_TEXT) == LogDocument(
        (
            LogEntry(_dt("04/11/26 08:00:00"), "Area A set by Paul"),
            LogEntry(None, "Power up"),
        )
    )


def test_output_document_with_detail() -> None:
    assert parse_document(OUTPUT_TEXT) == OutputDocument(
        is_new=True,
        outputs=(
            Output(
                number=1,
                name="Garage Door",
                output_type="Pulse",
                pulsed=True,
                status_text="Off",
                enabled=False,
            ),
        ),
    )


def test_protocol_examples_with_booleans() -> None:
    area = parse_document(
        '{"type":"area","isnew":true,"Areas":1,"SetType":"Area","Detail":[{"R":1,'
        '"C":"A","N":"House","V":1,"LastSet":{"U":"Paul","D":"04/11/26 08:00:00"},'
        '"Faults":{"Q":1,"List":[{"I":"text"}]}}]}'
    )
    assert isinstance(area, AreaDocument)
    assert area.is_new
    assert area.set_type is SetType.AREA
    assert area.areas[0].status is AreaStatus.SET
    assert area.areas[0].status_text == ""
    assert area.areas[0].last_unset is None
    assert area.areas[0].faults == ("text",)
    inputs = parse_document(
        '{"type":"input","isnew":1,"Inputs":32,"Detail":[{"R":1,"C":0,'
        '"N":"Front Door","L":"Hall","A":{"Q":1,"R":[1]},"V":0,"O":true,"B":false}]}'
    )
    assert isinstance(inputs, InputDocument)
    assert inputs.inputs[0].location == "Hall"
    assert inputs.inputs[0].omittable
    assert not inputs.inputs[0].omitted


def test_fault_document_with_detail() -> None:
    doc = parse_document(
        '{"type":"fault","Faults":2,"Detail":[{"I":"Mains fail","A":{"Q":1,"R":[1]}},'
        '{"I":"Tamper"},{"I":"Battery","A":{"Q":0}}]}'
    )
    assert doc == FaultDocument(
        (
            PanelFault("Mains fail", (1,)),
            PanelFault("Tamper", ()),
            PanelFault("Battery", ()),
        )
    )
    assert parse_document('{"type":"fault","Faults":0}') == FaultDocument(())


def test_lenient_values() -> None:
    doc = parse_document(
        '{"type":"area","Detail":[{"R":9,"C":"X","N":" Pad ","V":42,"Extra":1,'
        '"LastSet":{"U":"u","D":"31/02/26 25:00:00"}}],"SetType":"Odd"}'
    )
    assert isinstance(doc, AreaDocument)
    assert not doc.is_new
    assert doc.set_type is SetType.UNKNOWN
    area = doc.areas[0]
    assert area.name == "Pad"
    assert area.status is AreaStatus.UNKNOWN
    assert area.last_set == AreaEvent("u", None)
    no_set_type = parse_document('{"type":"area","isnew":0,"Detail":[]}')
    assert no_set_type == AreaDocument(is_new=False, set_type=SetType.UNKNOWN, areas=())
    state = parse_document(
        '{"type":"input","Detail":[{"R":1,"N":"n","A":{"Q":0},"V":7,"O":2,"B":0}]}'
    )
    assert isinstance(state, InputDocument)
    assert state.inputs[0].state is InputState.UNKNOWN
    assert state.inputs[0].areas == ()
    assert state.inputs[0].omittable


# --------------------------------------------------------------------------- errors


@pytest.mark.parametrize(
    "text",
    [
        "",
        "not json",
        "[]",
        '"area"',
        "null",
        "{}",
        '{"type":1}',
        '{"type":"area"}',
        '{"type":"area","Detail":{}}',
        '{"type":"area","Detail":[1]}',
        '{"type":"area","Detail":[{"R":true,"C":"A","N":"n","V":0}]}',
        '{"type":"area","Detail":[{"R":"0","C":"A","N":"n","V":0}]}',
        '{"type":"area","Detail":[{"R":0,"C":1,"N":"n","V":0}]}',
        '{"type":"area","Detail":[{"R":0,"C":"A","N":"n","V":0,"Faults":[]}]}',
        '{"type":"area","Detail":[{"R":0,"C":"A","N":"n","V":0,"LastSet":{"U":"u"}}]}',
        '{"type":"area","isnew":"1","Detail":[]}',
        '{"type":"input","Detail":[{"R":1,"N":"n","A":{"R":[1.5]},"V":0,"O":1,"B":0}]}',
        '{"type":"input","Detail":[{"R":1,"N":"n","A":{"R":1},"V":0,"O":1,"B":0}]}',
        '{"type":"input","Detail":[{"R":1,"N":"n","A":[],"V":0,"O":1,"B":0}]}',
        '{"type":"input","Detail":[{"R":1,"N":"n","A":{},"V":0,"O":null,"B":0}]}',
        '{"type":"user","U":"x","SetAreas":{"R":[0]}}',
        '{"type":"log","Detail":[{"D":1,"I":"x"}]}',
        '{"type":"panel","Panel":"P","VersionHi":10,"VersionLo":2.5}',
        '{"type":"fault","Detail":[{"I":"x","A":1}]}',
        "[" * 100_000,
        "1" * 5000,
    ],
)
def test_bad_documents_raise_document_error(text: str) -> None:
    with pytest.raises(DocumentError):
        parse_document(text)


def _safe_parse(text: str) -> None:
    try:
        doc = parse_document(text)
    except DocumentError:
        return
    assert _is_typed(doc)


def _is_typed(value: object) -> bool:
    """No raw dict or list survives parsing."""
    if isinstance(value, (dict, list)):
        return False
    if isinstance(value, (tuple, frozenset)):
        return all(_is_typed(item) for item in value)
    slots: tuple[str, ...] = getattr(type(value), "__slots__", ())
    return all(_is_typed(getattr(value, name)) for name in slots)


@given(st.text())
def test_arbitrary_text_only_raises_document_error(text: str) -> None:
    _safe_parse(text)


JSON_VALUES = st.recursive(
    st.none() | st.booleans() | st.integers() | st.floats() | st.text(max_size=8),
    lambda children: (
        st.lists(children, max_size=4)
        | st.dictionaries(st.text(max_size=8), children, max_size=4)
    ),
    max_leaves=20,
)
TYPES = st.sampled_from(
    ["panel", "area", "input", "output", "user", "fault", "log", "applevel"]
)


@given(JSON_VALUES)
def test_arbitrary_json_only_raises_document_error(value: object) -> None:
    _safe_parse(json.dumps(value))


@given(TYPES, st.dictionaries(st.text(max_size=10), JSON_VALUES, max_size=6))
def test_arbitrary_typed_objects_only_raise_document_error(
    doc_type: str, body: dict[str, object]
) -> None:
    _safe_parse(json.dumps({**body, "type": doc_type}))


def _paths(node: object, prefix: Path = ()) -> Iterator[Path]:
    yield prefix
    if isinstance(node, dict):
        for key, child in node.items():
            yield from _paths(child, (*prefix, key))
    elif isinstance(node, list):
        for index, child in enumerate(node):
            yield from _paths(child, (*prefix, index))


def _mutate(doc: Any, path: Path, replacement: object, *, delete: bool) -> Any:  # noqa: ANN401 - raw JSON under test
    if not path:
        return replacement
    doc = copy.deepcopy(doc)
    parent = doc
    for step in path[:-1]:
        parent = parent[step]
    if delete:
        del parent[path[-1]]
    else:
        parent[path[-1]] = replacement
    return doc


@given(st.sampled_from(ALL_DUMP_TEXTS), st.data())
def test_mutated_dump_documents_only_raise_document_error(
    text: str, data: st.DataObject
) -> None:
    doc = json.loads(text)
    path = data.draw(st.sampled_from(list(_paths(doc))))
    delete = bool(path) and data.draw(st.booleans())
    replacement = data.draw(JSON_VALUES)
    _safe_parse(json.dumps(_mutate(doc, path, replacement, delete=delete)))


@pytest.mark.parametrize("text", ALL_DUMP_TEXTS)
def test_every_field_deleted_or_mistyped(text: str) -> None:
    doc = json.loads(text)
    for path in list(_paths(doc))[1:]:
        _safe_parse(json.dumps(_mutate(doc, path, None, delete=True)))
        wrongs: tuple[object, ...] = (None, True, 1, 1.5, "x", [], {})
        for wrong in wrongs:
            _safe_parse(json.dumps(_mutate(doc, path, wrong, delete=False)))
