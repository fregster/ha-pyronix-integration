# SPDX-FileCopyrightText: 2026 Paul Fryer
# SPDX-License-Identifier: LGPL-3.0-or-later
from __future__ import annotations

from dataclasses import replace
from types import MappingProxyType

import pytest

from pyronix_cloud.area_rules import (
    ARMED,
    DISARMED,
    FORCE_REFUSED,
    REFUSED,
    all_in,
    check_not_refused,
    check_permitted,
    check_settable,
    refusals,
    refused_area,
)
from pyronix_cloud.errors import NotPermittedError, SetRefusedError
from pyronix_cloud.models import Area, AreaStatus, User
from pyronix_cloud.panel_state import PanelSnapshot


def area(number: int, status: AreaStatus, faults: tuple[str, ...] = ()) -> Area:
    return Area(
        number=number,
        letter="A",
        name=f"Area {number}",
        status=status,
        status_text=status.name,
        last_set=None,
        last_unset=None,
        faults=faults,
    )


USER = User(name="Manager", set_areas=frozenset({0, 1}), unset_areas=frozenset({0}))
SNAPSHOT = PanelSnapshot(
    areas=MappingProxyType({0: area(0, AreaStatus.SET), 1: area(1, AreaStatus.UNSET)}),
    user=USER,
)


def test_all_in() -> None:
    assert all_in(SNAPSHOT, (0,), ARMED)
    assert not all_in(SNAPSHOT, (0, 1), ARMED)
    assert all_in(SNAPSHOT, (1,), DISARMED)
    assert not all_in(SNAPSHOT, (5,), DISARMED)  # unknown areas never match


def test_permitted_areas_pass() -> None:
    check_permitted(SNAPSHOT, (0, 1), set_areas=True)
    check_permitted(SNAPSHOT, (0,), set_areas=False)


def test_denied_areas_are_named() -> None:
    with pytest.raises(NotPermittedError, match=r"set areas \[2\]"):
        check_permitted(SNAPSHOT, (0, 2), set_areas=True)
    with pytest.raises(NotPermittedError, match=r"unset areas \[1\]"):
        check_permitted(SNAPSHOT, (1,), set_areas=False)


def test_no_user_permits_nothing() -> None:
    with pytest.raises(NotPermittedError):
        check_permitted(replace(SNAPSHOT, user=None), (0,), set_areas=True)


def snapshot_of(*areas: Area) -> PanelSnapshot:
    return replace(SNAPSHOT, areas=MappingProxyType({a.number: a for a in areas}))


def test_ready_areas_are_settable() -> None:
    ready = snapshot_of(
        area(0, AreaStatus.UNSET), area(1, AreaStatus.SET), area(2, AreaStatus.SETTING)
    )
    check_settable(ready, (0, 1, 2, 7))  # unknown areas are left to the panel


@pytest.mark.parametrize("status", [AreaStatus.CANNOT_SET, AreaStatus.CAN_OVERRIDE])
def test_blocked_area_is_not_settable(status: AreaStatus) -> None:
    blocked = area(1, status, ("Landing DT Open", "Hall DT Open"))
    with pytest.raises(SetRefusedError) as raised:
        check_settable(snapshot_of(area(0, AreaStatus.UNSET), blocked), (0, 1))
    assert raised.value.area is blocked
    assert raised.value.reason == "Landing DT Open, Hall DT Open"
    assert "Landing DT Open" in str(raised.value)


def test_reason_without_faults_is_the_status_text() -> None:
    assert SetRefusedError(area(0, AreaStatus.SET_CANCELLED)).reason == (
        "SET_CANCELLED"
    )
    no_text = replace(area(0, AreaStatus.CAN_OVERRIDE), status_text="")
    assert SetRefusedError(no_text).reason == "CAN_OVERRIDE"


@pytest.mark.parametrize(
    "status",
    [AreaStatus.CANNOT_SET, AreaStatus.CAN_OVERRIDE, AreaStatus.SET_CANCELLED],
)
def test_a_new_refusal_is_found(status: AreaStatus) -> None:
    before = snapshot_of(area(0, AreaStatus.UNSET))
    after = snapshot_of(area(0, status))
    assert refused_area(after, (0,), before, force=False) is after.areas[0]
    with pytest.raises(SetRefusedError):
        check_not_refused(after, (0,), before, force=False)


@pytest.mark.parametrize("status", [AreaStatus.CANNOT_SET, AreaStatus.SET_CANCELLED])
def test_a_new_refusal_ends_a_forced_set(status: AreaStatus) -> None:
    before = snapshot_of(area(0, AreaStatus.UNSET))
    after = snapshot_of(area(0, status))
    assert refused_area(after, (0,), before, force=True) is after.areas[0]
    with pytest.raises(SetRefusedError):
        check_not_refused(after, (0,), before, force=True)


def test_a_forced_set_overrides_can_override() -> None:
    before = snapshot_of(area(0, AreaStatus.UNSET))
    after = snapshot_of(area(0, AreaStatus.CAN_OVERRIDE))
    assert refused_area(after, (0,), before, force=True) is None
    check_not_refused(after, (0,), before, force=True)


def test_refusal_statuses_depend_on_force() -> None:
    assert refusals(force=False) == REFUSED
    assert refusals(force=True) == FORCE_REFUSED
    assert AreaStatus.CAN_OVERRIDE not in FORCE_REFUSED


def test_an_unchanged_status_is_not_a_new_refusal() -> None:
    stale = snapshot_of(area(0, AreaStatus.SET_CANCELLED))
    for force in (False, True):
        assert refused_area(stale, (0,), stale, force=force) is None
        check_not_refused(stale, (0,), stale, force=force)
    setting = snapshot_of(area(0, AreaStatus.SETTING))
    assert refused_area(setting, (0, 9), stale, force=False) is None
