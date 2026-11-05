# SPDX-FileCopyrightText: 2026 Paul Fryer
# SPDX-License-Identifier: LGPL-3.0-or-later
"""Tests for pyronix_cloud.cloud_stages: reply letters to the next stage."""

from __future__ import annotations

import pytest

from pyronix_cloud.cloud import CloudReply, CloudRequest
from pyronix_cloud.cloud_stages import check_relay_reply, next_step
from pyronix_cloud.errors import (
    AccessDeniedError,
    CloudError,
    PanelBusyError,
    PanelOfflineError,
    SubscriptionExpiredError,
    UnknownCloudReplyError,
)
from pyronix_cloud.session_events import Stage

ERRORS = {
    "a": AccessDeniedError,
    "b": PanelBusyError,
    "i": SubscriptionExpiredError,
    "n": PanelOfflineError,
}


def reply(letter: str) -> CloudReply:
    return CloudReply(letter, "data")


@pytest.mark.parametrize(
    ("letter", "step"),
    [
        ("w", (Stage.REQUESTING_PASSWORD, CloudRequest.REQ_PWD)),
        ("m", (Stage.REQUESTING_PASSWORD, CloudRequest.REQ_PWD)),
        ("j", (Stage.CONNECTING_PANEL, CloudRequest.CONN_TO_CLOUD)),
        ("u", (Stage.HANDSHAKE, CloudRequest.CONN_TO_PANEL)),
        ("o", None),
        ("x", None),
    ],
)
def test_next_step(letter: str, step: tuple[Stage, CloudRequest] | None) -> None:
    assert next_step(reply(letter)) == step


@pytest.mark.parametrize(("letter", "error"), ERRORS.items())
def test_error_letters_raise(letter: str, error: type[CloudError]) -> None:
    with pytest.raises(error):
        next_step(reply(letter))
    with pytest.raises(error):
        check_relay_reply(reply(letter))


@pytest.mark.parametrize("letter", ["z", ""])
def test_unknown_letters(letter: str) -> None:
    with pytest.raises(UnknownCloudReplyError):
        next_step(reply(letter))
    check_relay_reply(reply(letter))


@pytest.mark.parametrize("letter", ["w", "m", "j", "u", "o", "x"])
def test_stage_letters_pass_in_the_relay(letter: str) -> None:
    check_relay_reply(reply(letter))
