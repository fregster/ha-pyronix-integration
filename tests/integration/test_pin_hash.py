# SPDX-FileCopyrightText: 2026 Paul Fryer
# SPDX-License-Identifier: Apache-2.0
from __future__ import annotations

import pytest

from custom_components.pyronix_cloud.pin_hash import hash_pin, pin_matches


def test_hash_is_salted_and_hides_the_pin() -> None:
    first, second = hash_pin("4711"), hash_pin("4711")
    assert first != second  # a fresh salt each time
    assert "4711" not in first
    assert first.startswith("scrypt$")


def test_matching() -> None:
    stored = hash_pin("123456")
    assert pin_matches("123456", stored)
    assert not pin_matches("123457", stored)
    assert not pin_matches("", stored)


@pytest.mark.parametrize(
    "stored",
    [
        "",
        "plain",
        "pbkdf2$16384$8$1$00$00",
        "scrypt$x$8$1$00$00",
        "scrypt$16384$8$1$zz$00",
        "scrypt$16384$8$1$00",
        "scrypt$3$8$1$00$00",  # n must be a power of two above 1
    ],
)
def test_malformed_hash_never_matches(stored: str) -> None:
    assert not pin_matches("1234", stored)
