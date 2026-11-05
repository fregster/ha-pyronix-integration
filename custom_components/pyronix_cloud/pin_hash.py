# SPDX-FileCopyrightText: 2026 Paul Fryer
# SPDX-License-Identifier: Apache-2.0
"""Salted scrypt hashes of the arm/disarm PIN, checked in constant time.

Both functions are CPU-bound (tens of milliseconds); call them in an executor.
"""

from __future__ import annotations

import hashlib
import hmac
import secrets
from typing import Final

_SCHEME: Final = "scrypt"
_N: Final = 2**14
_R: Final = 8
_P: Final = 1
_SALT_BYTES: Final = 16
_KEY_BYTES: Final = 32
_FIELDS: Final = 6
_MAX_MEMORY: Final = 64 * 1024 * 1024


def _derive(pin: str, salt: bytes, n: int, r: int, p: int) -> bytes:
    """Return the scrypt key of ``pin``."""
    return hashlib.scrypt(
        pin.encode("utf-8"),
        salt=salt,
        n=n,
        r=r,
        p=p,
        maxmem=_MAX_MEMORY,
        dklen=_KEY_BYTES,
    )


def hash_pin(pin: str) -> str:
    """Return ``scrypt$n$r$p$salt$key`` for ``pin``, with a fresh random salt."""
    salt = secrets.token_bytes(_SALT_BYTES)
    key = _derive(pin, salt, _N, _R, _P)
    return f"{_SCHEME}${_N}${_R}${_P}${salt.hex()}${key.hex()}"


def pin_matches(pin: str, stored: str) -> bool:
    """Return whether ``pin`` matches a hash from :func:`hash_pin`.

    A malformed hash never matches.
    """
    fields = stored.split("$")
    if len(fields) != _FIELDS or fields[0] != _SCHEME:
        return False
    try:
        n, r, p = (int(value) for value in fields[1:4])
        salt, key = bytes.fromhex(fields[4]), bytes.fromhex(fields[5])
        candidate = _derive(pin, salt, n, r, p)
    except ValueError:
        return False
    return hmac.compare_digest(candidate, key)
