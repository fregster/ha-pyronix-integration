# SPDX-FileCopyrightText: 2026 Paul Fryer
# SPDX-License-Identifier: LGPL-3.0-or-later
"""Turn session events into panel snapshots that listeners and waiting tasks follow.

A :class:`PanelFeed` parses each document a session delivers, folds it into a
:class:`~pyronix_cloud.panel_state.PanelStateTracker`, tells listeners about
each changed snapshot, and lets tasks wait for a condition on the state with a
deadline. A document that fails to parse is logged and skipped.
"""

from __future__ import annotations

import asyncio
from contextlib import suppress
import logging
from typing import TYPE_CHECKING

from pyronix_cloud.documents import parse_document
from pyronix_cloud.errors import (
    ConnectionClosedError,
    DocumentError,
    PyronixError,
    PyronixTimeoutError,
)
from pyronix_cloud.panel_state import PanelSnapshot, PanelStateTracker
from pyronix_cloud.session import DocumentReceived, Stage, StageChanged

if TYPE_CHECKING:
    from collections.abc import Callable

    from pyronix_cloud.connection import SessionEvent
    from pyronix_cloud.settings import Timeouts

_LOGGER = logging.getLogger(__name__)

type SnapshotListener = Callable[[PanelSnapshot], None]


class PanelFeed:
    """Panel state of one session at a time, with listeners and waiters."""

    def __init__(self, timeouts: Timeouts) -> None:
        """Start with an empty snapshot.

        Args:
            timeouts: ``step`` bounds each stage and ``dump`` the login dump.
        """
        self._timeouts = timeouts
        self._tracker = PanelStateTracker()
        self._listeners: list[SnapshotListener] = []
        self._wake = asyncio.Event()
        self._ended = False
        self._end_error: PyronixError | None = None
        self._stage = Stage.IDLE
        self._deadline = 0.0

    @property
    def snapshot(self) -> PanelSnapshot:
        """The latest panel state."""
        return self._tracker.snapshot

    def add_listener(self, listener: SnapshotListener) -> Callable[[], None]:
        """Call ``listener`` with each changed snapshot; returns a remover."""
        self._listeners.append(listener)

        def remove() -> None:
            with suppress(ValueError):
                self._listeners.remove(listener)

        return remove

    def restart(self) -> None:
        """Forget the previous session: empty snapshot, stage IDLE."""
        self._tracker = PanelStateTracker()
        self._ended, self._end_error = False, None
        self._enter_stage(Stage.IDLE)

    def handle(self, event: SessionEvent) -> None:
        """Act on one session event and wake the waiters."""
        if isinstance(event, StageChanged):
            _LOGGER.debug("Session stage %s", event.stage.name)
            self._enter_stage(event.stage)
        elif isinstance(event, DocumentReceived):
            self._apply(event.text)
        self._notify()

    def end(self, error: PyronixError | None) -> None:
        """Record that the session ended; waiters then raise ``error``.

        Args:
            error: Why it ended, or None after a clean panel goodbye (waiters
                then raise :class:`ConnectionClosedError`).
        """
        self._ended, self._end_error = True, error
        self._notify()

    async def wait_for_dump(self) -> None:
        """Wait for the end of the login dump, each stage within its timeout.

        Raises:
            PyronixTimeoutError: A stage or the dump took too long.
            PyronixError: The session ended first.
        """
        await self._wait(
            lambda: self.snapshot.dump_complete,
            lambda: self._deadline,
            lambda: PyronixTimeoutError(f"timed out in stage {self._stage.name}"),
        )

    async def wait_until(
        self,
        done: Callable[[PanelSnapshot], bool],
        within: float,
        timeout_error: Callable[[], PyronixError],
    ) -> PanelSnapshot:
        """Wait until ``done(snapshot)`` holds.

        Args:
            done: The condition on the snapshot.
            within: Seconds to wait.
            timeout_error: Builds the error raised on timeout.

        Returns:
            The first snapshot that satisfies ``done``.

        Raises:
            PyronixError: The timeout error, or why the session ended.
        """
        deadline = asyncio.get_running_loop().time() + within
        await self._wait(lambda: done(self.snapshot), lambda: deadline, timeout_error)
        return self.snapshot

    async def _wait(
        self,
        done: Callable[[], bool],
        deadline: Callable[[], float],
        timeout_error: Callable[[], PyronixError],
    ) -> None:
        """Wait until ``done()``, the session ends, or the deadline passes."""
        while not done():
            if self._ended:
                raise self._end_error or ConnectionClosedError("the panel said goodbye")
            wake = self._wake
            try:
                async with asyncio.timeout_at(deadline()):
                    await wake.wait()
            except TimeoutError as err:
                raise timeout_error() from err

    def _notify(self) -> None:
        """Wake every waiter."""
        wake, self._wake = self._wake, asyncio.Event()
        wake.set()

    def _enter_stage(self, stage: Stage) -> None:
        """Record the stage and restart its timeout."""
        self._stage = stage
        limit = self._timeouts.dump if stage is Stage.READY else self._timeouts.step
        self._deadline = asyncio.get_running_loop().time() + limit

    def _apply(self, text: str) -> None:
        """Parse and apply one document, telling listeners if the state changed."""
        try:
            document = parse_document(text)
        except DocumentError as err:
            _LOGGER.warning("Skipped a panel document that did not parse: %s", err)
            return
        if not self._tracker.apply(document):
            return
        snapshot = self.snapshot
        for listener in list(self._listeners):
            try:
                listener(snapshot)
            except Exception:
                _LOGGER.exception("Error in a snapshot listener")
