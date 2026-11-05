# SPDX-FileCopyrightText: 2026 Paul Fryer
# SPDX-License-Identifier: LGPL-3.0-or-later
"""Let one panel command run at a time on a client and refuse a second one at once.

Two set/unset commands in flight on one session would share one confirmation
wait and could each read the other's area changes as their own. A second
command is refused with :class:`~pyronix_cloud.errors.CommandInProgressError`
rather than queued, so a caller never sends a command it may no longer want.
"""

from __future__ import annotations

from contextlib import contextmanager
from typing import TYPE_CHECKING

from pyronix_cloud.errors import CommandInProgressError

if TYPE_CHECKING:
    from collections.abc import Iterator


class CommandGuard:
    """A non-blocking, non-queuing flag held while one command runs."""

    def __init__(self) -> None:
        """Start free."""
        self._busy = False

    @property
    def busy(self) -> bool:
        """Whether a command holds the guard."""
        return self._busy

    @contextmanager
    def hold(self) -> Iterator[None]:
        """Hold the guard for the body; it is released on every exit path.

        Raises:
            CommandInProgressError: Another command holds the guard.
        """
        if self._busy:
            msg = "another command is still running on this client"
            raise CommandInProgressError(msg)
        self._busy = True
        try:
            yield
        finally:
            self._busy = False
