# SPDX-FileCopyrightText: 2026 Paul Fryer
# SPDX-License-Identifier: LGPL-3.0-or-later
"""Exponential reconnect back-off with jitter for persistent sessions.

Repeated failed logins may lock the panel or raise tamper alerts (AGENTS.md), so a
persistent connection must never retry in a tight loop.
"""

from __future__ import annotations

import random
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from collections.abc import Callable


class Backoff:
    """Delays that grow by ``factor`` per failure, capped at ``maximum``."""

    def __init__(
        self,
        *,
        initial: float = 5.0,
        maximum: float = 300.0,
        factor: float = 2.0,
        jitter: float = 0.1,
        rng: Callable[[], float] = random.random,
    ) -> None:
        """Configure the back-off.

        Args:
            initial: Delay in seconds after the first failure.
            maximum: Upper bound for any delay, before jitter.
            factor: Growth per consecutive failure.
            jitter: Fraction of the delay added at random (0.1 adds up to 10 %).
            rng: Source of uniform floats in [0, 1); injectable for tests.
        """
        if initial <= 0 or maximum < initial or factor < 1 or not 0 <= jitter <= 1:
            msg = "invalid back-off parameters"
            raise ValueError(msg)
        self._initial = initial
        self._maximum = maximum
        self._factor = factor
        self._jitter = jitter
        self._rng = rng
        self._failures = 0

    @property
    def failures(self) -> int:
        """Consecutive failures since the last :meth:`reset`."""
        return self._failures

    def next_delay(self) -> float:
        """Record a failure and return the seconds to wait before the next try."""
        base = min(self._maximum, self._initial * self._factor**self._failures)
        self._failures += 1
        return base * (1 + self._jitter * self._rng())

    def reset(self) -> None:
        """Forget earlier failures after a successful connection."""
        self._failures = 0
