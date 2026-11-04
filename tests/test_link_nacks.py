# SPDX-FileCopyrightText: 2026 Paul Fryer
# SPDX-License-Identifier: LGPL-3.0-or-later
"""Tests for pyronix_cloud.link_nacks: NACK limits and the error at the limit."""

from __future__ import annotations

import pytest

from pyronix_cloud.errors import (
    AuthenticationError,
    HandshakeError,
    ProtocolError,
    PyronixError,
)
from pyronix_cloud.link_nacks import MAX_CONSECUTIVE_NACK, NackCounter


@pytest.mark.parametrize(
    ("in_data", "verified", "error"),
    [
        (False, False, HandshakeError),
        (False, True, HandshakeError),
        (True, False, AuthenticationError),
        (True, True, ProtocolError),
    ],
)
def test_sent_limit_picks_the_error(
    *, in_data: bool, verified: bool, error: type[PyronixError]
) -> None:
    counter = NackCounter()
    counter.clear(data_verified=verified)
    for _ in range(MAX_CONSECUTIVE_NACK):
        counter.count_sent("bad frame", in_data=in_data)
    with pytest.raises(error) as caught:
        counter.count_sent("bad frame", in_data=in_data)
    assert type(caught.value) is error


@pytest.mark.parametrize(
    ("in_data", "error", "message"),
    [
        (
            False,
            HandshakeError,
            "panel handshake failed: the panel rejected our frames",
        ),
        (True, AuthenticationError, "app password rejected"),
    ],
)
def test_received_limit_is_passed_after_three(
    *, in_data: bool, error: type[PyronixError], message: str
) -> None:
    counter = NackCounter()
    for _ in range(MAX_CONSECUTIVE_NACK):
        counter.count_received(in_data=in_data)
    with pytest.raises(error) as caught:
        counter.count_received(in_data=in_data)
    assert str(caught.value) == message


def test_clear_resets_both_counts_and_keeps_verified() -> None:
    counter = NackCounter()
    counter.clear(data_verified=True)
    for _ in range(MAX_CONSECUTIVE_NACK):
        counter.count_sent("x", in_data=True)
        counter.count_received(in_data=True)
    counter.clear()
    for _ in range(MAX_CONSECUTIVE_NACK):
        counter.count_sent("x", in_data=True)
        counter.count_received(in_data=True)
    with pytest.raises(ProtocolError, match="too many consecutive NACKs: x"):
        counter.count_sent("x", in_data=True)
