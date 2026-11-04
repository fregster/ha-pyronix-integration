# SPDX-FileCopyrightText: 2026 Paul Fryer
# SPDX-License-Identifier: LGPL-3.0-or-later
from __future__ import annotations

from types import MappingProxyType

import pytest

from pyronix_cloud.documents import parse_document
from pyronix_cloud.models import (
    AreaDocument,
    AreaStatus,
    FaultDocument,
    InputState,
    LogDocument,
    Output,
    OutputDocument,
    PanelFault,
    SetType,
    UnknownDocument,
)
from pyronix_cloud.panel_state import PanelSnapshot, PanelStateTracker
from pyronix_cloud.testing import dump


def _tracker_after_dump() -> PanelStateTracker:
    tracker = PanelStateTracker()
    for text in dump.login_dump():
        tracker.apply(parse_document(text))
    return tracker


def _apply_all(tracker: PanelStateTracker, texts: list[str]) -> list[bool]:
    return [tracker.apply(parse_document(text)) for text in texts]


def test_initial_snapshot_is_empty() -> None:
    snapshot = PanelStateTracker().snapshot
    assert snapshot == PanelSnapshot()
    assert snapshot.info is None
    assert snapshot.set_type is SetType.UNKNOWN
    assert dict(snapshot.areas) == {}
    assert snapshot.user is None
    assert not snapshot.dump_complete


def test_full_login_dump() -> None:
    snapshot = _tracker_after_dump().snapshot
    assert snapshot.info is not None
    assert snapshot.info.panel_id == dump.PANEL_ID
    assert snapshot.set_type is SetType.LEVEL
    assert sorted(snapshot.areas) == list(range(6))
    assert sorted(snapshot.inputs) == list(range(10))
    assert dict(snapshot.outputs) == {}
    assert snapshot.faults == ()
    assert snapshot.user is not None
    assert snapshot.user.set_areas == frozenset({0, 1, 2})
    assert snapshot.areas[0].name == "Full Set"
    assert snapshot.areas[3].status is AreaStatus.CANNOT_SET
    assert snapshot.inputs[9].name == "Garage Heat"
    assert snapshot.dump_complete


def test_dump_complete_only_after_user_document() -> None:
    tracker = PanelStateTracker()
    texts = dump.login_dump()
    changed = []
    for text in texts[:-1]:
        changed.append(tracker.apply(parse_document(text)))
        assert not tracker.snapshot.dump_complete
    # applevel is unknown and the empty fault list matches the initial state.
    assert changed == [True, False, True, True, True, False, True, True, False]
    assert tracker.apply(parse_document(texts[-1]))
    assert tracker.snapshot.dump_complete


def test_repeated_dump_changes_nothing() -> None:
    tracker = _tracker_after_dump()
    before = tracker.snapshot
    assert not any(_apply_all(tracker, dump.login_dump()))
    assert tracker.snapshot is before


def test_door_open_sequence() -> None:
    tracker = _tracker_after_dump()
    before = tracker.snapshot
    assert _apply_all(tracker, dump.door_open_sequence()) == [True, True, False]
    after = tracker.snapshot
    assert after.inputs[7].state is InputState.OPEN
    assert after.areas[0].status is AreaStatus.CAN_OVERRIDE
    assert after.areas[0].faults == ("Landing DT Open",)
    assert after.areas[0].not_ready
    assert len(after.areas) == 6
    assert len(after.inputs) == 10
    # The earlier snapshot is untouched.
    assert before.inputs[7].state is InputState.CLOSED
    assert before.areas[0].status is AreaStatus.UNSET


def test_door_close_sequence_restores_state() -> None:
    tracker = _tracker_after_dump()
    before = tracker.snapshot
    _apply_all(tracker, dump.door_open_sequence())
    opened = tracker.snapshot
    assert _apply_all(tracker, dump.door_close_sequence()) == [True, True, False]
    closed = tracker.snapshot
    assert closed.areas[0].status is AreaStatus.UNSET
    assert closed.areas[0].faults == ()
    assert closed.inputs[7].state is InputState.CLOSED
    assert closed == before
    assert opened.areas[0].status is AreaStatus.CAN_OVERRIDE


def test_snapshot_mappings_are_read_only() -> None:
    snapshot = _tracker_after_dump().snapshot
    assert isinstance(snapshot.areas, MappingProxyType)
    assert isinstance(snapshot.inputs, MappingProxyType)
    assert isinstance(snapshot.outputs, MappingProxyType)
    assert isinstance(PanelSnapshot().areas, MappingProxyType)
    with pytest.raises(TypeError):
        snapshot.areas[0] = snapshot.areas[1]  # type: ignore[index]  # proving immutability


def test_log_and_unknown_documents_do_not_change_state() -> None:
    tracker = _tracker_after_dump()
    assert not tracker.apply(LogDocument(()))
    assert not tracker.apply(UnknownDocument("applevel"))


def test_unknown_set_type_keeps_previous() -> None:
    tracker = _tracker_after_dump()
    assert not tracker.apply(
        AreaDocument(is_new=False, set_type=SetType.UNKNOWN, areas=())
    )
    assert tracker.snapshot.set_type is SetType.LEVEL
    assert tracker.apply(AreaDocument(is_new=False, set_type=SetType.AREA, areas=()))
    after: SetType = tracker.snapshot.set_type
    assert after is SetType.AREA


def test_faults_are_replaced() -> None:
    tracker = PanelStateTracker()
    fault = PanelFault("Mains fail", (0,))
    assert tracker.apply(FaultDocument((fault,)))
    assert tracker.snapshot.faults == (fault,)
    assert not tracker.apply(FaultDocument((fault,)))
    assert tracker.apply(FaultDocument(()))
    assert tracker.snapshot.faults == ()


def test_outputs_merge_by_number() -> None:
    def output(number: int, *, enabled: bool) -> Output:
        return Output(
            number=number,
            name=f"Output {number}",
            output_type="Latch",
            pulsed=False,
            status_text="",
            enabled=enabled,
        )

    tracker = PanelStateTracker()
    first = (output(1, enabled=False), output(2, enabled=False))
    assert tracker.apply(OutputDocument(is_new=True, outputs=first))
    assert tracker.apply(
        OutputDocument(is_new=False, outputs=(output(2, enabled=True),))
    )
    outputs = tracker.snapshot.outputs
    assert sorted(outputs) == [1, 2]
    assert not outputs[1].enabled
    assert outputs[2].enabled
