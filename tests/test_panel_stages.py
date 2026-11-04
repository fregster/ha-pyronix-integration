# SPDX-FileCopyrightText: 2026 Paul Fryer
# SPDX-License-Identifier: LGPL-3.0-or-later
"""Tests for pyronix_cloud.panel_stages: link events to stages and session events."""

from __future__ import annotations

import pytest

from pyronix_cloud.announce import Announced, ModemType
from pyronix_cloud.errors import AuthenticationError
from pyronix_cloud.link_events import KeyAgreed, PanelData, PanelGoodbye, PanelPhoto
from pyronix_cloud.panel_stages import PanelStep, on_link_event
from pyronix_cloud.session_events import (
    DocumentReceived,
    PanelHeartbeat,
    SessionEnded,
    Stage,
    StageChanged,
)


def test_announce_changes_nothing() -> None:
    event = Announced(ModemType.GSM, 1)
    assert on_link_event(Stage.HANDSHAKE, event) == PanelStep(Stage.HANDSHAKE)


def test_key_agreed_sends_the_login() -> None:
    assert on_link_event(Stage.HANDSHAKE, KeyAgreed()) == PanelStep(
        Stage.LOGGING_IN, (StageChanged(Stage.LOGGING_IN),), send_login=True
    )


def test_first_document_means_ready() -> None:
    assert on_link_event(Stage.LOGGING_IN, PanelData(b'{"a":1}')) == PanelStep(
        Stage.READY, (StageChanged(Stage.READY), DocumentReceived('{"a":1}'))
    )


def test_later_documents_and_heartbeats() -> None:
    step = on_link_event(Stage.READY, PanelData(b"{\xff}"))
    assert step == PanelStep(Stage.READY, (DocumentReceived("{�}"),))
    step = on_link_event(Stage.READY, PanelData(b" "))
    assert step == PanelStep(Stage.READY, (PanelHeartbeat(),))


@pytest.mark.parametrize(
    "event", [PanelData(b"\x01\x02"), PanelData(b""), PanelPhoto(b"jpg")]
)
def test_other_data_and_photos_are_ignored(event: PanelData | PanelPhoto) -> None:
    assert on_link_event(Stage.READY, event) == PanelStep(Stage.READY)


def test_goodbye_after_login_ends_the_session() -> None:
    assert on_link_event(Stage.READY, PanelGoodbye("f")) == PanelStep(
        Stage.CLOSED,
        (StageChanged(Stage.CLOSED), SessionEnded("panel sent goodbye 'f'")),
    )


def test_goodbye_in_reply_to_the_login_means_wrong_user_code() -> None:
    with pytest.raises(AuthenticationError, match="user code"):
        on_link_event(Stage.LOGGING_IN, PanelGoodbye("e"))
