# SPDX-FileCopyrightText: 2026 Paul Fryer
# SPDX-License-Identifier: LGPL-3.0-or-later
"""Tests for pyronix_cloud.frames: layout, round trips, tamper and seq rules."""

from __future__ import annotations

import hashlib

from hypothesis import given
from hypothesis import strategies as st
import pytest

from pyronix_cloud import crypto, frames
from pyronix_cloud.errors import FrameError

KEY = bytes(range(16))
ALL_TYPES = "ABCDEFGabcdefg"
UINT32 = st.integers(min_value=0, max_value=0xFFFFFFFF)
SEQ16 = st.integers(min_value=0, max_value=0xFFFF)

# --------------------------------------------------------------------------
# Layout
# --------------------------------------------------------------------------


def test_constants() -> None:
    assert frames.HEADER_LEN == 5
    assert frames.MIN_FRAME_LEN == 21
    assert frozenset("DdEeGg") == frames.ENCRYPTED_TYPES


def test_build_frame_layout_and_check() -> None:
    raw = frames.build_frame("A", 1, b"", None)
    assert len(raw) == 21
    assert raw[:5] == b"A\x01\x00\x00\x00"
    assert raw[5:] == hashlib.sha256(raw[:5]).digest()[:16]
    raw = frames.build_frame("b", 0x1_0203, b"xyz", None)
    assert raw[:5] == b"b\x03\x02\x03\x00"  # seq low 16 bits LE, len LE
    assert raw[5:8] == b"xyz"
    assert len(raw) == 3 + 21


def test_build_frame_encrypts_body_only() -> None:
    plain = frames.build_frame("A", 42, b"hello", None)
    enc = frames.build_frame("D", 42, b"hello", KEY)
    assert enc[:5] == b"D" + plain[1:5]
    body = crypto.cfb_decrypt(KEY, ord("D"), 5, 42, enc[5:])
    assert body == b"hello" + crypto.check16(enc[:5] + b"hello")


def test_build_frame_nonce_uses_full_seq() -> None:
    low = frames.build_frame("D", 3, b"x", KEY)
    high = frames.build_frame("D", 0x1_0003, b"x", KEY)
    assert low[:5] == high[:5]
    assert low != high
    assert frames.open_frame(high, KEY, 0x1_0003).payload == b"x"


@pytest.mark.parametrize(
    ("frame_type", "payload", "key"),
    [
        ("", b"", None),
        ("AB", b"", None),
        ("é", b"", None),
        ("A", bytes(0x10000), None),
        ("D", b"", None),
        ("g", b"", None),
    ],
)
def test_build_frame_rejects_programmer_errors(
    frame_type: str, payload: bytes, key: bytes | None
) -> None:
    with pytest.raises(ValueError, match=r"type|payload|key"):
        frames.build_frame(frame_type, 1, payload, key)


def test_build_frame_max_payload() -> None:
    raw = frames.build_frame("d", 1, bytes(0xFFFF), KEY)
    assert raw[3:5] == b"\xff\xff"
    assert frames.open_frame(raw, KEY, 1).payload == bytes(0xFFFF)


def test_frame_type_and_header_seq() -> None:
    raw = frames.build_frame("d", 0xABCD, b"", KEY)
    assert frames.frame_type_of(raw) == "d"
    assert frames.header_seq(raw) == 0xABCD  # high byte >= 0x80 read unsigned
    assert frames.header_seq(frames.build_frame("a", 0x80FF, b"", None)) == 0x80FF


@pytest.mark.parametrize("func", [frames.frame_type_of, frames.header_seq])
def test_short_frame_header_access_raises(func: object) -> None:
    assert callable(func)
    with pytest.raises(FrameError, match="short"):
        func(bytes(20))


# --------------------------------------------------------------------------
# open_frame
# --------------------------------------------------------------------------


def test_open_frame_plain() -> None:
    raw = frames.build_frame("a", 7, b"\x00\x83TESTPANL", None)
    assert frames.open_frame(raw, None, 7) == frames.Frame("a", 7, b"\x00\x83TESTPANL")
    assert frames.open_frame(raw, KEY, 999).payload == b"\x00\x83TESTPANL"


def test_open_frame_errors() -> None:
    with pytest.raises(FrameError, match="short"):
        frames.open_frame(bytes(20), KEY, 1)
    enc = frames.build_frame("d", 1, b"{}", KEY)
    with pytest.raises(FrameError, match="key agreement"):
        frames.open_frame(enc, None, 1)
    with pytest.raises(FrameError, match="check"):
        frames.open_frame(enc, KEY, 2)  # wrong nonce seq
    with pytest.raises(FrameError, match="check"):
        frames.open_frame(enc, bytes(16), 1)  # wrong key (e.g. app password)


def test_open_frame_uses_actual_length_not_header_len() -> None:
    # A frame whose header len lies is still opened by its real length, as in Java.
    payload = b"abc"
    header = b"a\x01\x00\x09\x00"  # claims 9 bytes
    raw = header + payload + crypto.check16(header + payload)
    assert frames.open_frame(raw, None, 1).payload == payload


@given(
    frame_type=st.sampled_from(ALL_TYPES),
    seq=UINT32,
    payload=st.binary(max_size=300),
)
def test_build_open_round_trip(frame_type: str, seq: int, payload: bytes) -> None:
    raw = frames.build_frame(frame_type, seq, payload, KEY)
    assert len(raw) == len(payload) + frames.MIN_FRAME_LEN
    assert frames.frame_type_of(raw) == frame_type
    assert frames.header_seq(raw) == seq & 0xFFFF
    opened = frames.open_frame(raw, KEY, seq)
    assert opened == frames.Frame(type=frame_type, seq=seq, payload=payload)
    if frame_type not in frames.ENCRYPTED_TYPES:
        assert raw == frames.build_frame(frame_type, seq, payload, None)
        assert frames.open_frame(raw, None, seq).payload == payload


@given(
    raw=st.binary(max_size=200),
    key=st.none() | st.binary(min_size=16, max_size=16),
    seq=UINT32,
)
def test_open_frame_arbitrary_bytes_only_raises_frame_error(
    raw: bytes, key: bytes | None, seq: int
) -> None:
    try:
        frame = frames.open_frame(raw, key, seq)
    except FrameError:
        return
    assert len(frame.payload) == len(raw) - frames.MIN_FRAME_LEN


@given(
    raw=st.binary(min_size=21, max_size=100).map(lambda b: bytes([ord("d")]) + b[1:]),
    seq=UINT32,
)
def test_open_frame_random_encrypted_frames_fail_check(raw: bytes, seq: int) -> None:
    with pytest.raises(FrameError):
        frames.open_frame(raw, KEY, seq)


@given(
    frame_type=st.sampled_from(sorted(frames.ENCRYPTED_TYPES)),
    seq=UINT32,
    payload=st.binary(max_size=300),
    data=st.data(),
)
def test_flipping_any_body_byte_fails_check(
    frame_type: str, seq: int, payload: bytes, data: st.DataObject
) -> None:
    raw = bytearray(frames.build_frame(frame_type, seq, payload, KEY))
    index = data.draw(st.integers(min_value=frames.HEADER_LEN, max_value=len(raw) - 1))
    mask = data.draw(st.integers(min_value=1, max_value=255))
    raw[index] ^= mask
    with pytest.raises(FrameError, match="check"):
        frames.open_frame(bytes(raw), KEY, seq)


# --------------------------------------------------------------------------
# accept_panel_seq
# --------------------------------------------------------------------------


def java_accept(last: int, seq16: int) -> int | None:
    """checkDecryptMessage (PanelEncrypt.java:251-273) with an UNSIGNED seq16."""
    i4 = last & 0xFFFFFFFF
    seq = seq16
    if i4 != 0 or seq == 0:
        last16 = 0xFFFF & i4
        if seq < last16:
            if seq >= 128 or last16 <= 65408:
                return None
            seq = (seq | (((i4 & 0xFFFF0000) + 65536) & 0xFFFFFFFF)) & 0xFFFFFFFF
        elif seq <= last16 or ((seq - last16) & 0xFFFFFFFF) >= 128:
            return None
    elif seq >= 128:
        return None
    return seq


def test_seq_first_frame() -> None:
    assert frames.accept_panel_seq(0, 1) == 1
    assert frames.accept_panel_seq(0, 127) == 127
    assert frames.accept_panel_seq(0, 128) is None
    assert frames.accept_panel_seq(0, 0) is None  # Java rejects seq 0


def test_seq_normal_window() -> None:
    assert frames.accept_panel_seq(5, 6) == 6
    assert frames.accept_panel_seq(5, 5) is None
    assert frames.accept_panel_seq(5, 4) is None
    assert frames.accept_panel_seq(5, 132) == 132
    assert frames.accept_panel_seq(5, 133) is None
    assert frames.accept_panel_seq(200, 0x80) is None  # behind
    assert frames.accept_panel_seq(127, 0x80) == 0x80  # low byte 0x80 accepted


def test_seq_wrap() -> None:
    assert frames.accept_panel_seq(65500, 3) == 0x10003
    assert frames.accept_panel_seq(65408, 3) is None  # last must be > 65408
    assert frames.accept_panel_seq(65409, 3) == 0x10003
    assert frames.accept_panel_seq(65500, 128) is None
    # Java quirk: after the wrap, the next frame's "full" seq loses the high bits.
    assert frames.accept_panel_seq(0x10003, 4) == 4
    assert frames.accept_panel_seq(0x1FFF0, 2) == 0x20002  # second wrap
    assert frames.accept_panel_seq(0xFFFFFFF0, 2) == 2  # 32-bit overflow wraps
    assert frames.accept_panel_seq(-16, 2) == 2  # signed Java int view of last


@given(last=UINT32, seq16=SEQ16)
def test_seq_matches_java_port(last: int, seq16: int) -> None:
    assert frames.accept_panel_seq(last, seq16) == java_accept(last, seq16)


@given(last=UINT32 | SEQ16, seq16=SEQ16)
def test_seq_invariants(last: int, seq16: int) -> None:
    full = frames.accept_panel_seq(last, seq16)
    if full is None:
        return
    assert full & 0xFFFF == seq16
    assert 0 <= full <= 0xFFFFFFFF
    last16 = last & 0xFFFF
    if last == 0:
        assert 1 <= full < 128
    elif seq16 > last16:
        assert full == seq16
        assert seq16 - last16 < 128
    else:  # the wrapping frame, the only one that gets high bits
        assert last16 > 65408
        assert seq16 < 128
        assert full == (((last & 0xFFFF0000) + 0x10000) | seq16) & 0xFFFFFFFF
