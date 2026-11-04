# SPDX-FileCopyrightText: 2026 Paul Fryer
# SPDX-License-Identifier: LGPL-3.0-or-later
from __future__ import annotations

from pyronix_cloud.cloud import DEFAULT_PUSH_ID
from pyronix_cloud.settings import Credentials, Timeouts


def test_credentials_hide_secrets() -> None:
    credentials = Credentials(
        app_user="tester",
        panel_id="TESTPANL",
        app_password="app-password",  # noqa: S106 - fake test value
        user_code="1234",
        device_id="device-1",
    )
    text = repr(credentials)
    assert "app-password" not in text
    assert "1234" not in text
    assert credentials.push_id == DEFAULT_PUSH_ID


def test_timeout_defaults() -> None:
    assert Timeouts() == Timeouts(connect=15, step=15, dump=10, command=15, close=5)
