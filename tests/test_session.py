# SPDX-FileCopyrightText: 2026 Paul Fryer
# SPDX-License-Identifier: LGPL-3.0-or-later
"""Tests for pyronix_cloud.session: cloud stages, login, documents and close.

The panel side is :class:`pyronix_cloud.testing.fake_panel.FakePanel`. The photo
and goodbye frames it never sends in data mode come from :func:`forge`.
"""

from __future__ import annotations

from dataclasses import replace
import logging

from hypothesis import given
from hypothesis import strategies as st
import pytest

from pyronix_cloud.cloud import CloudIdentity
from pyronix_cloud.commands import heartbeat, request_log
from pyronix_cloud.errors import (
    AccessDeniedError,
    AuthenticationError,
    CloudError,
    NotConnectedError,
    PanelBusyError,
    PanelOfflineError,
    PyronixError,
    SubscriptionExpiredError,
    UnknownCloudReplyError,
)
from pyronix_cloud.frames import build_frame, frame_type_of, header_seq
from pyronix_cloud.link import PanelLink
from pyronix_cloud.session import (
    DocumentReceived,
    PanelHeartbeat,
    ProtocolSession,
    SessionEnded,
    SessionEvent,
    SessionOutput,
    Stage,
    StageChanged,
)
from pyronix_cloud.testing.dump import PANEL_ID, login_dump
from pyronix_cloud.testing.fake_panel import FakePanel, PanelBehaviour
from pyronix_cloud.transport import android_b64decode, wrap

PASSWORD = "app-secret-pw"  # noqa: S105 - fake test value, not a real credential
OTHER_PASSWORD = "another-pw"  # noqa: S105 - fake test value
USER_CODE = "73519"
BEHAVIOUR = PanelBehaviour(app_password=PASSWORD, user_code=USER_CODE)
IDENTITY = CloudIdentity(
    app_user="user@example.com",
    panel_id=PANEL_ID,
    app_password=PASSWORD,
    device_id="device-0001",
)
CLOUD_STAGES = [Stage.SUBSCRIBING, Stage.REQUESTING_PASSWORD, Stage.CONNECTING_PANEL]
PANEL_STAGES = [Stage.HANDSHAKE, Stage.LOGGING_IN, Stage.READY]
ERRORS = {
    "a": AccessDeniedError,
    "b": PanelBusyError,
    "i": SubscriptionExpiredError,
    "n": PanelOfflineError,
}


def new_session(user_code: str = USER_CODE) -> ProtocolSession:
    return ProtocolSession(IDENTITY, user_code, link_factory=PanelLink)


def forge(panel: FakePanel, frame_type: str, payload: bytes = b"") -> bytes:
    """Build the fake panel's next frame with any type and payload.

    The fake has no public way to send these, so this reaches into its private
    ``_send`` (next seq and session key).
    """
    return panel._send(frame_type, payload)


def request_xml(message: str) -> str:
    return android_b64decode(message[1:-1]).decode()


def unwrap(message: str) -> bytes:
    return android_b64decode(message[1:-1])


def corrupt(raw: bytes) -> bytes:
    return raw[:-1] + bytes([raw[-1] ^ 1])


def pump(
    session: ProtocolSession,
    panel: FakePanel,
    out: SessionOutput,
    sent: list[bytes] | None = None,
) -> list[SessionEvent]:
    """Deliver panel frames both ways until both sides are quiet.

    The frames the session sends are appended to ``sent``, if given.
    """
    events: list[SessionEvent] = list(out.events)
    pending = [m for m in out.outbound if not m.startswith("(<p>")]
    while pending:
        raw = unwrap(pending.pop(0))
        if sent is not None:
            sent.append(raw)
        for reply in panel.receive_frame(raw):
            got = session.receive(wrap(reply))
            pending += got.outbound
            events += got.events
    return events


def panel_for(stage: Stage) -> FakePanel:
    """Return a fake panel; for LOGGING_IN one that sends no login dump."""
    if stage is Stage.LOGGING_IN:
        return FakePanel(replace(BEHAVIOUR, documents=()))
    return FakePanel(BEHAVIOUR)


def session_at(stage: Stage, panel: FakePanel | None = None) -> ProtocolSession:
    """Return a session driven to ``stage`` (LOGGING_IN: the panel sends no dump)."""
    session, panel = new_session(), panel or panel_for(stage)
    steps = [
        (Stage.SUBSCRIBING, session.start),
        (Stage.REQUESTING_PASSWORD, lambda: session.receive("<e>w 1</e>")),
        (Stage.CONNECTING_PANEL, lambda: session.receive("<e>j </e>")),
        (Stage.HANDSHAKE, lambda: session.receive("<e>u</e>")),
    ]
    out = SessionOutput()
    for target, step in steps:
        if session.stage is stage:
            return session
        out = step()
        assert session.stage is target
    if stage is not Stage.HANDSHAKE:
        pump(session, panel, out)
    if stage is Stage.CLOSED:
        session.close()
    assert session.stage is stage
    return session


def documents(events: list[SessionEvent]) -> list[str]:
    return [e.text for e in events if isinstance(e, DocumentReceived)]


# --------------------------------------------------------------------------
# Happy paths
# --------------------------------------------------------------------------


def test_full_session_to_ready() -> None:
    session, panel = new_session(), FakePanel(BEHAVIOUR)
    out = session.start()
    assert out.events == (StageChanged(Stage.SUBSCRIBING),)
    assert f"<w>{PANEL_ID}</w>" in request_xml(out.outbound[0])
    out = session.receive("<e>w data</e>")
    assert out.events == (StageChanged(Stage.REQUESTING_PASSWORD),)
    assert f"<j>{PANEL_ID}</j>" in request_xml(out.outbound[0])
    out = session.receive("<e>j </e>")
    assert out.events == (StageChanged(Stage.CONNECTING_PANEL),)
    assert f"<z>{PASSWORD}</z>" in request_xml(out.outbound[0])
    out = session.receive("<e>u</e>")
    assert out.events == (StageChanged(Stage.HANDSHAKE),)
    assert out.outbound[0].startswith("(<p>")
    assert unwrap(out.outbound[1]) == build_frame("A", 1, b"", None)
    sent: list[bytes] = []
    events = pump(session, panel, out, sent)
    assert events[:3] == [
        StageChanged(Stage.HANDSHAKE),
        StageChanged(Stage.LOGGING_IN),
        StageChanged(Stage.READY),
    ]
    assert documents(events) == login_dump()
    assert session.stage is Stage.READY
    assert (frame_type_of(sent[2]), header_seq(sent[2])) == ("D", 3)
    assert panel.logged_in  # the fake checks the payload is f",{USER_CODE}\n"


def test_subscription_answered_with_j_directly() -> None:
    session = new_session()
    session.start()
    out = session.receive("<e>j </e>")
    assert session.stage is Stage.CONNECTING_PANEL
    assert "<z>" in request_xml(out.outbound[0])


def test_alternate_subscription_letter_and_binary_replies() -> None:
    session = new_session()
    session.start()
    session.receive(b"<e>m x</e>")
    assert session.stage is Stage.REQUESTING_PASSWORD


@pytest.mark.parametrize("letter", ["o", "x"])
def test_ok_letters_are_ignored(letter: str) -> None:
    session = session_at(Stage.SUBSCRIBING)
    assert session.receive(f"<e>{letter} </e>") == SessionOutput()
    assert session.stage is Stage.SUBSCRIBING


def test_default_link_factory() -> None:
    session = ProtocolSession(IDENTITY, USER_CODE)
    session.start()
    out = session.receive("<e>u</e>")
    assert unwrap(out.outbound[1]) == build_frame("A", 1, b"", None)


@pytest.mark.parametrize("size", [1, 5, 77, 1_000_000])
@pytest.mark.parametrize("prefix", ["", " "])
def test_frames_split_and_joined_across_messages(size: int, prefix: str) -> None:
    panel = panel_for(Stage.LOGGING_IN)
    session = session_at(Stage.LOGGING_IN, panel)
    stream = prefix + "".join(wrap(raw) for d in login_dump() for raw in panel.push(d))
    events: list[SessionEvent] = []
    for start in range(0, len(stream), size):
        events += session.receive(stream[start : start + size]).events
    assert documents(events) == login_dump()
    assert events[0] == StageChanged(Stage.READY)


def test_binary_frames() -> None:
    panel = panel_for(Stage.LOGGING_IN)
    session = session_at(Stage.LOGGING_IN, panel)
    raw = b"".join(raw for d in login_dump() for raw in panel.push(d))
    assert documents(list(session.receive(raw).events)) == login_dump()


def test_heartbeats() -> None:
    panel = FakePanel(BEHAVIOUR)
    session = session_at(Stage.READY, panel)
    out = session.receive(" ")
    (reply,) = panel.receive_frame(unwrap(out.outbound[0]))
    assert panel.received_commands[-1] == heartbeat()
    assert session.receive(wrap(reply)).events == (PanelHeartbeat(),)


@pytest.mark.parametrize("stage", [Stage.IDLE, *CLOUD_STAGES, *PANEL_STAGES[:2]])
def test_cloud_heartbeat_before_ready_is_ignored(stage: Stage) -> None:
    assert session_at(stage).receive("\n") == SessionOutput()


def test_send_command() -> None:
    panel = FakePanel(BEHAVIOUR)
    session = session_at(Stage.READY, panel)
    (message,) = session.send_command(request_log(5)).outbound
    panel.receive_frame(unwrap(message))
    assert panel.received_commands[-1] == request_log(5)


@pytest.mark.parametrize("stage", [Stage.IDLE, Stage.HANDSHAKE, Stage.LOGGING_IN])
def test_send_command_needs_ready(stage: Stage) -> None:
    with pytest.raises(NotConnectedError):
        session_at(stage).send_command(heartbeat())


def test_other_panel_data_is_ignored() -> None:
    panel = FakePanel(BEHAVIOUR)
    session = session_at(Stage.READY, panel)
    (data,) = panel.push("\x01\x02")
    assert session.receive(wrap(data)) == SessionOutput()
    assert session.receive(wrap(forge(panel, "g", b"jpg"))) == SessionOutput()


# --------------------------------------------------------------------------
# Errors
# --------------------------------------------------------------------------


@pytest.mark.parametrize("stage", CLOUD_STAGES + PANEL_STAGES)
@pytest.mark.parametrize(("letter", "error"), ERRORS.items())
def test_cloud_error_letters_raise_in_every_stage(
    stage: Stage, letter: str, error: type[CloudError]
) -> None:
    session = session_at(stage)
    with pytest.raises(error):
        session.receive(f"<e>{letter} </e>")


@pytest.mark.parametrize("stage", CLOUD_STAGES)
def test_unknown_letter_raises_in_cloud_stages(stage: Stage) -> None:
    with pytest.raises(UnknownCloudReplyError):
        session_at(stage).receive("<e>z?</e>")


@pytest.mark.parametrize("stage", PANEL_STAGES)
@pytest.mark.parametrize("reply", ["<e>z?</e>", "<e>u</e>", "<e>w x</e>"])
def test_other_replies_are_ignored_in_panel_stages(stage: Stage, reply: str) -> None:
    session = session_at(stage)
    assert session.receive(reply) == SessionOutput()
    assert session.stage is stage


@pytest.mark.parametrize("stage", CLOUD_STAGES)
def test_other_text_in_cloud_stages_is_ignored(stage: Stage) -> None:
    session = session_at(stage)
    assert session.receive("(QUJD)") == SessionOutput()
    assert session.stage is stage


def test_wrong_user_code() -> None:
    panel = FakePanel(replace(BEHAVIOUR, user_code="0000"))
    with pytest.raises(AuthenticationError, match="user code") as caught:
        session_at(Stage.READY, panel)
    assert USER_CODE not in str(caught.value)


def test_wrong_app_password_panel_nacks() -> None:
    panel = FakePanel(replace(BEHAVIOUR, app_password=OTHER_PASSWORD))
    with pytest.raises(AuthenticationError, match="app password"):
        session_at(Stage.READY, panel)


def test_wrong_app_password_frames_never_verify() -> None:
    panel = panel_for(Stage.LOGGING_IN)
    session = session_at(Stage.LOGGING_IN, panel)
    for _ in range(3):
        session.receive(wrap(corrupt(panel.push("{}")[0])))
    with pytest.raises(AuthenticationError, match="app password"):
        session.receive(wrap(corrupt(panel.push("{}")[0])))


def test_invalid_user_code_is_rejected_up_front() -> None:
    with pytest.raises(ValueError, match="digits") as caught:
        new_session("12ab")
    assert "12ab" not in str(caught.value)


def test_start_twice() -> None:
    session = session_at(Stage.SUBSCRIBING)
    with pytest.raises(RuntimeError):
        session.start()


# --------------------------------------------------------------------------
# Closing
# --------------------------------------------------------------------------


@pytest.mark.parametrize("stage", [Stage.IDLE, *CLOUD_STAGES])
def test_close_before_panel_sends_nothing(stage: Stage) -> None:
    session = session_at(stage)
    assert session.close() == SessionOutput((), (StageChanged(Stage.CLOSED),))


@pytest.mark.parametrize(
    ("stage", "frame_type"),
    [(Stage.HANDSHAKE, "F"), (Stage.LOGGING_IN, "E"), (Stage.READY, "E")],
)
def test_close_in_panel_stages_says_goodbye(stage: Stage, frame_type: str) -> None:
    panel = panel_for(stage)
    session = session_at(stage, panel)
    out = session.close()
    assert out.events == (StageChanged(Stage.CLOSED),)
    (goodbye,) = out.outbound
    assert frame_type_of(unwrap(goodbye)) == frame_type
    assert panel.receive_frame(unwrap(goodbye)) == []
    assert panel.goodbye_received
    assert session.stage is Stage.CLOSED
    assert session.close() == SessionOutput()
    assert session.receive(" ") == SessionOutput()
    assert session.receive("<e>a </e>") == SessionOutput()


def test_panel_goodbye_ends_the_session() -> None:
    panel = FakePanel(BEHAVIOUR)
    session = session_at(Stage.READY, panel)
    message = wrap(forge(panel, "e")) + wrap(panel.push("{}")[0])
    out = session.receive(message)
    assert out.events == (
        StageChanged(Stage.CLOSED),
        SessionEnded("panel sent goodbye 'e'"),
    )
    assert session.stage is Stage.CLOSED
    assert session.close() == SessionOutput()


def test_receive_before_start_is_ignored() -> None:
    session = new_session()
    assert session.receive("<e>a </e>") == SessionOutput()
    assert session.stage is Stage.IDLE


# --------------------------------------------------------------------------
# Robustness and hygiene
# --------------------------------------------------------------------------

MESSAGES = st.one_of(
    st.text(max_size=60),
    st.binary(max_size=120),
    st.builds("<e>{}{}</e>".format, st.characters(), st.text(max_size=4)),
    st.builds(
        lambda t, s, p: wrap(build_frame(t, s, p, bytes(16))),
        st.sampled_from("abcdefg"),
        st.integers(0, 300),
        st.binary(max_size=60),
    ),
)


@pytest.mark.parametrize("stage", list(Stage))
@given(messages=st.lists(MESSAGES, max_size=5))
def test_arbitrary_messages_only_raise_library_errors(
    stage: Stage, messages: list[str | bytes]
) -> None:
    session = session_at(stage)
    for message in messages:
        try:
            session.receive(message)
        except PyronixError:
            return


def test_logs_hold_no_secrets(caplog: pytest.LogCaptureFixture) -> None:
    caplog.set_level(logging.DEBUG)
    session = session_at(Stage.READY)
    session.receive(" ")
    session.close()
    assert caplog.records
    assert PASSWORD not in caplog.text
    assert USER_CODE not in caplog.text
    assert "<z>" not in caplog.text
