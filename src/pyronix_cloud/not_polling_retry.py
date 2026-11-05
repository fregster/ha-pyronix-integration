# SPDX-FileCopyrightText: 2026 Paul Fryer
# SPDX-License-Identifier: LGPL-3.0-or-later
"""Retry opening a session while the cloud replies ``n``, panel not polling (§3.4).

Right after a session's goodbye, the cloud sometimes answers the next connection
with ``n`` and accepts one about 35 seconds later (reported by aiopyronix, tested on
hardware). ``n`` arrives in the cloud stages, before the panel link exists, so a
retry can never repeat a panel command. Only whole connection attempts are
retried, with a fixed pause between them and a small, fixed number of tries.
"""

from __future__ import annotations

import asyncio
from dataclasses import dataclass
import logging
from typing import TYPE_CHECKING, Final

from pyronix_cloud.errors import PanelOfflineError

if TYPE_CHECKING:
    from collections.abc import Awaitable, Callable

_LOGGER = logging.getLogger(__name__)


@dataclass(frozen=True, slots=True, kw_only=True)
class RetryPolicy:
    """How often, and how far apart, a connection refused with ``n`` is retried.

    The defaults pause 40 s in total, enough for the ~35 s gap seen live.
    """

    retries: int = 4
    """Connection attempts after the first refusal; 0 disables retrying."""
    pause: float = 10.0
    """Seconds between attempts."""


NO_RETRY: Final = RetryPolicy(retries=0)
"""Fail at the first ``n``, for example while validating new credentials."""


class NotPollingRetry:
    """Runs connection attempts, retrying those the cloud refused with ``n``."""

    def __init__(self, policy: RetryPolicy) -> None:
        """Check the policy.

        Args:
            policy: The number of retries and the pause between them.

        Raises:
            ValueError: The policy would retry a negative number of times, or
                without a pause.
        """
        if policy.retries < 0 or (policy.retries and policy.pause <= 0):
            msg = "invalid retry policy: retries must be >= 0 and pause > 0"
            raise ValueError(msg)
        self._policy = policy
        self._stopped = asyncio.Event()

    async def run[T](self, attempt: Callable[[], Awaitable[T]]) -> T:
        """Await ``attempt`` until it succeeds, retrying after ``n`` refusals.

        Args:
            attempt: Opens one session; it must clean up after itself on failure.

        Returns:
            What the successful attempt returned.

        Raises:
            PanelOfflineError: The cloud still refused after the last retry, or
                :meth:`stop` was called during a pause.
        """
        self._stopped.clear()
        retries_left = self._policy.retries
        while True:
            try:
                return await attempt()
            except PanelOfflineError:
                if retries_left == 0 or not await self._pause():
                    raise
            retries_left -= 1

    def stop(self) -> None:
        """End a pending pause; the refusal that caused it is raised."""
        self._stopped.set()

    async def _pause(self) -> bool:
        """Wait for the policy's pause; False if :meth:`stop` ended it early."""
        pause = self._policy.pause
        _LOGGER.debug("The cloud says the panel is not polling; retry in %.0f s", pause)
        try:
            async with asyncio.timeout(pause):
                await self._stopped.wait()
        except TimeoutError:
            return True
        return False
