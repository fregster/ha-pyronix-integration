# SPDX-FileCopyrightText: 2026 Paul Fryer
# SPDX-License-Identifier: LGPL-3.0-or-later
from __future__ import annotations

from dataclasses import replace
from types import MappingProxyType

import pytest

from pyronix_cloud.area_rules import ARMED, DISARMED, all_in, check_permitted
from pyronix_cloud.errors import NotPermittedError
from pyronix_cloud.models import Area, AreaStatus, User
from pyronix_cloud.panel_state import PanelSnapshot


def area(number: int, status: AreaStatus) -> Area:
    return Area(
        number=number,
        letter="A",
        name=f"Area {number}",
        status=status,
        status_text=status.name,
        last_set=None,
        last_unset=None,
        faults=(),
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
