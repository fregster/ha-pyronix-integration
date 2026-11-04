# SPDX-FileCopyrightText: 2026 Paul Fryer
# SPDX-License-Identifier: LGPL-3.0-or-later
"""Async client for Pyronix panels via the Pyronix Cloud (HomeControl2) relay.

The protocol is specified in PROTOCOL.md at the repository root.
"""

from importlib.metadata import PackageNotFoundError, version

try:
    __version__ = version("pyronix-cloud")
except PackageNotFoundError:  # pragma: no cover - only when run from a raw checkout
    __version__ = "0.0.0"

__all__ = ["__version__"]
