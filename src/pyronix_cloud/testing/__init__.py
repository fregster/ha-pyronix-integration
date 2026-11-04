# SPDX-FileCopyrightText: 2026 Paul Fryer
# SPDX-License-Identifier: LGPL-3.0-or-later
"""Offline test doubles for the Pyronix Cloud relay and panel.

Nothing in this package opens a network connection unless asked to serve on a local
port. It is shipped with the library so that downstream projects (such as the Home
Assistant integration) can test against the same fake panel.
"""
