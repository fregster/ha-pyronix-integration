# SPDX-FileCopyrightText: 2026 Paul Fryer
# SPDX-License-Identifier: LGPL-3.0-or-later
from __future__ import annotations

import dataclasses
from datetime import datetime

from hypothesis import given
from hypothesis import strategies as st
import pytest

from pyronix_cloud.models import (
    Area,
    AreaEvent,
    AreaStatus,
    InputState,
    PanelFault,
    SetType,
    User,
)


def _area(status: AreaStatus, faults: tuple[str, ...] = ()) -> Area:
    return Area(
        number=0,
        letter="A",
        name="Full Set",
        status=status,
        status_text="",
        last_set=AreaEvent("User 1", datetime(2025, 8, 5, 9, 17, 59)),  # noqa: DTZ001 - panel time is naive
        last_unset=None,
        faults=faults,
    )


@pytest.mark.parametrize(
    ("wire", "expected"),
    [
        (0, AreaStatus.UNSET),
        (1, AreaStatus.SET),
        (2, AreaStatus.ALARMED),
        (3, AreaStatus.SETTING),
        (4, AreaStatus.CANNOT_SET),
        (5, AreaStatus.CAN_OVERRIDE),
        (6, AreaStatus.SET_CANCELLED),
        (7, AreaStatus.UNKNOWN),
        (-1, AreaStatus.UNKNOWN),
        (99999, AreaStatus.UNKNOWN),
    ],
)
def test_area_status_from_wire(wire: int, expected: AreaStatus) -> None:
    assert AreaStatus.from_wire(wire) is expected


@pytest.mark.parametrize(
    ("wire", "expected"),
    [
        (0, InputState.CLOSED),
        (1, InputState.OPEN),
        (2, InputState.PROBLEM),
        (3, InputState.UNKNOWN),
        (-5, InputState.UNKNOWN),
    ],
)
def test_input_state_from_wire(wire: int, expected: InputState) -> None:
    assert InputState.from_wire(wire) is expected


@given(st.integers())
def test_from_wire_never_raises(value: int) -> None:
    assert isinstance(AreaStatus.from_wire(value), AreaStatus)
    assert isinstance(InputState.from_wire(value), InputState)


def test_set_type_values_match_the_wire() -> None:
    assert SetType("Level") is SetType.LEVEL
    assert SetType("Area") is SetType.AREA
    assert str(SetType.LEVEL) == "Level"


@pytest.mark.parametrize(
    ("status", "faults", "not_ready", "in_use"),
    [
        (AreaStatus.UNSET, (), False, True),
        (AreaStatus.UNSET, ("Landing DT Open",), True, True),
        (AreaStatus.CAN_OVERRIDE, ("Landing DT Open",), True, True),
        (AreaStatus.CAN_OVERRIDE, (), True, True),
        (AreaStatus.CANNOT_SET, (), False, False),
        (AreaStatus.SET, (), False, True),
    ],
)
def test_area_readiness(
    status: AreaStatus,
    faults: tuple[str, ...],
    *,
    not_ready: bool,
    in_use: bool,
) -> None:
    area = _area(status, faults)
    assert area.not_ready is not_ready
    assert area.in_use is in_use


def test_models_are_frozen() -> None:
    area = _area(AreaStatus.UNSET)
    with pytest.raises(dataclasses.FrozenInstanceError):
        area.name = "x"  # type: ignore[misc]  # proving immutability
    fault = PanelFault("Mains fail", (1,))
    with pytest.raises(dataclasses.FrozenInstanceError):
        fault.text = "x"  # type: ignore[misc]  # proving immutability


def test_models_compare_by_value() -> None:
    assert _area(AreaStatus.UNSET) == _area(AreaStatus.UNSET)
    assert _area(AreaStatus.UNSET) != _area(AreaStatus.SET)
    user = User(name="Manager", set_areas=frozenset({0}), unset_areas=frozenset())
    assert user == User(
        name="Manager", set_areas=frozenset({0}), unset_areas=frozenset()
    )
