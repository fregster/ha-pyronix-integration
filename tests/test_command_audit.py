# SPDX-FileCopyrightText: 2026 Paul Fryer
# SPDX-License-Identifier: LGPL-3.0-or-later
from __future__ import annotations

import pytest

from pyronix_cloud import commands
from pyronix_cloud.frames import build_frame
from pyronix_cloud.testing.command_audit import (
    is_read_only,
    state_changes,
    state_commands,
)
from pyronix_cloud.testing.fake_panel import FakePanel
from tests.test_fake_panel import App

STATE_CHANGING = {
    "set areas": commands.set_areas([0]),
    "unset areas": commands.unset_areas([0]),
    "operate output": bytes([0x31, 1, 1]),
    "omit input": bytes([0x34, 7, 1]),
    "validate code": bytes([0x2C]),
    "empty": b"",
}
READ_ONLY = {
    "heartbeat": commands.heartbeat(),
    "log request": commands.request_log(10),
}


def panel_with(*payloads: bytes) -> FakePanel:
    panel = FakePanel()
    panel.received_commands.extend(payloads)
    return panel


@pytest.mark.parametrize("payload", READ_ONLY.values(), ids=READ_ONLY.keys())
def test_read_only_commands_pass(payload: bytes) -> None:
    assert is_read_only(payload)
    assert state_changes([panel_with(payload)]) == []


@pytest.mark.parametrize("payload", STATE_CHANGING.values(), ids=STATE_CHANGING.keys())
def test_anything_else_is_a_state_change(payload: bytes) -> None:
    panel = panel_with(commands.heartbeat(), payload)
    assert not is_read_only(payload)
    assert state_commands([panel]) == [payload]
    (found,) = state_changes([panel])
    assert found == (f"command 0x{payload[0]:02X}" if payload else "empty command")


def test_take_photo_frame_is_a_state_change() -> None:
    panel, app = FakePanel(), App()
    app.login(panel)
    panel.receive_frame(app.frame("G", b"\x00\x07\x00"))
    assert panel.received_frame_types == ["A", "B", "D", "G"]
    assert state_changes([panel]) == ["frame 'G'"]
    assert state_commands([panel]) == []


def test_rejected_frames_are_not_recorded() -> None:
    panel = FakePanel()
    frame = bytearray(build_frame("A", 1, b"", None))
    frame[-1] ^= 1  # fails its check value
    (nack,) = panel.receive_frame(bytes(frame))
    assert nack[:1] == b"c"
    assert panel.received_frame_types == []
