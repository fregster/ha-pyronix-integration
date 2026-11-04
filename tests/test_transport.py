# SPDX-FileCopyrightText: 2026 Paul Fryer
# SPDX-License-Identifier: LGPL-3.0-or-later
"""Tests for pyronix_cloud.transport."""

from __future__ import annotations

import base64

from hypothesis import given
from hypothesis import strategies as st
import pytest

from pyronix_cloud.errors import ProtocolError
from pyronix_cloud.transport import (
    MAX_BUFFER,
    FrameReassembler,
    android_b64decode,
    android_b64encode,
    wrap,
)


def make_frame(letter: str, seq: int, payload: bytes) -> bytes:
    """Build a frame by hand: header (type, seq LE, len LE) + payload + 16 bytes."""
    header = (
        letter.encode()
        + (seq & 0xFFFF).to_bytes(2, "little")
        + len(payload).to_bytes(2, "little")
    )
    return header + payload + bytes(range(16))


FRAME = make_frame("d", 7, b"payload bytes that make the base64 span two lines" * 2)


def collect(chunks: list[bytes]) -> list[bytes]:
    reassembler = FrameReassembler()
    frames: list[bytes] = []
    for chunk in chunks:
        frames.extend(reassembler.feed(chunk))
    return frames


# --- base64 -------------------------------------------------------------------


@pytest.mark.parametrize("size", [0, 1, 56, 57, 58, 114, 200])
def test_android_b64encode_matches_android_default(size: int) -> None:
    data = bytes(range(size))
    encoded = android_b64encode(data)
    assert all(len(line) <= 76 for line in encoded.split("\n"))
    assert encoded.endswith("\n") or not data
    assert base64.b64decode(encoded.replace("\n", "")) == data


def test_android_b64encode_exact_layout() -> None:
    assert android_b64encode(b"") == ""
    assert android_b64encode(b"abc") == "YWJj\n"
    assert android_b64encode(bytes(57)) == "A" * 76 + "\n"
    assert android_b64encode(bytes(58)) == "A" * 76 + "\nAA==\n"


@pytest.mark.parametrize("text", ["YW Jj\n", b"YWJj", "YW\r\nJj", "Y*W)J(j", "YWJjé"])
def test_android_b64decode_is_lenient(text: str | bytes) -> None:
    assert android_b64decode(text) == b"abc"


@pytest.mark.parametrize("text", ["A", "AB", "YWJ", "YQ==YQ=="])
def test_android_b64decode_rejects_bad_padding(text: str) -> None:
    with pytest.raises(ValueError, match="base64"):
        android_b64decode(text)


@given(st.binary(max_size=300))
def test_b64_round_trip(data: bytes) -> None:
    assert android_b64decode(android_b64encode(data)) == data


def test_wrap() -> None:
    assert wrap(b"abc") == "(YWJj\n)"
    assert wrap(b"") == "()"


# --- reassembly -----------------------------------------------------------------


def test_text_frame_split_at_every_point() -> None:
    wire = wrap(FRAME).encode()
    assert b"\n" in wire[1:-2]  # a multi-line base64 body
    for cut in range(len(wire) + 1):
        assert collect([wire[:cut], wire[cut:]]) == [FRAME], cut


def test_live_split_shape() -> None:
    first = make_frame("d", 1, b"one")
    second = make_frame("d", 2, b"two")
    w1, w2 = wrap(first).encode(), wrap(second).encode()
    # Live: "...tL)(ZAQ..." - the end of one frame and the start of the next.
    assert collect([w1[:-3], w1[-3:] + w2[:5], w2[5:]]) == [first, second]


def test_lone_closing_parenthesis_completes_frame() -> None:
    wire = wrap(FRAME).encode()
    reassembler = FrameReassembler()
    assert reassembler.feed(wire[:-1]) == []
    assert reassembler.feed(b")") == [FRAME]


def test_several_frames_in_one_feed() -> None:
    frames = [make_frame("d", n, bytes([n]) * n) for n in range(5)]
    wire = "".join(wrap(frame) for frame in frames).encode()
    assert collect([wire]) == frames


def test_leading_space_and_junk_are_skipped() -> None:
    assert collect([b" " + wrap(FRAME).encode()]) == [FRAME]
    assert collect([b"\r\n\x00XYZ" + wrap(FRAME).encode()]) == [FRAME]


def test_junk_only_is_dropped() -> None:
    reassembler = FrameReassembler()
    assert reassembler.feed(b"  XYZ\n") == []
    assert reassembler.feed(wrap(FRAME).encode()) == [FRAME]


@pytest.mark.parametrize("letter", list("abcdef"))
def test_raw_binary_frame(letter: str) -> None:
    frame = make_frame(letter, 3, b"raw")
    assert collect([frame]) == [frame]


def test_raw_frame_length_is_unsigned() -> None:
    frame = make_frame("d", 0xFFFF, bytes(200))  # len 200 = 0xC8 (negative if signed)
    assert frame[3] >= 0x80
    for cut in (1, 4, 5, 100, len(frame) - 1):
        assert collect([frame[:cut], frame[cut:]]) == [frame]


def test_raw_frame_waits_for_header() -> None:
    frame = make_frame("a", 0, b"")
    reassembler = FrameReassembler()
    assert reassembler.feed(frame[:4]) == []
    assert reassembler.feed(frame[4:20]) == []
    assert reassembler.feed(frame[20:]) == [frame]


def test_raw_and_text_frames_mixed() -> None:
    raw = make_frame("b", 1, b"key")
    wire = b" " + raw + wrap(FRAME).encode() + raw
    assert collect([wire]) == [raw, FRAME, raw]


@pytest.mark.parametrize("body", [b"A", b"AB", b"!!!", b"YQ==YQ=="])
def test_invalid_base64_yields_empty_frame(body: bytes) -> None:
    wire = b"(" + body + b")" + wrap(FRAME).encode()
    assert collect([wire]) == [b"", FRAME]


def test_empty_parentheses_yield_empty_frame() -> None:
    assert collect([b"()"]) == [b""]


def test_buffer_limit_on_unterminated_text() -> None:
    reassembler = FrameReassembler()
    assert reassembler.feed(b"(" + b"A" * (MAX_BUFFER - 1)) == []
    with pytest.raises(ProtocolError, match="without a complete panel frame"):
        reassembler.feed(b"A")
    # The buffer was discarded, so the stream can recover.
    assert reassembler.feed(wrap(FRAME).encode()) == [FRAME]


def test_buffer_limit_on_large_raw_frame() -> None:
    header = b"d\x00\x00\xff\xff"  # claims 65535 + 21 bytes
    reassembler = FrameReassembler()
    with pytest.raises(ProtocolError):
        reassembler.feed(header + bytes(MAX_BUFFER))


def test_buffer_limit_counts_only_leftover() -> None:
    wire = wrap(make_frame("d", 1, bytes(MAX_BUFFER * 3 // 4))).encode()
    assert len(wire) > MAX_BUFFER
    assert collect([wire]) == [make_frame("d", 1, bytes(MAX_BUFFER * 3 // 4))]


_frames = st.builds(
    make_frame,
    st.sampled_from("abcdef"),
    st.integers(0, 0xFFFF),
    st.binary(max_size=120),
)


@given(
    frames=st.lists(st.tuples(_frames, st.booleans()), max_size=8),
    cuts=st.lists(st.integers(0, 4000), max_size=10),
    lead=st.sampled_from([b"", b" ", b"\n"]),
)
def test_rechunked_stream_round_trips(
    frames: list[tuple[bytes, bool]], cuts: list[int], lead: bytes
) -> None:
    wire = lead + b"".join(
        wrap(frame).encode() if as_text else frame for frame, as_text in frames
    )
    points = sorted({min(cut, len(wire)) for cut in cuts})
    chunks = [
        wire[start:end]
        for start, end in zip([0, *points], [*points, len(wire)], strict=True)
    ]
    assert b"".join(chunks) == wire
    assert collect(chunks) == [frame for frame, _ in frames]
