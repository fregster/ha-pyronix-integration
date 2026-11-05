# SPDX-FileCopyrightText: 2026 Paul Fryer
# SPDX-License-Identifier: Apache-2.0
from __future__ import annotations

import pytest

from custom_components.pyronix_cloud.const import (
    CONF_APP_PASSWORD,
    CONF_APP_USER,
    CONF_PANEL_ID,
    CONF_USER_CODE,
)
from custom_components.pyronix_cloud.input_checks import (
    field_errors,
    normalise_system_id,
    url_allowed,
    user_code_valid,
)


def test_normalise_system_id() -> None:
    assert normalise_system_id("  abCd1234 ") == "ABCD1234"


@pytest.mark.parametrize(
    ("code", "valid"),
    [
        ("1", True),
        ("12345678", True),
        ("", False),
        ("123456789", False),
        ("12a4", False),
        ("12 4", False),
        ("١٢٣٤", False),  # Arabic-Indic digits are digits, but not ASCII
    ],
)
def test_user_code_valid(code: str, *, valid: bool) -> None:
    assert user_code_valid(code) is valid


@pytest.mark.parametrize(
    ("url", "allowed"),
    [
        ("wss://relay.example.com", True),
        ("ws://localhost:8080", True),
        ("ws://127.0.0.1", True),
        ("ws://relay.example.com", False),
        ("https://localhost", False),
        ("wss://", False),
        ("wss://[::1", False),
    ],
)
def test_url_allowed(url: str, *, allowed: bool) -> None:
    assert url_allowed(url) is allowed


def test_field_errors_checks_only_present_fields() -> None:
    assert field_errors({}) == {}
    assert (
        field_errors(
            {
                CONF_APP_USER: "Home Assistant",
                CONF_PANEL_ID: "TESTPANL",
                CONF_APP_PASSWORD: "app-password",
                CONF_USER_CODE: "1234",
            }
        )
        == {}
    )
    assert field_errors(
        {
            CONF_APP_USER: "<me>",
            CONF_PANEL_ID: "SHORT",
            CONF_APP_PASSWORD: "a&b",
            CONF_USER_CODE: "x",
        }
    ) == {
        CONF_APP_USER: "invalid_characters",
        CONF_PANEL_ID: "invalid_system_id",
        CONF_APP_PASSWORD: "invalid_characters",
        CONF_USER_CODE: "invalid_user_code",
    }
