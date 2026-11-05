# SPDX-FileCopyrightText: 2026 Paul Fryer
# SPDX-License-Identifier: LGPL-3.0-or-later
"""Async client for Pyronix panels via the Pyronix Cloud (HomeControl2) relay.

The protocol is specified in PROTOCOL.md at the repository root.
"""

from importlib.metadata import PackageNotFoundError, version

from pyronix_cloud.backoff import Backoff
from pyronix_cloud.client import PyronixClient
from pyronix_cloud.cloud import LIVE_URL, TEST_URL, AppProfile
from pyronix_cloud.errors import (
    AccessDeniedError,
    AuthenticationError,
    CloudError,
    CommandNotConfirmedError,
    CommunicationError,
    ConnectionClosedError,
    DocumentError,
    FrameError,
    HandshakeError,
    NotConnectedError,
    NotPermittedError,
    PanelBusyError,
    PanelOfflineError,
    ProtocolError,
    PyronixError,
    PyronixTimeoutError,
    SetRefusedError,
    SubscriptionExpiredError,
    UnknownCloudReplyError,
)
from pyronix_cloud.models import (
    Area,
    AreaDocument,
    AreaEvent,
    AreaStatus,
    FaultDocument,
    Input,
    InputDocument,
    InputState,
    LogDocument,
    LogEntry,
    Output,
    OutputDocument,
    PanelDocument,
    PanelFault,
    PanelInfo,
    SetType,
    UnknownDocument,
    User,
    UserDocument,
)
from pyronix_cloud.not_polling_retry import NO_RETRY, RetryPolicy
from pyronix_cloud.panel_state import PanelSnapshot
from pyronix_cloud.settings import Credentials, Timeouts
from pyronix_cloud.websocket import aiohttp_connector

try:
    __version__ = version("pyronix-cloud")
except PackageNotFoundError:  # pragma: no cover - only when run from a raw checkout
    __version__ = "0.0.0"

__all__ = [
    "LIVE_URL",
    "NO_RETRY",
    "TEST_URL",
    "AccessDeniedError",
    "AppProfile",
    "Area",
    "AreaDocument",
    "AreaEvent",
    "AreaStatus",
    "AuthenticationError",
    "Backoff",
    "CloudError",
    "CommandNotConfirmedError",
    "CommunicationError",
    "ConnectionClosedError",
    "Credentials",
    "DocumentError",
    "FaultDocument",
    "FrameError",
    "HandshakeError",
    "Input",
    "InputDocument",
    "InputState",
    "LogDocument",
    "LogEntry",
    "NotConnectedError",
    "NotPermittedError",
    "Output",
    "OutputDocument",
    "PanelBusyError",
    "PanelDocument",
    "PanelFault",
    "PanelInfo",
    "PanelOfflineError",
    "PanelSnapshot",
    "ProtocolError",
    "PyronixClient",
    "PyronixError",
    "PyronixTimeoutError",
    "RetryPolicy",
    "SetRefusedError",
    "SetType",
    "SubscriptionExpiredError",
    "Timeouts",
    "UnknownCloudReplyError",
    "UnknownDocument",
    "User",
    "UserDocument",
    "__version__",
    "aiohttp_connector",
]
