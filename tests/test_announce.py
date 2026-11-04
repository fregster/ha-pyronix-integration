# SPDX-FileCopyrightText: 2026 Paul Fryer
# SPDX-License-Identifier: LGPL-3.0-or-later
"""Tests for pyronix_cloud.announce: modem byte, System ID and key extra bytes."""

from __future__ import annotations

from hypothesis import given
from hypothesis import strategies as st
import pytest

from pyronix_cloud.announce import Announced, ModemType, modem_type, parse_announce


@pytest.mark.parametrize(
    ("flags", "modem"),
    [
        (0x81, ModemType.USB),
        (0x82, ModemType.RS232),
        (0x83, ModemType.WAN_LAN),
        (0x84, ModemType.GSM),
        (0x85, ModemType.GPRS),
        (0x86, ModemType.PSTN),
        (0x80, ModemType.UNKNOWN),
        (0x87, ModemType.UNKNOWN),
        (0xFF, ModemType.UNKNOWN),
        (0x20, ModemType.WAN_LAN),
        (0x40, ModemType.WAN_LAN),
        (0x63, ModemType.WAN_LAN),
        (0x02, ModemType.GPRS),
        (0x03, ModemType.GPRS),
        (0x01, ModemType.GSM),
        (0x05, ModemType.GSM),
        (0x04, ModemType.PSTN),
        (0x00, ModemType.UNKNOWN),
        (0x18, ModemType.UNKNOWN),
    ],
)
def test_modem_type(flags: int, modem: ModemType) -> None:
    assert modem_type(flags) is modem


def announce(flags: int, tail: bytes) -> bytes:
    return b"\x07" + bytes([flags]) + b"TESTPANL" + tail


@pytest.mark.parametrize(
    ("flags", "tail", "extra"),
    [
        (0x83, b"", b""),
        (0x83, b"\x02" + bytes(11), b"\x02"),
        (0x83, b"\x01\x02\x03\x04\x05" + bytes(11), b"\x01\x02\x03\x04"),
        (0x83, b"\x01\x02" + bytes(5), b""),  # frame_len - 42 < 1: no extra
        (0x03, b"\x01\x02" + bytes(11), b""),  # bit 7 clear: no extra
    ],
)
def test_parse_announce_extra(flags: int, tail: bytes, extra: bytes) -> None:
    payload = announce(flags, tail)
    parsed = parse_announce(payload, len(payload) + 21)
    assert parsed.panel_id == b"TESTPANL"
    assert parsed.key_extra == extra
    assert parsed.announced == Announced(modem_type(flags), extra[0] if extra else 0)


@pytest.mark.parametrize("payload", [b"", b"\x07", b"\x07\x83TEST"])
def test_parse_short_announce(payload: bytes) -> None:
    parsed = parse_announce(payload, len(payload) + 21)
    assert parsed.panel_id == payload[2:10]
    assert parsed.key_extra == b""
    flags = payload[1] if len(payload) > 1 else 0
    assert parsed.announced.modem is modem_type(flags)


@given(payload=st.binary(max_size=80))
def test_parse_any_payload(payload: bytes) -> None:
    parsed = parse_announce(payload, len(payload) + 21)
    assert len(parsed.key_extra) <= 4
    assert 0 <= parsed.announced.json_version <= 255
