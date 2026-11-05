# SPDX-FileCopyrightText: 2026 Paul Fryer
# SPDX-License-Identifier: LGPL-3.0-or-later
"""Shared pytest configuration for the library tests."""

from __future__ import annotations

import asyncio
import sys

from hypothesis import HealthCheck, settings
import pytest

# Every test fails if a log record reveals a fake credential or a session key.
pytest_plugins = ["tests.leak_guard"]

# Crypto-heavy properties are slow on shared CI runners; keep them deterministic and
# free of wall-clock deadlines.
settings.register_profile(
    "default",
    deadline=None,
    derandomize=True,
    max_examples=200,
    suppress_health_check=[HealthCheck.too_slow],
)
settings.load_profile("default")


_SYNC_LOOP = pytest.StashKey[asyncio.AbstractEventLoop]()
# A plain bool, so mypy does not treat the hook body as unreachable on Python 3.14.
_OLD_HA_TEST_PLUGIN: bool = sys.version_info < (3, 14)


@pytest.hookimpl(tryfirst=True)
def pytest_runtest_setup(item: pytest.Item) -> None:
    """Give every test a current event loop on the Python 3.13 / HA 2026.2 leg.

    That HA test plugin's sync autouse fixtures (``enable_event_loop_debug``,
    ``verify_cleanup``) call ``asyncio.get_event_loop()``. It raises once
    pytest-asyncio has set and closed a loop for an earlier test, because those
    fixtures can run before pytest-asyncio makes its own loop current. pytest-asyncio
    still runs async tests in its own loop. Newer HA plugins (Python 3.14 leg) use
    the running loop instead. Remove this hook and its teardown partner when Python
    3.13 support is dropped.
    """
    if not _OLD_HA_TEST_PLUGIN:
        return
    loop = asyncio.new_event_loop()
    asyncio.set_event_loop(loop)
    item.stash[_SYNC_LOOP] = loop


@pytest.hookimpl(trylast=True)
def pytest_runtest_teardown(item: pytest.Item) -> None:
    """Close the loop that :func:`pytest_runtest_setup` created, if any."""
    loop = item.stash.get(_SYNC_LOOP, None)
    if loop is not None:
        asyncio.set_event_loop(None)
        loop.close()
