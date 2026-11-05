# SPDX-FileCopyrightText: 2026 Paul Fryer
# SPDX-License-Identifier: LGPL-3.0-or-later
from __future__ import annotations

from hypothesis import given
from hypothesis import strategies as st
import pytest

from pyronix_cloud.backoff import Backoff


def test_delays_grow_and_cap() -> None:
    backoff = Backoff(initial=5, maximum=60, factor=2, jitter=0, rng=lambda: 0.5)
    assert [backoff.next_delay() for _ in range(6)] == [5, 10, 20, 40, 60, 60]
    assert backoff.failures == 6


def test_reset_starts_again() -> None:
    backoff = Backoff(initial=1, maximum=10, jitter=0)
    backoff.next_delay()
    backoff.next_delay()
    backoff.reset()
    assert backoff.failures == 0
    assert backoff.next_delay() == 1


def test_jitter_is_added() -> None:
    backoff = Backoff(initial=10, maximum=10, jitter=0.5, rng=lambda: 1.0)
    assert backoff.next_delay() == 15


@pytest.mark.parametrize(
    "kwargs",
    [
        {"initial": 0},
        {"initial": 10, "maximum": 5},
        {"factor": 0.5},
        {"jitter": -0.1},
        {"jitter": 1.5},
    ],
)
def test_rejects_bad_parameters(kwargs: dict[str, float]) -> None:
    settings = {"initial": 5.0, "maximum": 300.0, "factor": 2.0, "jitter": 0.1}
    settings.update(kwargs)
    with pytest.raises(ValueError, match="invalid"):
        Backoff(
            initial=settings["initial"],
            maximum=settings["maximum"],
            factor=settings["factor"],
            jitter=settings["jitter"],
        )


@given(
    initial=st.floats(0.1, 10),
    extra=st.floats(0, 600),
    jitter=st.floats(0, 1),
    rolls=st.lists(st.floats(0, 0.999), min_size=1, max_size=30),
)
def test_delay_is_always_bounded(
    initial: float, extra: float, jitter: float, rolls: list[float]
) -> None:
    maximum = initial + extra
    values = iter(rolls)
    backoff = Backoff(
        initial=initial, maximum=maximum, jitter=jitter, rng=lambda: next(values)
    )
    for _ in rolls:
        delay = backoff.next_delay()
        assert initial <= delay <= maximum * (1 + jitter)
