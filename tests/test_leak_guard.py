# SPDX-FileCopyrightText: 2026 Paul Fryer
# SPDX-License-Identifier: LGPL-3.0-or-later
"""Prove the leak guard is live and catches leaks, so it cannot pass vacuously."""

from __future__ import annotations

import base64
import logging

import pytest

from pyronix_cloud import commands
from pyronix_cloud.client import Credentials, PyronixClient, Timeouts
from pyronix_cloud.testing import dump
from pyronix_cloud.testing.fake_cloud import FakeCloud
from pyronix_cloud.testing.fake_panel import PanelBehaviour
from tests.leak_guard import WATCHED_LOGGERS, LeakGuard

DEFAULTS = PanelBehaviour()
PASSWORD = DEFAULTS.app_password
CODE = DEFAULTS.user_code
KEY = bytes(range(0x80, 0x90))


def guard() -> LeakGuard:
    """A guard that is not installed, holding the fake credentials and ``KEY``."""
    fresh = LeakGuard()
    fresh.add_text("app password", PASSWORD)
    fresh.add_user_code(CODE)
    fresh.add_key("session key", KEY)
    return fresh


def record(
    msg: str, *args: object, exc: BaseException | None = None
) -> logging.LogRecord:
    exc_info = None if exc is None else (type(exc), exc, None)
    return logging.LogRecord(
        "pyronix_cloud.test", logging.DEBUG, __file__, 1, msg, args, exc_info
    )


def cloud_request(prefix: bytes) -> str:
    """Base64 of a cloud request whose ``<z>`` starts at any byte alignment."""
    xml = prefix + b"<z>" + PASSWORD.encode() + b"</z>"
    return base64.b64encode(xml).decode()


LEAKS = {
    "password in the message": record(f"password is {PASSWORD}"),
    "password in an arg": record("password is %s", PASSWORD),
    "user code as a word": record("code=%s.", CODE),
    "login payload repr": record("TX payload %r", commands.login(CODE)),
    "login payload hex": record("TX payload %s", commands.login(CODE).hex()),
    "key hex": record("key %s", KEY.hex()),
    "key hex upper": record("key %s", KEY.hex().upper()),
    "key repr": record("key %r", KEY),
    "key base64": record("key %s", base64.b64encode(KEY).decode()),
    "password in base64, offset 0": record("sent %s", cloud_request(b"")),
    "password in base64, offset 1": record("sent %s", cloud_request(b"<")),
    "password in base64, offset 2": record("sent %s", cloud_request(b"<p")),
    "malformed record": record("no placeholder", PASSWORD),
}
CLEAN = [
    record("Received a base64 panel frame of %d bytes", 412345),
    record("Task <Task pending> at 0x7f1234abcdef"),
    record("TX %s seq=%d", "D", 1234567),
    record("Sending cloud request %s", "CONN_TO_CLOUD"),
]


@pytest.mark.parametrize("leaky", LEAKS.values(), ids=LEAKS.keys())
def test_each_leak_form_is_caught(leaky: logging.LogRecord) -> None:
    leak = guard().find_leak(leaky)
    assert leak is not None
    assert PASSWORD not in leak
    assert CODE not in leak


def test_a_leak_in_a_traceback_is_caught() -> None:
    leaky = record("validation failed", exc=ValueError(PASSWORD))
    assert guard().find_leak(leaky) is not None


@pytest.mark.parametrize("clean", CLEAN)
def test_ordinary_records_pass(clean: logging.LogRecord) -> None:
    assert guard().find_leak(clean) is None


def test_check_fails_the_test_after_a_leak(leak_guard: LeakGuard) -> None:
    logging.getLogger("pyronix_cloud.client").debug("code %s", CODE)
    assert len(leak_guard.leaks) == 1
    with pytest.raises(pytest.fail.Exception, match="reveals the user code"):
        leak_guard.check()
    leak_guard.leaks.clear()  # the leak was deliberate; let teardown pass


def test_every_watched_logger_logs_debug(leak_guard: LeakGuard) -> None:
    del leak_guard  # requested so the assertion runs with the guard installed
    for name in (*WATCHED_LOGGERS, "pyronix_cloud.link", "aiohttp.client"):
        assert logging.getLogger(name).isEnabledFor(logging.DEBUG)


async def test_a_real_session_is_watched(leak_guard: LeakGuard) -> None:
    credentials = Credentials(
        app_user="tester",
        panel_id=dump.PANEL_ID,
        app_password=PASSWORD,
        user_code=CODE,
        device_id="device-1",
    )
    timeouts = Timeouts(connect=1, step=1, dump=1, command=0.05, close=1)
    async with FakeCloud() as cloud:
        client = PyronixClient(credentials, cloud.connector(), timeouts=timeouts)
        async with client:
            pass
        panel = cloud.sessions[0].panel
    assert panel is not None
    assert panel.session_key in leak_guard.keys
    library = [
        level
        for name, level in leak_guard.captured
        if name.startswith("pyronix_cloud.")
    ]
    assert logging.DEBUG in library
    assert leak_guard.leaks == []
