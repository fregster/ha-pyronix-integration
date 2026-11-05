# SPDX-FileCopyrightText: 2026 Paul Fryer
# SPDX-License-Identifier: LGPL-3.0-or-later
"""Cryptographic primitives of the encrypted panel session (PROTOCOL.md §4.4-4.7).

Everything here is a pure function of its inputs except :class:`EcdhKeyPair`, which
holds one ephemeral P-256 private key for a single handshake.
"""

from __future__ import annotations

import hashlib
import importlib
import struct
from typing import TYPE_CHECKING, cast
import unicodedata

from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric import ec
from cryptography.hazmat.primitives.ciphers import Cipher, algorithms, modes

from .errors import HandshakeError

if TYPE_CHECKING:
    from collections.abc import Callable


def _cfb_mode_class() -> Callable[[bytes], modes.ModeWithInitializationVector]:
    """Return AES-CFB from its current home, falling back for older cryptography.

    cryptography >= 47 moved CFB to ``hazmat.decrepit`` and deprecates the old name
    (PROTOCOL.md §11). Home Assistant 2026.2 (our Python 3.13 floor) ships 46, which
    only has the old one. Resolved dynamically so type-checking does not depend on
    the installed version.
    """
    try:
        home = importlib.import_module("cryptography.hazmat.decrepit.ciphers.modes")
    except ImportError:  # pragma: no cover - cryptography < 47 only
        home = modes
    return cast("Callable[[bytes], modes.ModeWithInitializationVector]", home.CFB)


CFB = _cfb_mode_class()

KEY_LEN = 16
"""Length in bytes of the fixed key and of the AES-128 session key."""

CHECK_LEN = 16
"""Length in bytes of the truncated SHA-256 frame check."""

_SHORT_PASSWORD_MAX = 31
"""Passwords up to this many UTF-16 code units are hashed (PROTOCOL.md §4.5)."""

_HEX_PASSWORD_UNITS = 2 * KEY_LEN
"""Characters of a long password that reach the 16-byte key."""

_PUBLIC_KEY_LENGTHS = frozenset({33, 65})
"""Accepted SEC1 encodings of a peer key: compressed or uncompressed."""

_MAX_INT32 = 0x7FFF_FFFF
_SIGN_BIT = 0x80
_BYTE = 0x100


def check16(data: bytes) -> bytes:
    """Return the frame check: the first 16 bytes of SHA-256 over ``data``.

    Args:
        data: The plaintext header and payload of a frame.

    Returns:
        The 16-byte check value.
    """
    return hashlib.sha256(data).digest()[:CHECK_LEN]


def _utf16_units(text: str) -> list[str]:
    """Split ``text`` into UTF-16 code units, as Java's ``String.charAt`` sees it."""
    raw = text.encode("utf-16-le", "surrogatepass")
    return [
        chr(int.from_bytes(raw[i : i + 2], "little")) for i in range(0, len(raw), 2)
    ]


def _hex_password_key(units: list[str]) -> bytes:
    """Port ``convertHexPasswordToKey`` with its radix-10 quirk (PanelEncrypt.java:202).

    Kotlin's ``digitToIntOrNull()`` without a radix only knows decimal digits, so
    ``a``-``f`` count as 0 like any other non-digit.
    """
    nibbles = [unicodedata.decimal(unit, 0) for unit in units[:_HEX_PASSWORD_UNITS]]
    nibbles += [0] * (_HEX_PASSWORD_UNITS - len(nibbles))
    return bytes((nibbles[i] << 4) | nibbles[i + 1] for i in range(0, len(nibbles), 2))


def fixed_key(app_password: str, panel_id: str) -> bytes:
    """Derive the 16-byte fixed key from the app password (``setPanelInfo``).

    Passwords of up to 31 UTF-16 code units are hashed with the panel ID. Longer
    ones are read as "hex" exactly like the app does, where only decimal digits
    count (PROTOCOL.md §4.5).

    Args:
        app_password: The panel's app password.
        panel_id: The panel's System ID.

    Returns:
        The 16-byte fixed key.
    """
    units = _utf16_units(app_password)
    if len(units) <= _SHORT_PASSWORD_MAX:
        # Java's getBytes(UTF_8) turns lone surrogates into "?", as "replace" does.
        material = (
            app_password.encode("utf-8", "replace")
            + b"\x00"
            + panel_id.encode("utf-8", "replace")
        )
        return hashlib.sha256(material).digest()[:KEY_LEN]
    return _hex_password_key(units)


def _require_key(key: bytes) -> None:
    """Raise ValueError unless ``key`` is a 16-byte AES-128 key."""
    if len(key) != KEY_LEN:
        msg = f"AES key must be {KEY_LEN} bytes, got {len(key)}"
        raise ValueError(msg)


def frame_iv(key: bytes, frame_type: int, payload_len: int, seq: int) -> bytes:
    """Return the CFB IV of one frame: AES-ECB of the frame nonce (PROTOCOL.md §4.7).

    The nonce is ``LE32(type) ‖ LE32(len) ‖ LE32(seq) ‖ LE32(0)``. Like the Java,
    a type byte of 0x80 or more is sign-extended.

    Args:
        key: The 16-byte session key.
        frame_type: The frame's type byte (0..255).
        payload_len: The frame's payload length (0..2**31-1).
        seq: The full sequence number; taken modulo 2**32.

    Returns:
        The 16-byte IV.

    Raises:
        ValueError: The key, type byte or length is out of range.
    """
    _require_key(key)
    if not 0 <= frame_type < _BYTE:
        msg = f"frame type must be a byte, got {frame_type}"
        raise ValueError(msg)
    if not 0 <= payload_len <= _MAX_INT32:
        msg = f"payload length out of range: {payload_len}"
        raise ValueError(msg)
    signed_type = frame_type - _BYTE if frame_type >= _SIGN_BIT else frame_type
    nonce = struct.pack("<iiII", signed_type, payload_len, seq & 0xFFFF_FFFF, 0)
    # One block, as the protocol requires: the IV is AES-ECB of the nonce.
    ecb = modes.ECB()  # noqa: S305 - single-block IV derivation mandated by §4.7
    encryptor = Cipher(algorithms.AES(key), ecb).encryptor()
    return encryptor.update(nonce) + encryptor.finalize()


def cfb_encrypt(
    key: bytes, frame_type: int, payload_len: int, seq: int, data: bytes
) -> bytes:
    """Encrypt ``data`` with AES-128-CFB-128 under the frame's IV.

    Args:
        key: The 16-byte session key.
        frame_type: The frame's type byte.
        payload_len: The frame's payload length, used in the nonce.
        seq: The full sequence number, used in the nonce.
        data: The plaintext, i.e. frame bytes 5 to the end (payload and check).

    Returns:
        The ciphertext, the same length as ``data``.
    """
    iv = frame_iv(key, frame_type, payload_len, seq)
    encryptor = Cipher(algorithms.AES(key), CFB(iv)).encryptor()
    return encryptor.update(data) + encryptor.finalize()


def cfb_decrypt(
    key: bytes, frame_type: int, payload_len: int, seq: int, data: bytes
) -> bytes:
    """Decrypt ``data`` with AES-128-CFB-128 under the frame's IV.

    Args:
        key: The 16-byte session key.
        frame_type: The frame's type byte.
        payload_len: The frame's payload length, used in the nonce.
        seq: The full sequence number, used in the nonce.
        data: The ciphertext, i.e. frame bytes 5 to the end.

    Returns:
        The plaintext, the same length as ``data``.
    """
    iv = frame_iv(key, frame_type, payload_len, seq)
    decryptor = Cipher(algorithms.AES(key), CFB(iv)).decryptor()
    return decryptor.update(data) + decryptor.finalize()


def derive_session_key(fixed: bytes, shared: bytes, announce_extra: bytes) -> bytes:
    """Derive the AES session key: ``SHA-256(fixed ‖ shared ‖ extra)[:16]`` (§4.4).

    Args:
        fixed: The 16-byte fixed key from :func:`fixed_key`.
        shared: The 32-byte ECDH shared secret.
        announce_extra: The 0..4 extra bytes saved from the panel's announce.

    Returns:
        The 16-byte session key.
    """
    return hashlib.sha256(fixed + shared + announce_extra).digest()[:KEY_LEN]


class EcdhKeyPair:
    """An ephemeral P-256 key pair for one panel handshake."""

    __slots__ = ("_private_key",)

    def __init__(self, private_key: ec.EllipticCurvePrivateKey) -> None:
        """Wrap an existing P-256 private key.

        Args:
            private_key: A private key on the SECP256R1 curve.

        Raises:
            TypeError: The key is on another curve.
        """
        if not isinstance(private_key.curve, ec.SECP256R1):
            msg = "ECDH key must be on P-256 (SECP256R1)"
            raise TypeError(msg)
        self._private_key = private_key

    @classmethod
    def generate(cls) -> EcdhKeyPair:
        """Generate a fresh random key pair.

        Returns:
            A new key pair.
        """
        return cls(ec.generate_private_key(ec.SECP256R1()))

    @property
    def public_bytes(self) -> bytes:
        """The public key as a 65-byte uncompressed SEC1 point, ``04 ‖ X ‖ Y``."""
        return self._private_key.public_key().public_bytes(
            serialization.Encoding.X962, serialization.PublicFormat.UncompressedPoint
        )

    def shared_secret(self, peer_public: bytes) -> bytes:
        """Compute the 32-byte ECDH shared secret with the peer's public key.

        Args:
            peer_public: The peer's key as a 33-byte compressed or 65-byte
                uncompressed SEC1 point.

        Returns:
            The X coordinate of the shared point (32 bytes).

        Raises:
            HandshakeError: The key has a bad length or is not a valid P-256 point.
        """
        if len(peer_public) not in _PUBLIC_KEY_LENGTHS:
            msg = f"panel public key has length {len(peer_public)}, want 33 or 65"
            raise HandshakeError(msg)
        try:
            peer = ec.EllipticCurvePublicKey.from_encoded_point(
                ec.SECP256R1(), peer_public
            )
            return self._private_key.exchange(ec.ECDH(), peer)
        except ValueError as err:
            msg = "panel public key is not a valid P-256 point"
            raise HandshakeError(msg) from err
