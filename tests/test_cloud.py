# SPDX-FileCopyrightText: 2026 Paul Fryer
# SPDX-License-Identifier: LGPL-3.0-or-later
"""Tests for pyronix_cloud.cloud."""

from __future__ import annotations

import base64

from hypothesis import given
from hypothesis import strategies as st
import pytest

from pyronix_cloud.cloud import (
    CloudIdentity,
    CloudReply,
    CloudRequest,
    build_request,
    check_reply,
    is_heartbeat,
    parse_reply,
    redact,
)
from pyronix_cloud.errors import (
    AccessDeniedError,
    CloudError,
    PanelBusyError,
    PanelOfflineError,
    SubscriptionExpiredError,
    UnknownCloudReplyError,
)

PASSWORD = "s3cret-PW"  # noqa: S105 - fake test value, not a real credential


def identity(**overrides: str) -> CloudIdentity:
    values = {
        "app_user": "user@example.com",
        "panel_id": "12345678",
        "app_password": PASSWORD,
        "device_id": "dev-0001",
    } | overrides
    return CloudIdentity(**values)


# Expected wire text, generated once with probe/pyronix_probe.py
# (make_cloud_message + wrap_cloud) for the identity above.
EXPECTED = {
    CloudRequest.SUBSCRIPTION: (
        "(PHA+PG4+dXNlckBleGFtcGxlLmNvbTwvbj48dT5oYS1weXJvbml4PC91Pjx3PjEyMzQ1Njc4PC93\n"
        "PjxpPmRldi0wMDAxPC9pPjxsPjYuMDMuMDAxPC9sPjx0PjE2PC90PjxtPkFuZHJvaWQ8L20+PHY+\n"
        "MTQ8L3Y+PGM+V2lmaTwvYz48bGFuZz51azwvbGFuZz48L3A+\n)"
    ),
    CloudRequest.REQ_PWD: (
        "(PHA+PG4+dXNlckBleGFtcGxlLmNvbTwvbj48dT5oYS1weXJvbml4PC91PjxqPjEyMzQ1Njc4PC9q\n"
        "PjxpPmRldi0wMDAxPC9pPjxsPjYuMDMuMDAxPC9sPjx0PjE2PC90PjxtPkFuZHJvaWQ8L20+PHY+\n"
        "MTQ8L3Y+PGM+V2lmaTwvYz48bGFuZz51azwvbGFuZz48L3A+\n)"
    ),
    CloudRequest.CONN_TO_CLOUD: (
        "(PHA+PG4+dXNlckBleGFtcGxlLmNvbTwvbj48dT5oYS1weXJvbml4PC91PjxhPjEyMzQ1Njc4PC9h\n"
        "PjxiPkhvbWU8L2I+PHo+czNjcmV0LVBXPC96PjxpPmRldi0wMDAxPC9pPjxsPjYuMDMuMDAxPC9s\n"
        "Pjx0PjE2PC90PjxtPkFuZHJvaWQ8L20+PHY+MTQ8L3Y+PGM+V2lmaTwvYz48bGFuZz51azwvbGFu\n"
        "Zz48L3A+\n)"
    ),
    CloudRequest.CONN_TO_PANEL: (
        "(<p><n>user@example.com</n><u>ha-pyronix</u><a>12345678</a><b>Home</b>"
        "<z>s3cret-PW</z><i>dev-0001</i><l>6.03.001</l><t>16</t><m>Android</m>"
        "<v>14</v><c>Wifi</c><lang>uk</lang></p>)"
    ),
}

TAIL = (
    "<i>dev-0001</i><l>6.03.001</l><t>16</t><m>Android</m><v>14</v><c>Wifi</c>"
    "<lang>uk</lang></p>"
)
HEAD = "<p><n>user@example.com</n><u>ha-pyronix</u>"


@pytest.mark.parametrize("kind", list(CloudRequest))
def test_build_request_matches_probe(kind: CloudRequest) -> None:
    assert build_request(identity(), kind) == EXPECTED[kind]


def test_build_request_plain_text_field_order() -> None:
    def plain(kind: CloudRequest) -> str:
        wire = build_request(identity(), kind)
        return base64.b64decode(wire[1:-1].replace("\n", "")).decode()

    assert plain(CloudRequest.SUBSCRIPTION) == HEAD + "<w>12345678</w>" + TAIL
    assert plain(CloudRequest.REQ_PWD) == HEAD + "<j>12345678</j>" + TAIL
    assert plain(CloudRequest.CONN_TO_CLOUD) == (
        HEAD + "<a>12345678</a><b>Home</b><z>s3cret-PW</z>" + TAIL
    )


def test_build_request_uses_push_id_and_system_name() -> None:
    wire = build_request(
        identity(push_id="push", system_name="Villa"), CloudRequest.CONN_TO_PANEL
    )
    assert "<u>push</u>" in wire
    assert "<b>Villa</b>" in wire


def test_identity_repr_hides_password() -> None:
    assert PASSWORD not in repr(identity())


@pytest.mark.parametrize("name", ["app_user", "panel_id", "device_id", "push_id"])
def test_identity_rejects_empty_required_field(name: str) -> None:
    with pytest.raises(ValueError, match=name):
        identity(**{name: ""})


def test_identity_allows_empty_system_name() -> None:
    assert identity(system_name="").system_name == ""


@pytest.mark.parametrize(
    "name",
    ["app_user", "panel_id", "app_password", "device_id", "push_id", "system_name"],
)
@pytest.mark.parametrize("char", ["<", ">", "&"])
def test_identity_rejects_markup_characters(name: str, char: str) -> None:
    value = f"pw{char}{PASSWORD}"
    with pytest.raises(ValueError, match=name) as info:
        identity(**{name: value})
    assert PASSWORD not in str(info.value)
    assert value not in str(info.value)


def test_redact_removes_every_secret() -> None:
    text = (
        "a<z>" + PASSWORD + "</z>b<z>multi\nline\n" + PASSWORD + "</z>c<z></z>"
        "<z>" + PASSWORD
    )
    out = redact(text)
    assert PASSWORD not in out
    assert "multi" not in out
    assert out == (
        "a<z>[REDACTED]</z>b<z>[REDACTED]</z>c<z>[REDACTED]</z><z>[REDACTED]</z>"
    )


def test_redact_connect_request() -> None:
    out = redact(build_request(identity(), CloudRequest.CONN_TO_PANEL))
    assert PASSWORD not in out
    assert "<z>[REDACTED]</z>" in out
    assert redact("no secret") == "no secret"


@pytest.mark.parametrize(
    ("message", "expected"),
    [(" ", True), ("\n", True), (")", False), ("", False), ("  ", False)],
)
def test_is_heartbeat(message: str, *, expected: bool) -> None:
    assert is_heartbeat(message) is expected


@pytest.mark.parametrize(
    ("message", "letter", "data"),
    [
        ("<e>j </e>", "j", ""),
        ("<e>j abc</e>", "j", "abc"),
        ("<e>u</e>", "u", ""),
        ("<e>w,1</e>", "w", "1"),
        ("<e>o</e>", "o", ""),
        ("<e>jABCDEF</e>", "j", "BCDEF"),
        ("xx<e>n:</e>", "n", ""),
        ("<e>w,12", "w", "12"),
        ("<e>", "", ""),
        ("<e></e>", "", ""),
        ("text<e>", "", ""),
    ],
)
def test_parse_reply(message: str, letter: str, data: str) -> None:
    assert parse_reply(message) == CloudReply(letter=letter, data=data)


@pytest.mark.parametrize("message", ["", "hello", "<e", "(QUJD)"])
def test_parse_reply_without_marker(message: str) -> None:
    assert parse_reply(message) is None


def test_reply_repr_hides_data() -> None:
    reply = CloudReply(letter="j", data=PASSWORD)
    assert PASSWORD not in repr(reply)
    assert "'j'" in repr(reply)


@given(st.text())
def test_parse_reply_never_raises(message: str) -> None:
    reply = parse_reply(message)
    assert reply is None or len(reply.letter) <= 1


@given(st.text(), st.text())
def test_parse_reply_finds_marker_anywhere(prefix: str, data: str) -> None:
    reply = parse_reply(prefix.replace("<e>", "") + "<e>w," + data + "</e>")
    assert reply is not None
    assert reply.letter == "w"
    assert data.startswith(reply.data)


@pytest.mark.parametrize(
    ("letter", "error"),
    [
        ("a", AccessDeniedError),
        ("b", PanelBusyError),
        ("i", SubscriptionExpiredError),
        ("n", PanelOfflineError),
    ],
)
def test_check_reply_errors(letter: str, error: type[CloudError]) -> None:
    with pytest.raises(error) as info:
        check_reply(CloudReply(letter=letter, data=PASSWORD))
    assert PASSWORD not in str(info.value)


@pytest.mark.parametrize("letter", list("wmjuox"))
def test_check_reply_accepts_ok_letters(letter: str) -> None:
    check_reply(CloudReply(letter=letter, data=""))


@pytest.mark.parametrize("letter", ["", "z", "A", "<"])
def test_check_reply_unknown(letter: str) -> None:
    with pytest.raises(UnknownCloudReplyError):
        check_reply(CloudReply(letter=letter, data=""))


def test_check_reply_truncated_message() -> None:
    reply = parse_reply("<e>")
    assert reply is not None
    with pytest.raises(UnknownCloudReplyError):
        check_reply(reply)
