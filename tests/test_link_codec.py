# SPDX-FileCopyrightText: 2026 Paul Fryer
# SPDX-License-Identifier: LGPL-3.0-or-later
"""Tests for pyronix_cloud.link_codec: frame numbering, sealing, opening, rewind."""

from __future__ import annotations

import pytest

from pyronix_cloud.frames import Frame, build_frame, open_frame
from pyronix_cloud.link_codec import LinkCodec

KEY = bytes(range(16))


def test_seal_numbers_frames_and_remembers_all_but_nacks() -> None:
    codec = LinkCodec()
    first = codec.seal("A", b"")
    assert first == build_frame("A", 1, b"", None)
    nack = codec.seal("C", b"a\x01\x00\x00\x00")
    assert nack == build_frame("C", 2, b"a\x01\x00\x00\x00", None)
    assert codec.last_sent == first


def test_seal_encrypts_with_the_key() -> None:
    codec = LinkCodec()
    codec.key = KEY
    raw = codec.seal("D", b"\x20")
    assert open_frame(raw, KEY, 1) == Frame("D", 1, b"\x20")
    assert codec.last_sent == raw


def test_tx_seq_wraps_at_32_bits() -> None:
    codec = LinkCodec()
    codec._tx_seq = 2**32 - 1
    assert codec.seal("A", b"") == build_frame("A", 0, b"", None)


@pytest.mark.parametrize(
    "raw",
    [
        b"",
        b"a\x01\x00",
        build_frame("a", 0, b"x", None),
        build_frame("a", 200, b"x", None),
        build_frame("a", 1, b"x", None)[:-1] + b"\x00",
        build_frame("d", 1, b"x", bytes(16)),
    ],
)
def test_open_rejects_bad_frames(raw: bytes) -> None:
    codec = LinkCodec()
    codec.key = KEY
    assert codec.open(raw) is None
    assert codec._rx_seq == 0


def test_open_accepts_in_sequence_and_rewind_allows_a_repeat() -> None:
    codec = LinkCodec()
    first = build_frame("a", 1, b"x", None)
    assert codec.open(first) == Frame("a", 1, b"x")
    assert codec.open(first) is None
    codec.rewind()
    assert codec.open(first) == Frame("a", 1, b"x")


def test_rewind_stops_at_zero() -> None:
    codec = LinkCodec()
    codec.rewind()
    assert codec._rx_seq == 0
