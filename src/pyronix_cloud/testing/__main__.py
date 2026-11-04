# SPDX-FileCopyrightText: 2026 Paul Fryer
# SPDX-License-Identifier: LGPL-3.0-or-later
"""Command line that serves the fake Pyronix Cloud on a local port until Ctrl-C.

Run ``python -m pyronix_cloud.testing --port 8765`` and point a development Home
Assistant at the printed URL with the printed fake credentials.
"""

from __future__ import annotations

import argparse
import asyncio
import contextlib
import sys
from typing import TYPE_CHECKING

from pyronix_cloud.testing.fake_cloud import FakeCloud
from pyronix_cloud.testing.fake_panel import PanelBehaviour

if TYPE_CHECKING:
    from collections.abc import Sequence

DEFAULT_PORT = 8765


async def run(host: str, port: int, stop: asyncio.Event) -> None:
    """Serve the fake cloud, print how to reach it, and wait for ``stop``.

    Args:
        host: The address to listen on.
        port: The port; 0 picks a free one.
        stop: Set it to shut down.
    """
    panel = PanelBehaviour()
    async with FakeCloud() as cloud:
        url = await cloud.serve(host, port)
        # These are the fake panel's built-in test values, not real secrets.
        sys.stdout.write(
            f"Fake Pyronix Cloud listening on {url}\n"
            f"  System ID:    {panel.panel_id}\n"
            f"  App password: {panel.app_password}\n"
            f"  User code:    {panel.user_code}\n"
            "Press Ctrl-C to stop.\n"
        )
        sys.stdout.flush()
        await stop.wait()


def main(argv: Sequence[str] | None = None, stop: asyncio.Event | None = None) -> int:
    """Parse the command line and serve until Ctrl-C (or until ``stop`` is set).

    Args:
        argv: The arguments; ``sys.argv[1:]`` if None.
        stop: An event that ends the server; tests set it, people press Ctrl-C.

    Returns:
        The exit status, 0.
    """
    parser = argparse.ArgumentParser(
        prog="python -m pyronix_cloud.testing",
        description="Serve the fake Pyronix Cloud relay for local testing.",
    )
    parser.add_argument("--host", default="127.0.0.1", help="address to listen on")
    parser.add_argument("--port", type=int, default=DEFAULT_PORT, help="TCP port")
    args = parser.parse_args(argv)
    with contextlib.suppress(KeyboardInterrupt):
        asyncio.run(run(args.host, args.port, stop or asyncio.Event()))
    return 0


if __name__ == "__main__":  # pragma: no cover - entry point, main() is tested
    sys.exit(main())
