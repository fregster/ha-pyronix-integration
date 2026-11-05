# SPDX-FileCopyrightText: 2026 Paul Fryer
# SPDX-License-Identifier: LGPL-3.0-or-later
"""Fail every test whose log records reveal the fake credentials or a derived key.

``tests/conftest.py`` loads this module as a pytest plugin, so its autouse
fixture runs in every test. The fixture turns on DEBUG logging for every logger
(so the redaction paths run), records each fixed key and session key that the
client or the fake panel derives, and checks each log record as it is emitted.
The secrets are the fake panel's default app password and user code (the values
every fake-cloud test uses), any longer app password the client hashes, and the
keys, in the plain, hex, ``repr`` and base64 forms a careless log line would use.
"""

from __future__ import annotations

import base64
from contextlib import suppress
import logging
import re
from typing import TYPE_CHECKING, Final, override

import pytest

from pyronix_cloud import commands, crypto, link
from pyronix_cloud.testing import fake_panel

if TYPE_CHECKING:
    from collections.abc import Iterator

WATCHED_LOGGERS: Final = (
    "",
    "aiohttp",
    "asyncio",
    "custom_components.pyronix_cloud",
    "homeassistant",
    "pyronix_cloud",
)
"""Loggers set to DEBUG for each test (``""`` is the root logger)."""

_MIN_FORM_LEN: Final = 6
"""Shorter encoded forms would match unrelated text by chance."""
_MIN_PASSWORD_LEN: Final = 6
"""Passwords hashed by the client are recorded only from this length."""
_NOT_WORD: Final = r"(?<![A-Za-z0-9]){}(?![A-Za-z0-9])"


def _base64_forms(data: bytes) -> set[str]:
    """Return the base64 text that encodes ``data`` at each byte alignment.

    A secret inside a longer base64 message (such as a cloud request with the
    password in ``<z>``) starts at any of three offsets; only the characters
    that depend on ``data`` alone are kept, in both base64 alphabets.
    """
    forms: set[str] = set()
    for offset in range(3):
        encoded = base64.b64encode(bytes(offset) + data).decode("ascii")
        start = -(-8 * offset // 6)
        end = 8 * (offset + len(data)) // 6
        forms.add(encoded[start:end])
    forms |= {form.replace("+", "-").replace("/", "_") for form in forms}
    return forms


def _byte_forms(data: bytes) -> set[str]:
    """Return the hex, ``repr`` and base64 forms of a secret byte string."""
    forms = {data.hex(), data.hex().upper(), repr(data)[2:-1]}
    forms |= _base64_forms(data)
    return {form for form in forms if len(form) >= _MIN_FORM_LEN}


def record_text(record: logging.LogRecord) -> str:
    """Return every text a log record carries: message, args and traceback."""
    parts = [str(record.msg), repr(record.args)]
    # A malformed record cannot be formatted; its raw msg and args are checked.
    with suppress(TypeError, ValueError):
        parts.append(record.getMessage())
    if record.exc_info:
        parts.append(logging.Formatter().formatException(record.exc_info))
    parts.extend(text for text in (record.exc_text, record.stack_info) if text)
    return "\n".join(parts)


class LeakGuard(logging.Handler):
    """A log handler that notes each record revealing a registered secret."""

    def __init__(self) -> None:
        """Start with no secrets; nothing can leak until one is added."""
        super().__init__(logging.DEBUG)
        self.leaks: list[str] = []
        self.captured: list[tuple[str, int]] = []
        self.keys: set[bytes] = set()
        self._patterns: dict[str, tuple[re.Pattern[str], str]] = {}

    def add_text(self, kind: str, secret: str) -> None:
        """Watch for a text secret as a whole word and in its encoded forms."""
        self._add(kind, re.compile(_NOT_WORD.format(re.escape(secret))))
        self.add_bytes(kind, secret.encode("utf-8"))

    def add_user_code(self, code: str) -> None:
        """Watch for a user code and for the login payload that carries it."""
        self.add_text("user code", code)
        self.add_bytes("login payload", commands.login(code))

    def add_bytes(self, kind: str, secret: bytes) -> None:
        """Watch for a byte secret in its hex, ``repr`` and base64 forms."""
        for form in sorted(_byte_forms(secret)):
            self._add(kind, re.compile(re.escape(form)))

    def add_key(self, kind: str, key: bytes) -> None:
        """Watch for a derived key."""
        self.keys.add(key)
        self.add_bytes(kind, key)

    def find_leak(self, record: logging.LogRecord) -> str | None:
        """Describe the secret a record reveals, without echoing it; else None."""
        text = record_text(record)
        for pattern, kind in self._patterns.values():
            if pattern.search(text):
                return (
                    f"{record.levelname} record from {record.name} "
                    f"({record.pathname}:{record.lineno}) reveals the {kind}"
                )
        return None

    @override
    def emit(self, record: logging.LogRecord) -> None:
        """Note the record, and the secret it reveals if any."""
        self.captured.append((record.name, record.levelno))
        leak = self.find_leak(record)
        if leak is not None:
            self.leaks.append(leak)

    def check(self) -> None:
        """Fail the test if any record revealed a secret."""
        if self.leaks:
            pytest.fail("Secret leaked to the log:\n" + "\n".join(self.leaks))

    def _add(self, kind: str, pattern: re.Pattern[str]) -> None:
        """Index one compiled pattern by its source, labelled with its kind."""
        self._patterns.setdefault(pattern.pattern, (pattern, kind))


def _record_keys(guard: LeakGuard, monkeypatch: pytest.MonkeyPatch) -> None:
    """Make the client and the fake panel report every key they derive."""

    def fixed_key(app_password: str, panel_id: str) -> bytes:
        if len(app_password) >= _MIN_PASSWORD_LEN:
            guard.add_text("app password", app_password)
        key = crypto.fixed_key(app_password, panel_id)
        guard.add_key("fixed key", key)
        return key

    def derive_session_key(fixed: bytes, shared: bytes, extra: bytes) -> bytes:
        key = crypto.derive_session_key(fixed, shared, extra)
        guard.add_key("session key", key)
        return key

    for module in (link, fake_panel):
        monkeypatch.setattr(module, "fixed_key", fixed_key)
        monkeypatch.setattr(module, "derive_session_key", derive_session_key)


@pytest.fixture(autouse=True)
def leak_guard(monkeypatch: pytest.MonkeyPatch) -> Iterator[LeakGuard]:
    """Log everything at DEBUG and fail the test if a record reveals a secret."""
    guard = LeakGuard()
    defaults = fake_panel.PanelBehaviour()
    guard.add_text("app password", defaults.app_password)
    guard.add_user_code(defaults.user_code)
    _record_keys(guard, monkeypatch)
    loggers = [logging.getLogger(name) for name in WATCHED_LOGGERS]
    levels = [logger.level for logger in loggers]
    for logger in loggers:
        logger.setLevel(logging.DEBUG)
    loggers[0].addHandler(guard)
    try:
        yield guard
    finally:
        loggers[0].removeHandler(guard)
        for logger, level in zip(loggers, levels, strict=True):
            logger.setLevel(level)
    guard.check()
