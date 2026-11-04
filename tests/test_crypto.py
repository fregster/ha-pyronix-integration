# SPDX-FileCopyrightText: 2026 Paul Fryer
# SPDX-License-Identifier: LGPL-3.0-or-later
"""Tests for pyronix_cloud.crypto, with line-for-line Java ports as oracles."""

from __future__ import annotations

import hashlib
import struct

from cryptography.hazmat.primitives import padding, serialization
from cryptography.hazmat.primitives.asymmetric import ec
from cryptography.hazmat.primitives.ciphers import Cipher, algorithms, modes
from hypothesis import given
from hypothesis import strategies as st
import pytest

from pyronix_cloud import crypto
from pyronix_cloud.errors import HandshakeError

PANEL_ID = "12345678"
KEY = bytes(range(16))
JAVA_ENCRYPTED_TYPES = (100, 101, 103, 68, 69, 71)

# --------------------------------------------------------------------------
# Line-for-line ports of the Java (PanelEncrypt.java, AES128.java)
# --------------------------------------------------------------------------


def java_encrypt_ecb(key: bytes, data: bytes) -> bytes:
    """AES128.encryptECB: "AES/ECB/PKCS5Padding" doFinal (output grows a block)."""
    padder = padding.PKCS7(128).padder()
    padded = padder.update(data) + padder.finalize()
    ecb = modes.ECB()  # noqa: S305 - porting the app's AES/ECB/PKCS5Padding call
    encryptor = Cipher(algorithms.AES(key), ecb).encryptor()
    return encryptor.update(padded) + encryptor.finalize()


def java_int_array_to_byte_array(ints: list[int]) -> bytes:
    out = bytearray(len(ints) * 4)
    for i, v in enumerate(ints):
        out[i * 4 + 0] = v & 0xFF
        out[i * 4 + 1] = (v >> 8) & 0xFF
        out[i * 4 + 2] = (v >> 16) & 0xFF
        out[i * 4 + 3] = (v >> 24) & 0xFF
    return bytes(out)


def java_encrypt_message(key: bytes, tx: bytes, appl_seq: int) -> bytes:
    """PanelEncrypt.encryptMessage (PanelEncrypt.java:484-526), line for line."""
    b_arr = bytearray(tx)
    assert len(b_arr) >= 21
    length2 = len(b_arr)
    b3 = b_arr[0]
    if b3 not in JAVA_ENCRYPTED_TYPES:
        return bytes(b_arr)
    i4 = length2 - 5
    nonce = java_int_array_to_byte_array([b3, length2 - 21, appl_seq, 0])
    ecb = bytearray(java_encrypt_ecb(key, nonce))
    i5 = 0
    i6 = 0
    while i4 > 0:
        if i5 == 0:
            ecb = bytearray(java_encrypt_ecb(key, bytes(ecb)))
        i7 = i6 + 5
        b4 = b_arr[i7] ^ ecb[i5]
        b_arr[i7] = b4
        ecb[i5] = b4
        i5 = (i5 + 1) & 15
        i4 -= 1
        i6 += 1
    return bytes(b_arr)


def java_decrypt_message(key: bytes, rcv: bytes, seq: int) -> bytes:
    """PanelEncrypt.decryptMessage (PanelEncrypt.java:373-407), line for line."""
    b_arr = bytearray(rcv)
    length = len(b_arr)
    b3 = b_arr[0]
    if b3 not in JAVA_ENCRYPTED_TYPES:
        return bytes(b_arr)
    i3 = length - 5
    nonce = java_int_array_to_byte_array([b_arr[0], length - 21, seq, 0])
    ecb = bytearray(java_encrypt_ecb(key, nonce))
    i4 = 0
    i5 = 0
    while i3 > 0:
        if i4 == 0:
            ecb = bytearray(java_encrypt_ecb(key, bytes(ecb)))
        i6 = i5 + 5
        b4 = b_arr[i6]
        b_arr[i6] = ecb[i4] ^ b4
        ecb[i4] = b4
        i4 = (i4 + 1) & 15
        i3 -= 1
        i5 += 1
    return bytes(b_arr)


def plain_frame(type_byte: int, seq: int, payload: bytes) -> bytes:
    """makeUnencryptMsg: header, payload, then SHA-256[:16] over both."""
    header = struct.pack("<BHH", type_byte, seq & 0xFFFF, len(payload))
    return header + payload + hashlib.sha256(header + payload).digest()[:16]


def ours_encrypt(key: bytes, frame: bytes, seq: int) -> bytes:
    body = crypto.cfb_encrypt(key, frame[0], len(frame) - 21, seq, frame[5:])
    return frame[:5] + body


def ours_decrypt(key: bytes, frame: bytes, seq: int) -> bytes:
    body = crypto.cfb_decrypt(key, frame[0], len(frame) - 21, seq, frame[5:])
    return frame[:5] + body


# --------------------------------------------------------------------------
# CFB equivalence with the app's manual loops
# --------------------------------------------------------------------------


@pytest.mark.parametrize("n", [0, 1, 7, 16, 30, 200])
@pytest.mark.parametrize("seq", [1, 2, 300, 0x12345678, 0xFFFFFFFF])
@pytest.mark.parametrize("type_byte", JAVA_ENCRYPTED_TYPES)
def test_cfb_matches_java_loops(n: int, seq: int, type_byte: int) -> None:
    payload = bytes((i * 31 + 3) & 0xFF for i in range(n))
    plain = plain_frame(type_byte, seq, payload)
    java_cipher = java_encrypt_message(KEY, plain, seq)
    assert ours_encrypt(KEY, plain, seq) == java_cipher
    assert java_cipher[5:] != plain[5:]
    assert java_decrypt_message(KEY, java_cipher, seq) == plain
    assert ours_decrypt(KEY, java_cipher, seq) == plain


@given(
    key=st.binary(min_size=16, max_size=16),
    type_byte=st.sampled_from(JAVA_ENCRYPTED_TYPES),
    seq=st.integers(min_value=0, max_value=0xFFFFFFFF),
    payload=st.binary(max_size=300),
)
def test_cfb_matches_java_loops_property(
    key: bytes, type_byte: int, seq: int, payload: bytes
) -> None:
    plain = plain_frame(type_byte, seq, payload)
    java_cipher = java_encrypt_message(key, plain, seq)
    assert ours_encrypt(key, plain, seq) == java_cipher
    assert ours_decrypt(key, java_cipher, seq) == plain
    assert java_decrypt_message(key, java_cipher, seq) == plain


def test_cfb_comes_from_decrepit_module_when_available() -> None:
    # cryptography 46 (HA 2026.2, the Python 3.13 CI leg) has no decrepit module and
    # the library falls back to the old location there.
    decrepit = pytest.importorskip("cryptography.hazmat.decrepit.ciphers.modes")
    assert vars(crypto)["CFB"] is decrepit.CFB


# --------------------------------------------------------------------------
# Nonce / IV
# --------------------------------------------------------------------------


def test_frame_iv_is_ecb_of_nonce() -> None:
    nonce = struct.pack("<IIII", 0x44, 3, 0x01020304, 0)
    want = java_encrypt_ecb(KEY, nonce)[:16]
    assert crypto.frame_iv(KEY, ord("D"), 3, 0x01020304) == want


def test_frame_iv_masks_seq_and_sign_extends_high_type() -> None:
    assert crypto.frame_iv(KEY, 0x44, 0, -1) == crypto.frame_iv(KEY, 0x44, 0, 2**32 - 1)
    assert crypto.frame_iv(KEY, 0x44, 0, 2**32 + 5) == crypto.frame_iv(KEY, 0x44, 0, 5)
    signed = struct.pack("<iiII", -0x7F, 0, 0, 0)  # Java sign-extends byte 0x81
    want = java_encrypt_ecb(KEY, signed)[:16]
    assert crypto.frame_iv(KEY, 0x81, 0, 0) == want


@pytest.mark.parametrize(
    ("key", "type_byte", "length"),
    [
        (bytes(15), 0x44, 0),
        (bytes(32), 0x44, 0),
        (KEY, -1, 0),
        (KEY, 256, 0),
        (KEY, 0x44, -1),
        (KEY, 0x44, 2**31),
    ],
)
def test_frame_iv_rejects_programmer_errors(
    key: bytes, type_byte: int, length: int
) -> None:
    with pytest.raises(ValueError, match=r"key|type|length"):
        crypto.frame_iv(key, type_byte, length, 1)


# --------------------------------------------------------------------------
# check16, fixed_key, session key
# --------------------------------------------------------------------------


def test_check16() -> None:
    assert crypto.check16(b"abc") == hashlib.sha256(b"abc").digest()[:16]
    assert len(crypto.check16(b"")) == crypto.CHECK_LEN


def test_fixed_key_short_password() -> None:
    want = hashlib.sha256(b"secretpw\x00" + PANEL_ID.encode()).digest()[:16]
    assert crypto.fixed_key("secretpw", PANEL_ID) == want


def test_fixed_key_known_answer_short() -> None:
    # SHA-256(b"secretpw\x0012345678")[:16], computed once with sha256sum.
    want = bytes.fromhex("27e9258d63d88ab4b0d597e522fa03de")
    assert crypto.fixed_key("secretpw", "12345678") == want
    assert len(want) == crypto.KEY_LEN


def test_fixed_key_31_chars_still_hashed() -> None:
    pwd = "x" * 31
    want = hashlib.sha256(pwd.encode() + b"\x00" + PANEL_ID.encode()).digest()[:16]
    assert crypto.fixed_key(pwd, PANEL_ID) == want


def test_fixed_key_long_decimal_password() -> None:
    pwd = "0123456789" * 4  # 40 chars; only the first 32 reach the 16-byte key
    want = bytes.fromhex("01234567890123456789012345678901")
    assert crypto.fixed_key(pwd, PANEL_ID) == want


def test_fixed_key_long_hex_letters_count_as_zero() -> None:
    # Kotlin digitToIntOrNull() is radix 10: a-f and A-F give 0, unlike int(c, 16).
    assert crypto.fixed_key("ab" * 16, PANEL_ID) == bytes(16)
    assert crypto.fixed_key("1a" * 16, PANEL_ID) == bytes([0x10]) * 16
    assert crypto.fixed_key("F9" * 16, PANEL_ID) == bytes([0x09]) * 16
    assert crypto.fixed_key("9" * 32 + "1", PANEL_ID) == bytes([0x99]) * 16


def test_fixed_key_long_ignores_panel_id() -> None:
    pwd = "12" * 20
    assert crypto.fixed_key(pwd, "A") == crypto.fixed_key(pwd, "B")


def test_fixed_key_counts_utf16_units_like_java() -> None:
    # 16 astral chars = 32 UTF-16 units: Java takes the long branch, all units 0.
    pwd = "\U0001f600" * 16
    assert crypto.fixed_key(pwd, PANEL_ID) == bytes(16)
    # Unicode decimal digits count, as Character.digit(c, 10) does.
    assert crypto.fixed_key("٣" * 32, PANEL_ID) == bytes([0x33]) * 16


def test_fixed_key_lone_surrogate_is_replaced_like_java() -> None:
    want = hashlib.sha256(b"a?\x00" + PANEL_ID.encode()).digest()[:16]
    assert crypto.fixed_key("a\ud800", PANEL_ID) == want


def test_derive_session_key() -> None:
    fixed, shared, extra = bytes(16), bytes(range(32)), b"\x01\x02"
    want = hashlib.sha256(fixed + shared + extra).digest()[:16]
    assert crypto.derive_session_key(fixed, shared, extra) == want


# --------------------------------------------------------------------------
# ECDH
# --------------------------------------------------------------------------


def test_ecdh_pairs_agree() -> None:
    app, panel = crypto.EcdhKeyPair.generate(), crypto.EcdhKeyPair.generate()
    assert len(app.public_bytes) == 65
    assert app.public_bytes[0] == 0x04
    shared = app.shared_secret(panel.public_bytes)
    assert len(shared) == 32
    assert shared == panel.shared_secret(app.public_bytes)


def test_ecdh_accepts_compressed_key() -> None:
    app = crypto.EcdhKeyPair.generate()
    panel_priv = ec.generate_private_key(ec.SECP256R1())
    compressed = panel_priv.public_key().public_bytes(
        serialization.Encoding.X962, serialization.PublicFormat.CompressedPoint
    )
    assert len(compressed) == 33
    app_pub = ec.EllipticCurvePublicKey.from_encoded_point(
        ec.SECP256R1(), app.public_bytes
    )
    assert app.shared_secret(compressed) == panel_priv.exchange(ec.ECDH(), app_pub)


@pytest.mark.parametrize("length", [0, 1, 32, 34, 64, 66])
def test_ecdh_rejects_bad_length(length: int) -> None:
    with pytest.raises(HandshakeError, match="length"):
        crypto.EcdhKeyPair.generate().shared_secret(b"\x04" * length)


@pytest.mark.parametrize(
    "key",
    [
        b"\x04" + bytes(64),  # (0, 0) is not on the curve
        b"\x04" + b"\x01" * 64,
        b"\x05" + bytes(64),  # bad prefix
        b"\x02" + b"\xff" * 32,  # x >= p
        b"\x04" + bytes(32),  # uncompressed prefix with compressed length
    ],
)
def test_ecdh_rejects_bad_point(key: bytes) -> None:
    with pytest.raises(HandshakeError, match="point"):
        crypto.EcdhKeyPair.generate().shared_secret(key)


def test_ecdh_rejects_other_curve() -> None:
    with pytest.raises(TypeError, match="P-256"):
        crypto.EcdhKeyPair(ec.generate_private_key(ec.SECP384R1()))
