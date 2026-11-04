# SPDX-FileCopyrightText: 2026 Paul Fryer
# SPDX-License-Identifier: LGPL-3.0-or-later
"""Tests for pyronix_cloud.commands."""

from __future__ import annotations

from hypothesis import given
from hypothesis import strategies as st
import pytest

from pyronix_cloud.commands import (
    HEARTBEAT,
    REQUEST_LOG,
    SET_AREAS,
    UNSET_AREAS,
    heartbeat,
    login,
    request_log,
    set_areas,
    unset_areas,
)


def test_opcodes() -> None:
    assert (HEARTBEAT, SET_AREAS, UNSET_AREAS, REQUEST_LOG) == (0x20, 0x2D, 0x2F, 0x35)


@pytest.mark.parametrize("code", ["1", "1234", "00000000"])
def test_login(code: str) -> None:
    assert login(code) == b"," + code.encode() + b"\n"


@pytest.mark.parametrize(
    "code", ["", "123456789", "12a4", " 1234", "1234\n", "١٢٣٤", "12,4"]
)
def test_login_rejects_invalid_code_without_echoing_it(code: str) -> None:
    with pytest.raises(ValueError, match="ASCII digits") as info:
        login(code)
    if code:
        assert code not in str(info.value)


def test_heartbeat() -> None:
    assert heartbeat() == b"\x20"


def test_set_areas_force_flag_and_sorting() -> None:
    assert set_areas([3, 1]) == bytes([0x2D, 0x82, 1, 3])
    assert set_areas([3, 1], force=False) == bytes([0x2D, 0x02, 1, 3])
    assert set_areas(iter([0, 255])) == bytes([0x2D, 0x82, 0, 255])


def test_unset_areas() -> None:
    assert unset_areas([2]) == bytes([0x2F, 1, 2])
    assert unset_areas((5, 4, 1)) == bytes([0x2F, 3, 1, 4, 5])


def test_maximum_area_count() -> None:
    areas = range(127)
    assert set_areas(areas) == bytes([0x2D, 0xFF, *areas])
    assert unset_areas(areas) == bytes([0x2F, 127, *areas])


@pytest.mark.parametrize(
    ("areas", "match"),
    [
        ([], "between 1 and 127"),
        (range(128), "between 1 and 127"),
        ([1, 1], "distinct"),
        ([256], "between 0 and 255"),
        ([-1], "between 0 and 255"),
    ],
)
def test_invalid_areas(areas: list[int], match: str) -> None:
    with pytest.raises(ValueError, match=match):
        set_areas(areas)
    with pytest.raises(ValueError, match=match):
        unset_areas(areas)


@given(
    areas=st.sets(st.integers(0, 255), min_size=1, max_size=127),
    force=st.booleans(),
)
def test_set_areas_layout(areas: set[int], *, force: bool) -> None:
    payload = set_areas(areas, force=force)
    assert payload[0] == SET_AREAS
    assert payload[1] & 0x7F == len(areas)
    assert bool(payload[1] & 0x80) is force
    assert list(payload[2:]) == sorted(areas)


@pytest.mark.parametrize("count", [1, 10, 255])
def test_request_log(count: int) -> None:
    assert request_log(count) == bytes([0x35, count])


@pytest.mark.parametrize("count", [0, -1, 256])
def test_request_log_rejects_out_of_range(count: int) -> None:
    with pytest.raises(ValueError, match="between 1 and 255"):
        request_log(count)
