# SPDX-FileCopyrightText: 2026 Paul Fryer
# SPDX-License-Identifier: LGPL-3.0-or-later
from __future__ import annotations

from typing import Any

import pytest

from pyronix_cloud.cloud import CloudIdentity, CloudRequest, build_request
from pyronix_cloud.testing import dump
from pyronix_cloud.testing.cloud_session import (
    REPLY_J,
    REPLY_U,
    UNKNOWN_REQUEST,
    CloudBehaviour,
    CloudSession,
    SessionSlot,
)
from pyronix_cloud.testing.fake_panel import FakePanel, PanelBehaviour
from pyronix_cloud.transport import MAX_BUFFER, wrap

IDENTITY = CloudIdentity(
    app_user="tester",
    panel_id=dump.PANEL_ID,
    app_password=PanelBehaviour().app_password,
    device_id="d1",
)


def session(behaviour: CloudBehaviour | None = None) -> CloudSession:
    return CloudSession(behaviour or CloudBehaviour(), FakePanel, SessionSlot(), "u")


def request(kind: CloudRequest) -> str:
    return build_request(IDENTITY, kind)


@pytest.mark.parametrize(
    "kwargs", [{"chunk_size": 1}, {"error_letter": ""}, {"error_letter": "ab"}]
)
def test_behaviour_validation(kwargs: dict[str, Any]) -> None:
    with pytest.raises(ValueError, match="must be"):
        CloudBehaviour(**kwargs)


@pytest.mark.parametrize(
    "message",
    [
        "no parentheses",
        "(!!!not base64!)",
        wrap(b"<p><n>x</n></p>"),
        "(<p><n>raw without a kind</n></p>)",
    ],
)
def test_unknown_requests_are_recorded_and_ignored(message: str) -> None:
    cloud = session()
    assert cloud.receive(message) == []
    assert cloud.requests == [UNKNOWN_REQUEST]


def test_request_kinds_and_replies() -> None:
    cloud = session()
    assert cloud.receive(request(CloudRequest.SUBSCRIPTION)) == [REPLY_J]
    assert cloud.receive(request(CloudRequest.REQ_PWD)) == [REPLY_J]
    # Connecting to the panel before the cloud said "u" does nothing.
    assert cloud.receive(request(CloudRequest.CONN_TO_PANEL)) == []
    assert cloud.heartbeat() == []
    assert cloud.receive(request(CloudRequest.CONN_TO_CLOUD)) == [REPLY_U]
    assert cloud.receive(request(CloudRequest.CONN_TO_PANEL)) == []
    assert cloud.heartbeat() == [" "]
    assert cloud.requests == [
        CloudRequest.SUBSCRIPTION,
        CloudRequest.REQ_PWD,
        CloudRequest.CONN_TO_PANEL,
        CloudRequest.CONN_TO_CLOUD,
        CloudRequest.CONN_TO_PANEL,
    ]


def test_ended_session_ignores_everything() -> None:
    cloud = session()
    cloud.receive(request(CloudRequest.CONN_TO_CLOUD))
    cloud.receive(request(CloudRequest.CONN_TO_PANEL))
    cloud.end(by_client=False)
    cloud.end(by_client=True)
    assert not cloud.closed_by_client
    assert cloud.receive(request(CloudRequest.SUBSCRIPTION)) == []
    assert cloud.push("{}") == []
    assert cloud.heartbeat() == []


def test_oversized_panel_text_is_dropped() -> None:
    cloud = session()
    cloud.receive(request(CloudRequest.CONN_TO_CLOUD))
    cloud.receive(request(CloudRequest.CONN_TO_PANEL))
    assert cloud.receive("(" + "A" * (MAX_BUFFER + 1)) == []
    assert cloud.heartbeat() == [" "]


@pytest.mark.parametrize(
    ("behaviour", "expected"),
    [
        (CloudBehaviour(), [" (YWFh\n", ")(YmJi\n", ")"]),
        (CloudBehaviour(chunk_size=None), [" (YWFh\n)(YmJi\n)"]),
        (
            CloudBehaviour(batch_frames=False, chunk_size=None),
            [" (YWFh\n)", "(YmJi\n)"],
        ),
        (
            CloudBehaviour(leading_space=False, chunk_size=4),
            ["(YWF", "h\n)(", "YmJi", "\n)"],
        ),
    ],
)
def test_delivery_shapes(behaviour: CloudBehaviour, expected: list[str]) -> None:
    cloud = session(behaviour)
    assert cloud._deliver([]) == []
    assert cloud._deliver([b"aaa", b"bbb"]) == expected
    assert not cloud._deliver([b"c"])[0].startswith(" ")
