#!/usr/bin/env python3
"""Read-only probe of the Pyronix Cloud panel protocol (see ../PROTOCOL.md).

Flow: subscription -> req pwd -> conn-to-cloud -> 'u' -> conn-to-panel (raw)
      -> A/a announce -> B/b ECDH -> derive key -> login D ",{code}\\n"
      -> decrypt d frames and pretty-print JSON -> E + close(1001).

The probe never sends set/unset/omit/output opcodes. The only application
opcodes it can send are the heartbeat (0x20) and, behind --request-log, the
log request (0x35).

Everything protocol-related below is written to be importable without a
network (see test_offline.py) and is meant as the seed of `pyronix_cloud`.
"""

from __future__ import annotations

import argparse
import asyncio
import base64
import enum
import hashlib
import json
import logging
import os
import re
import struct
import sys
import time
import unicodedata
import uuid
from dataclasses import dataclass, field
from pathlib import Path

from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric import ec
from cryptography.hazmat.primitives.ciphers import Cipher, algorithms, modes

try:  # cryptography >= 43 moved CFB to "decrepit"
    from cryptography.hazmat.decrepit.ciphers.modes import CFB as _CFB
except ImportError:  # pragma: no cover - older cryptography
    _CFB = modes.CFB

log = logging.getLogger("pyronix_probe")

PROBE_DIR = Path(__file__).resolve().parent
REPO_ROOT = PROBE_DIR.parent
DEVICE_ID_FILE = PROBE_DIR / ".device_id"

# --------------------------------------------------------------------------
# Constants (HomeControl.java, HomeControlKt.java, PanelEncryptKt.java)
# --------------------------------------------------------------------------

LIVE_URL = "wss://app.pyronixcloud.com:443"
TEST_URL = "wss://apptestlb.pyronixcloud.com:443"

APP_VERSION = "6.03.001"  # SdkManagerImpl.P()
APP_TYPE = 16
OS_NAME = "Android"

HEADER_LEN = 5
HASH_LEN = 16
MIN_FRAME = HEADER_LEN + HASH_LEN  # 21
RX_SEQ_WRAP_GAP = 128
MAX_CONSECUTIVE_NACK = 3

ENCRYPTED_TYPES = frozenset(b"dDeEgG")
PANEL_TYPES = frozenset(b"abcdef")  # receiveMessage start bytes ('a'..'f'; 'g' excluded!)

HEARTBEAT_MSG = 0x20
SPI_REQUEST_LOG_EVENTS = 0x35
# Deliberately NOT defined here: 0x2C validate, 0x2D set, 0x2F unset,
# 0x31 output, 0x34 omit. This probe is read-only.
ALLOWED_OPCODES = frozenset({HEARTBEAT_MSG, SPI_REQUEST_LOG_EVENTS})

CLOUD_ERRORS = {
    "a": "ACCESS DENIED (ERROR_ACCESS_DENIED): the cloud rejected AppUser / PanelId / panel "
    "app password. Check PYRONIX_APP_USER, PYRONIX_PANEL_ID and PYRONIX_PANEL_PASSWORD.",
    "b": "PANEL BUSY (ERROR_PANEL_BUSY): another client is probably connected to the panel "
    "(close the HomeControl2 app) or the panel is busy. Try again later.",
    "i": "SUBSCRIPTION EXPIRED (ERROR_PANEL_SUBSCRIPTION_EXPIRED): the panel's Pyronix Cloud "
    "subscription has lapsed.",
    "n": "PANEL NOT POLLING (ERROR_PANEL_NOT_POLLING): the panel is offline / not polling "
    "the cloud. Check its LAN/GPRS uplink.",
}

MODEM_NAMES = {0: "UNKNOWN", 1: "USB", 2: "RS232", 3: "WAN_LAN", 4: "GSM", 5: "GPRS", 6: "PSTN"}


class ProbeError(Exception):
    """Fatal, user-facing error. ``exit_code`` is used by main()."""

    def __init__(self, msg: str, exit_code: int = 3):
        super().__init__(msg)
        self.exit_code = exit_code


# --------------------------------------------------------------------------
# Base64 (Android Base64.DEFAULT)
# --------------------------------------------------------------------------


def android_b64encode(data: bytes) -> str:
    """Android ``Base64.encodeToString(x, Base64.DEFAULT)``.

    DEFAULT = standard alphabet, '=' padding, '\\n' after every 76 output chars
    and a trailing '\\n' after a partial last line. Python's encodebytes() is
    byte-identical (57-byte input chunks, each line '\\n'-terminated; empty
    input gives '').
    """
    return base64.encodebytes(data).decode("ascii")


def android_b64decode(text: bytes | str) -> bytes:
    """Lenient decode like Android Base64.decode(DEFAULT): non-alphabet bytes
    (whitespace, newlines) are skipped. Raises ValueError on bad padding."""
    if isinstance(text, str):
        text = text.encode("latin-1")
    cleaned = re.sub(rb"[^A-Za-z0-9+/=]", b"", text)
    return base64.b64decode(cleaned, validate=False)


# --------------------------------------------------------------------------
# Layer 1: cloud relay messages
# --------------------------------------------------------------------------


class CloudMsg(enum.Enum):
    SUBSCRIPTION = "MSG_SUBSCRIPTION"
    REQ_PWD = "MSG_REQ_PWD"
    CONN_TO_CLOUD = "MSG_CONN_TO_CLOUD"
    CONN_TO_PANEL = "MSG_CONN_TO_PANEL"


@dataclass
class Config:
    app_user: str
    panel_id: str
    panel_password: str
    user_code: str
    system_name: str = "Home"
    device_id: str = ""
    push_id: str = "ha-pyronix-probe"
    cloud_url: str = LIVE_URL
    app_version: str = APP_VERSION
    app_type: int | None = APP_TYPE
    os_name: str = OS_NAME
    os_version: str = "14"
    network_type: str = "Wifi"  # SdkManagerImpl.U(): Wifi / Cellular / Ethernet
    app_lang: str = "uk"  # SdkManagerImpl.T(): effectively always "uk"
    save_app_pwd: int = 0  # the app always sends 0


def generate_password(pwd: str, panel_id: str) -> str:
    """HomeControl.generatePassword (HomeControl.java:770). Only used when SaveAppPwd == 1."""
    if len(pwd) < 32:
        return hashlib.sha256(pwd.encode() + b"\x00" + panel_id.encode()).hexdigest()[:32]
    return pwd


def make_cloud_message(cfg: Config, msg_type: CloudMsg) -> str:
    """Port of HomeControl.makeCloudMessage (HomeControl.java:814-905). Field
    order and the "null" return are exactly as in the Java; no XML escaping."""
    if not cfg.app_user or not cfg.push_id or not cfg.device_id:
        return "null"
    s = f"<p><n>{cfg.app_user}</n><u>{cfg.push_id}</u>"
    if msg_type is CloudMsg.SUBSCRIPTION:
        s += f"<w>{cfg.panel_id}</w>"
    elif msg_type is CloudMsg.REQ_PWD:
        s += f"<j>{cfg.panel_id}</j>"
    elif msg_type in (CloudMsg.CONN_TO_CLOUD, CloudMsg.CONN_TO_PANEL):
        s += f"<a>{cfg.panel_id}</a><b>{cfg.system_name}</b>"
        if cfg.save_app_pwd == 1 and cfg.panel_password is not None:
            s += f"<z>{generate_password(cfg.panel_password, cfg.panel_id)}</z>"
        else:
            s += f"<z>{cfg.panel_password or ''}</z>"
    app_type = cfg.app_type if cfg.app_type is not None else 1
    s += f"<i>{cfg.device_id}</i><l>{cfg.app_version or ''}</l><t>{app_type}</t>"
    s += f"<m>{cfg.os_name or ''}</m><v>{cfg.os_version or ''}</v>"
    s += f"<c>{cfg.network_type or ''}</c><lang>{cfg.app_lang or ''}</lang></p>"
    return s


def wrap_cloud(text: str, to_base64: bool, b64_wrap_lines: bool = True) -> str:
    """HomeControl.sendMessage2Cloud (HomeControl.java:972)."""
    if to_base64:
        raw = text.encode("utf-8")
        enc = android_b64encode(raw) if b64_wrap_lines else base64.b64encode(raw).decode()
        return "(" + enc + ")"
    if not (text.startswith("(") and text.endswith(")")):
        return "(" + text + ")"
    return text


CLOUD_MSG_BASE64 = {
    CloudMsg.SUBSCRIPTION: True,
    CloudMsg.REQ_PWD: True,
    CloudMsg.CONN_TO_CLOUD: True,
    CloudMsg.CONN_TO_PANEL: False,  # sent raw from the 'u' handler (HomeControl.java:681)
}


@dataclass
class CloudReply:
    letter: str
    sep: str
    data: str
    positional: bool  # True when "<e>" was at index 0 (the only case the Java handles)


def parse_cloud_reply(message: str) -> CloudReply | None:
    """HomeControl.didReceiveMessage / getCommandFromXml / getDataFromXml.

    Java: contains("<e>") then letter = substring(3, 4), data = substring(5, len-4).
    That is positional, i.e. it assumes the message starts with "<e>". If "<e>"
    is elsewhere we parse from there and flag it (Java would mis-parse).
    """
    if "<e>" not in message:
        return None
    idx = message.find("<e>")
    m = message[idx:]
    letter = m[3:4]
    sep = m[4:5]
    data = m[5 : len(m) - 4] if len(m) >= 9 else ""
    return CloudReply(letter=letter, sep=sep, data=data, positional=(idx == 0))


# --------------------------------------------------------------------------
# Layer 2: crypto primitives
# --------------------------------------------------------------------------


def check16(data: bytes) -> bytes:
    return hashlib.sha256(data).digest()[:16]


def _java_decimal_digit(ch: str) -> int | None:
    """Kotlin Char.digitToIntOrNull() (radix 10!) -> Character.digit(c, 10)."""
    return unicodedata.decimal(ch, None)


def convert_hex_password_to_key(pwd: str) -> bytes:
    """PanelEncrypt.convertHexPasswordToKey (PanelEncrypt.java:202).

    NB: the app calls digitToIntOrNull() WITHOUT a radix, i.e. radix 10, so
    hex letters a-f/A-F count as 0, just like any other non-decimal char.
    """
    out = bytearray(32)
    i3 = 0
    b3 = 0
    for i4 in range(len(pwd[:64])):
        d = _java_decimal_digit(pwd[i4])
        v = (d if d is not None else 0) & 0xFF
        if i4 % 2 == 1:
            out[i3] = (v | ((b3 & 0xFF) << 4)) & 0xFF
            i3 += 1
        else:
            b3 = v
    return bytes(out)


def fixed_key(pwd: str, panel_id: str) -> bytes:
    """PanelEncrypt.setPanelInfo (PanelEncrypt.java:1232)."""
    if len(pwd) <= 31:
        return hashlib.sha256(pwd.encode("utf-8") + b"\x00" + panel_id.encode("utf-8")).digest()[:16]
    return convert_hex_password_to_key(pwd)[:16]


def make_nonce(ftype: int, plen: int, seq: int) -> bytes:
    """intArrayToByteArray([type, len, seq, 0]) little-endian (PanelEncrypt.java:921)."""
    if ftype >= 0x80:  # Java sign-extends bArr[0]; irrelevant for ASCII letters
        ftype -= 0x100
    return struct.pack("<iiII", ftype, plen, seq & 0xFFFFFFFF, 0)


def aes_ecb_block(key: bytes, block16: bytes) -> bytes:
    enc = Cipher(algorithms.AES(key), modes.ECB()).encryptor()
    return enc.update(block16) + enc.finalize()


def cfb_crypt(key: bytes, ftype: int, plen: int, seq: int, data: bytes, encrypt: bool) -> bytes:
    """AES-128-CFB-128 with IV = AES-ECB_k(nonce) (PanelEncrypt.java:373, 484)."""
    iv = aes_ecb_block(key, make_nonce(ftype, plen, seq))
    c = Cipher(algorithms.AES(key), _CFB(iv))
    op = c.encryptor() if encrypt else c.decryptor()
    return op.update(data) + op.finalize()


def build_frame(ftype: int | str, seq: int, payload: bytes) -> bytes:
    """makeUnencryptMsg (PanelEncrypt.java:960): plaintext frame with check."""
    t = ord(ftype) if isinstance(ftype, str) else ftype
    hdr = bytes([t]) + struct.pack("<HH", seq & 0xFFFF, len(payload) & 0xFFFF)
    return hdr + payload + check16(hdr + payload)


def encrypt_frame(key: bytes | None, ftype: int | str, seq: int, payload: bytes) -> bytes:
    """Build a frame; encrypt bytes 5.. for D/E/G. ``seq`` is the full 32-bit counter."""
    f = build_frame(ftype, seq, payload)
    if f[0] not in ENCRYPTED_TYPES:
        return f
    if key is None:
        raise ProbeError("internal: encrypted frame requested before key agreement")
    return f[:5] + cfb_crypt(key, f[0], len(payload), seq, f[5:], True)


def decrypt_frame(key: bytes | None, frame: bytes, seq_full: int) -> bytes | None:
    """Decrypt (if needed) and verify. Returns the plaintext payload, or None.

    Like the Java (decryptMessage / checkDecryptMessage) the payload length is
    taken from the frame's actual length, not the header's len field.
    """
    if len(frame) < MIN_FRAME:
        return None
    plen = len(frame) - MIN_FRAME
    if frame[0] in ENCRYPTED_TYPES:
        if key is None:
            return None
        plain = frame[:5] + cfb_crypt(key, frame[0], plen, seq_full, frame[5:], False)
    else:
        plain = frame
    if check16(plain[: len(plain) - 16]) != plain[len(plain) - 16 :]:
        return None
    return plain[5 : 5 + plen]


def wrap_frame(frame: bytes, b64_wrap_lines: bool = True) -> str:
    """encodeSendData (PanelEncrypt.java:465) with mOutBase64 = true."""
    enc = android_b64encode(frame) if b64_wrap_lines else base64.b64encode(frame).decode()
    return "(" + enc + ")"


# P-256 parameters, for the manual decompression port
P256_P = 0xFFFFFFFF00000001000000000000000000000000FFFFFFFFFFFFFFFFFFFFFFFF
P256_A = P256_P - 3
P256_B = 0x5AC635D8AA3A93E7B3EBBD55769886BC651D06B0CC53B0F63BCE3C3E27D2604B


def decompress_point(x_bytes: bytes, y_odd: bool) -> bytes:
    """PanelEncrypt.decompressPublicKey (PanelEncrypt.java:339). Returns 04||X||Y."""
    x = int.from_bytes(x_bytes, "big")
    p = P256_P
    y = pow((pow(x, 3, p) + P256_A * x + P256_B) % p, (p + 1) // 4, p)
    if (y & 1) != int(y_odd):
        y = p - y
    return b"\x04" + x.to_bytes(32, "big") + y.to_bytes(32, "big")


def load_panel_public_key(key_bytes: bytes) -> ec.EllipticCurvePublicKey:
    """struRcvPublicKey: 33-byte compressed or 65-byte uncompressed SEC1 point."""
    if len(key_bytes) not in (33, 65):
        raise ProbeError(f"panel ECDH key has unexpected length {len(key_bytes)} (want 33 or 65)")
    return ec.EllipticCurvePublicKey.from_encoded_point(ec.SECP256R1(), key_bytes)


def derive_session_key(fixed: bytes, shared: bytes, announce_extra: bytes) -> bytes:
    return hashlib.sha256(fixed + shared + announce_extra).digest()[:16]


# --------------------------------------------------------------------------
# Layer 2: receive path and sequence handling
# --------------------------------------------------------------------------


class FrameReceiver:
    """PanelEncrypt.receivedFromPanel + receiveMessage (PanelEncrypt.java:1104).

    Implements the *intended* behaviour. Two Java bugs are deliberately not
    copied: ``ArraysKt.drop()`` results are discarded (so the garbage skip and
    the raw-binary consume are no-ops in the app), and the raw length uses
    sign-extended bytes.
    """

    def __init__(self) -> None:
        self.buf = bytearray()

    def feed(self, data: bytes) -> None:
        self.buf += data

    def next_frame(self) -> tuple[bytes, str] | None:
        """Return (frame, form) with form 'b64' or 'raw', or None if more data is needed."""
        skipped = 0
        while self.buf and not (self.buf[0] == 0x28 or self.buf[0] in PANEL_TYPES):
            del self.buf[0]
            skipped += 1
        if skipped:
            log.debug("receiver: skipped %d leading byte(s)", skipped)
        if len(self.buf) < MIN_FRAME:
            return None
        if self.buf[0] == 0x28:  # '('
            end = self.buf.find(b")")
            if end < 0:
                return None
            blob = bytes(self.buf[1:end])
            del self.buf[: end + 1]
            try:
                frame = android_b64decode(blob)
            except ValueError:  # binascii.Error is a ValueError
                frame = b""  # Java: IllegalArgumentException -> empty -> NACK
            return frame, "b64"
        n = (self.buf[3] | (self.buf[4] << 8)) + MIN_FRAME
        if len(self.buf) < n:
            return None
        frame = bytes(self.buf[:n])
        del self.buf[:n]
        return frame, "raw"


def accept_panel_seq(last: int, seq16: int) -> int | None:
    """checkDecryptMessage seq rules (PanelEncrypt.java:259-273), exactly.

    Returns the "full" seq to use for the nonce and to store as the new
    mPanelMsgSeq, or None to reject. Note the Java only adds the high 16 bits
    on the frame that wraps; in the normal branch the full seq is just seq16.
    """
    last &= 0xFFFFFFFF
    if last == 0 and seq16 != 0:
        return seq16 if seq16 < RX_SEQ_WRAP_GAP else None
    last16 = last & 0xFFFF
    if seq16 < last16:
        if seq16 >= RX_SEQ_WRAP_GAP or last16 <= 65536 - RX_SEQ_WRAP_GAP:
            return None
        return (seq16 | ((last & 0xFFFF0000) + 0x10000)) & 0xFFFFFFFF
    if seq16 <= last16 or seq16 - last16 >= RX_SEQ_WRAP_GAP:
        return None
    return seq16


class State(enum.Enum):
    BEGIN = "BEGIN"
    GET_ANNOUNCE = "GET_ANNOUNCE"
    RECV_ECDH = "RECV_ECDH"
    DATA_TRANSFER = "DATA_TRANSFER"
    CLOSED = "CLOSED"


@dataclass
class Event:
    kind: str  # announce, key, data, photo, bye, error, info
    detail: dict = field(default_factory=dict)


class PanelSession:
    """Sans-IO port of PanelEncrypt. Methods return lists of wire strings to send."""

    def __init__(self, panel_id: str, password: str, which_key: int = 1, b64_wrap_lines: bool = True, nack: bool = True):
        self.panel_id = panel_id
        self.panel_id_bytes = panel_id.encode("utf-8")
        self.fixed = fixed_key(password, panel_id)
        self.which_key = which_key
        self.b64_wrap_lines = b64_wrap_lines
        self.nack_enabled = nack
        self.state = State.BEGIN
        self.app_seq = 0
        self.panel_seq = 0
        self.rcv_nacks = 0
        self.sent_nacks = 0
        self.key: bytes | None = None
        self.priv: ec.EllipticCurvePrivateKey | None = None
        self.modem = 0
        self.announce_extra = b""
        self.last_tx: bytes | None = None
        self.receiver = FrameReceiver()

    # ---- sending -----------------------------------------------------------

    def _next_seq(self) -> int:
        self.app_seq = (self.app_seq + 1) & 0xFFFFFFFF
        return self.app_seq

    def _send(self, ftype: str, payload: bytes) -> str:
        seq = self._next_seq()
        frame = encrypt_frame(self.key, ftype, seq, payload)
        self.last_tx = frame
        wire = wrap_frame(frame, self.b64_wrap_lines)
        log.debug("TX frame %s seq=%d len=%d frame=%s", ftype, seq, len(payload), frame.hex())
        return wire

    def announce(self) -> str:
        self.state = State.GET_ANNOUNCE
        return self._send("A", b"")

    def ecdh(self) -> str:
        if self.priv is None:  # createPublicKey keeps an existing key pair
            self.priv = ec.generate_private_key(ec.SECP256R1())
        pub = self.priv.public_key().public_bytes(serialization.Encoding.X962, serialization.PublicFormat.UncompressedPoint)
        self.state = State.RECV_ECDH
        self.sent_nacks = 0
        return self._send("B", bytes([self.which_key & 0xFF]) + pub)

    def login(self, user_code: str) -> str:
        return self._send("D", ("," + user_code + "\n").encode("utf-8"))

    def data(self, payload: bytes) -> str:
        if self.state is not State.DATA_TRANSFER:
            raise ProbeError("internal: data frame outside DATA_TRANSFER")
        if not payload or payload[0] not in ALLOWED_OPCODES:
            raise ProbeError(f"refusing to send non-read-only opcode {payload[:1].hex()}")
        return self._send("D", payload)

    def heartbeat(self) -> str:
        return self.data(bytes([HEARTBEAT_MSG]))

    def request_log(self, count: int) -> str:
        return self.data(bytes([SPI_REQUEST_LOG_EVENTS, max(1, min(255, count))]))

    def bye(self) -> str:
        """sendByeMessage: E (encrypted) in data mode, else F."""
        t = "E" if self.state is State.DATA_TRANSFER else "F"
        return self._send(t, b"")

    def _nack(self, rejected: bytes, why: str) -> list[str]:
        """Intended sendNACK: C frame whose payload is the rejected 5-byte header."""
        log.warning("rejecting panel frame (%s): %s", why, rejected[:5].hex() if rejected else "<empty>")
        if self.sent_nacks + 1 > MAX_CONSECUTIVE_NACK:
            raise ProbeError("too many consecutive NACKs sent (MANY_NACKS): frames from the panel "
                             "keep failing seq/check. Wrong panel app password?")
        self.sent_nacks += 1
        self.panel_seq = 0 if self.panel_seq == 0 else (self.panel_seq - 1) & 0xFFFFFFFF
        if not self.nack_enabled:
            return []
        hdr = (rejected + b"\x00" * 5)[:5]
        return [self._send("C", hdr)]

    # ---- receiving ---------------------------------------------------------

    def feed(self, data: bytes) -> tuple[list[str], list[Event]]:
        self.receiver.feed(data)
        out: list[str] = []
        events: list[Event] = []
        while True:
            got = self.receiver.next_frame()
            if got is None:
                break
            frame, form = got
            o, e = self._handle(frame, form)
            out += o
            events += e
            if self.state is State.CLOSED:
                break
        return out, events

    def _check(self, frame: bytes) -> tuple[bytes | None, int | None, str]:
        if len(frame) < MIN_FRAME:
            return None, None, f"too short ({len(frame)} bytes)"
        seq16 = frame[1] | (frame[2] << 8)
        full = accept_panel_seq(self.panel_seq, seq16)
        if full is None:
            return None, None, f"seq {seq16} not acceptable after {self.panel_seq}"
        payload = decrypt_frame(self.key, frame, full)
        if payload is None:
            return None, full, "check (SHA-256[0:16]) mismatch after decrypt"
        self.panel_seq = full
        return payload, full, ""

    def _handle(self, frame: bytes, form: str) -> tuple[list[str], list[Event]]:
        t = chr(frame[0]) if frame else "?"
        log.debug("RX frame form=%s type=%s bytes=%s", form, t, frame.hex())
        if len(frame) >= 5:
            hlen = frame[3] | (frame[4] << 8)
            if hlen != len(frame) - MIN_FRAME:
                log.warning("frame header len=%d but frame carries %d payload bytes (using actual)",
                            hlen, len(frame) - MIN_FRAME)
        payload, full, why = self._check(frame)

        if self.state is State.GET_ANNOUNCE:
            if payload is None:
                # Java: checkDecryptMessage false here -> return NO_DATA (no NACK)
                log.warning("ignoring frame during announce: %s", why)
                return [], []
            if t != "a":
                self.rcv_nacks += 1
                if self.rcv_nacks > MAX_CONSECUTIVE_NACK:
                    raise ProbeError("handshake failed: no valid 'a' announce (MANY_NACKS)")
                return self._nack(frame, f"expected 'a', got {t!r}"), []
            got_id = payload[2:10]
            if got_id != self.panel_id_bytes:
                log.error("announce panel ID %r != configured %r", got_id, self.panel_id_bytes)
                if self.rcv_nacks > MAX_CONSECUTIVE_NACK:
                    raise ProbeError("handshake failed: panel ID mismatch")
                return self._nack(frame, "panel ID mismatch"), []
            flags = payload[1]
            self.modem, self.announce_extra = 0, b""
            if flags & 0x80:
                self.modem = {0x81: 1, 0x82: 2, 0x83: 3, 0x84: 4, 0x85: 5, 0x86: 6}.get(flags, 0)
                extra_n = len(frame) - 42
                if extra_n > 0:
                    self.announce_extra = payload[10 : 10 + min(extra_n, 4)]
            elif flags & 0x60:
                self.modem = 3
            elif flags & 0x02:
                self.modem = 5
            elif flags & 0x01:
                self.modem = 4
            elif flags & 0x04:
                self.modem = 6
            ev = Event("announce", {
                "seq": full, "p0": payload[0], "flags": flags, "modem": MODEM_NAMES[self.modem],
                "panel_id": got_id.decode("latin-1"), "extra": self.announce_extra.hex(),
                "json_version": self.announce_extra[0] if self.announce_extra else 0,
                "payload_len": len(payload), "payload": payload.hex(),
            })
            return [self.ecdh()], [ev]

        if self.state is State.RECV_ECDH:
            if payload is not None and t == "b":
                key_bytes = payload[1:]
                panel_pub = load_panel_public_key(key_bytes)
                assert self.priv is not None
                shared = self.priv.exchange(ec.ECDH(), panel_pub)
                self.key = derive_session_key(self.fixed, shared, self.announce_extra)
                self.state = State.DATA_TRANSFER
                self.rcv_nacks = self.sent_nacks = 0
                self.priv = None
                ev = Event("key", {
                    "seq": full, "b0": payload[0], "key_format": "compressed" if len(key_bytes) == 33 else "uncompressed",
                    "panel_pub": key_bytes.hex(),
                })
                return [], [ev]
            if payload is not None and t == "c":
                self.rcv_nacks += 1
                if self.rcv_nacks > MAX_CONSECUTIVE_NACK:
                    raise ProbeError("panel NACKed our ECDH key 4 times (MANY_NACKS)")
                log.warning("panel NACKed our B frame; re-sending")
                return [self.ecdh()], []
            if payload is not None and t == "f":
                self.state = State.CLOSED
                raise ProbeError("panel sent plain goodbye 'f' during key exchange (RECEIVE_CLOSE)")
            return self._nack(frame, why or f"unexpected {t!r} during ECDH"), []

        if self.state is State.DATA_TRANSFER:
            if payload is not None and t == "d":
                self.rcv_nacks = self.sent_nacks = 0
                return [], [Event("data", {"seq": full, "payload": payload})]
            if payload is not None and t == "g":
                self.rcv_nacks = self.sent_nacks = 0
                return [], [Event("photo", {"seq": full, "payload": payload})]
            if payload is not None and t == "c":
                self.rcv_nacks += 1
                if self.rcv_nacks > MAX_CONSECUTIVE_NACK:
                    raise ProbeError("panel NACKed our frames too often (MANY_NACKS). Wrong app password "
                                     "or user code?")
                log.warning("panel NACK for header %s; re-sending last frame", payload[:5].hex())
                if self.last_tx is not None and len(self.last_tx) >= MIN_FRAME:
                    return [wrap_frame(self.last_tx, self.b64_wrap_lines)], [Event("nack", {"header": payload.hex()})]
                return [], [Event("nack", {"header": payload.hex()})]
            if payload is not None and t in "ef":
                self.state = State.CLOSED
                return [], [Event("bye", {"type": t})]
            return self._nack(frame, why or f"unexpected {t!r} in data mode"), []

        log.warning("frame received in state %s; ignored", self.state.value)
        return [], []


# --------------------------------------------------------------------------
# Logging / redaction
# --------------------------------------------------------------------------


def _encoded_forms(b: bytes) -> set[str]:
    """Hex and base64 renderings of a secret as it may appear inside a logged frame.

    Needed because a secret can travel in a frame in the clear (e.g. a panel app password that
    equals the System ID shows up in the announce frame), and we log frames as hex and base64.
    For base64, take the characters that depend only on the secret's bits, for each of the
    three possible byte alignments. Skipped for short secrets to avoid redacting noise.
    """
    forms = {b.hex(), b.hex().upper()}
    if len(b) >= 6:
        for p in range(3):
            full = base64.b64encode(b"\x00" * p + b + b"\x00\x00").decode()
            start, end = -(-8 * p // 6), 8 * (p + len(b)) // 6
            forms.add(full[start:end])
    return forms


class RedactingFilter(logging.Filter):
    def __init__(self, secrets: list[str]):
        super().__init__()
        forms: set[str] = set()
        for s in secrets:
            if not s or len(s) < 3:
                continue
            forms.add(s)
            forms.update(_encoded_forms(s.encode("utf-8")))
        self.secrets = sorted(forms, key=len, reverse=True)

    def redact(self, text: str) -> str:
        text = re.sub(r"<z>.*?</z>", "<z>[REDACTED]</z>", text, flags=re.S)
        for s in self.secrets:
            text = text.replace(s, "[REDACTED]")
        return text

    def filter(self, record: logging.LogRecord) -> bool:
        record.msg = self.redact(record.getMessage())
        record.args = ()
        return True


_REDACTOR: RedactingFilter | None = None


def redact(text: str) -> str:
    return _REDACTOR.redact(text) if _REDACTOR else re.sub(r"<z>.*?</z>", "<z>[REDACTED]</z>", text)


def describe_cloud_wire(plain: str, wire: str, base64_used: bool) -> str:
    """Loggable form of an outbound cloud message (secrets never appear, even base64'd)."""
    if "<z>" in plain:
        if base64_used:
            return f"plain={redact(plain)} wire=({len(wire) - 2} base64 chars withheld: contains <z>)"
        return f"wire={redact(wire)}"
    return f"plain={plain} wire={wire!r}"


# --------------------------------------------------------------------------
# Config loading
# --------------------------------------------------------------------------


def load_device_id(env_value: str, persist: bool) -> str:
    if env_value:
        return env_value
    try:
        v = DEVICE_ID_FILE.read_text().strip()
        if v:
            return v
    except OSError:
        pass
    v = uuid.uuid4().hex[:16]  # Android-ID-like 16 hex chars
    if persist:
        DEVICE_ID_FILE.write_text(v + "\n")
        log.info("generated device ID %s and saved it to %s", v, DEVICE_ID_FILE)
    else:
        log.info("generated ephemeral device ID %s (not saved in dry-run)", v)
    return v


def load_config(env_file: Path | None, persist_device_id: bool, args: argparse.Namespace) -> Config:
    try:
        from dotenv import load_dotenv
    except ImportError as e:  # pragma: no cover
        raise ProbeError("python-dotenv is not installed (pip install -r probe/requirements.txt)", 1) from e
    path = env_file or (REPO_ROOT / ".env")
    if path.exists():
        load_dotenv(path, override=False)  # real process env wins
    g = lambda k, d="": os.environ.get(k, d).strip()  # noqa: E731
    cfg = Config(
        app_user=g("PYRONIX_APP_USER"),
        panel_id=g("PYRONIX_PANEL_ID"),
        panel_password=os.environ.get("PYRONIX_PANEL_PASSWORD", ""),
        user_code=g("PYRONIX_USER_CODE"),
        system_name=g("PYRONIX_SYSTEM_NAME", "Home") or "Home",
        device_id="",
        push_id=g("PYRONIX_PUSH_ID"),
        cloud_url=g("PYRONIX_CLOUD_URL", LIVE_URL) or LIVE_URL,
        os_version=args.os_version,
        network_type=args.network_type,
        app_lang=args.app_lang,
        save_app_pwd=1 if args.save_app_pwd else 0,
    )
    missing = [k for k, v in (("PYRONIX_APP_USER", cfg.app_user), ("PYRONIX_PANEL_ID", cfg.panel_id),
                              ("PYRONIX_PANEL_PASSWORD", cfg.panel_password), ("PYRONIX_USER_CODE", cfg.user_code),
                              ("PYRONIX_PUSH_ID", cfg.push_id)) if not v]
    if missing:
        raise ProbeError(f"missing settings: {', '.join(missing)} (fill in {path} or the environment)", 1)
    cfg.device_id = load_device_id(g("PYRONIX_DEVICE_ID"), persist_device_id)
    if len(cfg.panel_id.encode()) != 8:
        log.warning("PYRONIX_PANEL_ID is %d bytes; the announce check compares exactly 8 bytes "
                    "(PanelEncrypt.java:756), so the handshake will fail unless the panel ID is 8 chars",
                    len(cfg.panel_id.encode()))
    return cfg


# --------------------------------------------------------------------------
# Output
# --------------------------------------------------------------------------


def print_panel_text(text: str, counters: dict) -> None:
    if text == " ":
        counters["hb"] = counters.get("hb", 0) + 1
        log.info("panel heartbeat #%d", counters["hb"])
        return
    if text.startswith("{"):
        try:
            doc = json.loads(text)
        except json.JSONDecodeError as e:
            print(f"\n--- unparseable JSON ({e}) ---\n{text}", flush=True)
            return
        typ = doc.get("type", "?") if isinstance(doc, dict) else "?"
        counters[typ] = counters.get(typ, 0) + 1
        print(f"\n=== {typ} (#{counters[typ]}) ===", flush=True)
        print(json.dumps(doc, indent=2, ensure_ascii=False), flush=True)
        return
    print(f"\n--- unknown format ---\n{text!r}", flush=True)


# --------------------------------------------------------------------------
# Live run
# --------------------------------------------------------------------------


class Probe:
    def __init__(self, cfg: Config, args: argparse.Namespace):
        self.cfg = cfg
        self.args = args
        self.ws = None
        self.session: PanelSession | None = None
        self.phase = "STG_REQ_SUB"
        self.counters: dict = {}
        self.log_requested = False

    async def send_raw(self, wire: str, what: str) -> None:
        log.debug("OUT %s: %r", what, wire)
        await self.ws.send(wire)

    async def send_cloud(self, mt: CloudMsg) -> None:
        plain = make_cloud_message(self.cfg, mt)
        if plain == "null":
            raise ProbeError("makeCloudMessage returned \"null\": AppUser, PushId and DeviceId must be non-empty", 1)
        b64 = CLOUD_MSG_BASE64[mt]
        wire = wrap_cloud(plain, b64, not self.args.no_b64_wrap)
        log.info("-> cloud %s", mt.value)
        log.debug("OUT cloud %s %s", mt.value, describe_cloud_wire(plain, wire, b64))
        await self.ws.send(wire)

    async def send_panel(self, wires: list[str], what: str = "panel frame") -> None:
        for w in wires:
            await self.send_raw(w, what)

    async def recv(self, timeout: float):
        try:
            msg = await asyncio.wait_for(self.ws.recv(), timeout=timeout)
        except asyncio.TimeoutError:
            raise ProbeError(f"timeout: no message from the cloud for {timeout:.0f}s in phase {self.phase}", 4) from None
        if isinstance(msg, bytes):
            log.debug("IN binary (%d bytes): %s", len(msg), msg.hex())
        else:
            log.debug("IN text (%d chars): %r", len(msg), msg)
        return msg

    async def run(self) -> int:
        from websockets.asyncio.client import connect
        from websockets.exceptions import ConnectionClosed

        # Treat SIGTERM (e.g. a supervisor stopping us) like Ctrl-C: cancel, so the goodbye runs.
        task = asyncio.current_task()
        if task is not None:
            try:
                import signal
                asyncio.get_running_loop().add_signal_handler(signal.SIGTERM, task.cancel)
            except (NotImplementedError, RuntimeError):
                pass  # e.g. Windows

        url = self.cfg.cloud_url
        log.info("connecting to %s", url)
        deadline = time.monotonic() + self.args.duration
        try:
            async with connect(url, open_timeout=self.args.step_timeout, ping_interval=None, max_size=None,
                               user_agent_header=self.args.user_agent, close_timeout=5) as ws:
                self.ws = ws
                log.info("websocket open")
                try:
                    return await self._loop(deadline)
                finally:
                    # Every exit path (normal, error, Ctrl-C/SIGINT, task cancellation) must send
                    # the panel goodbye 'E' and close the socket, so the panel's single remote
                    # session slot is released immediately (PROTOCOL.md §13 Q11).
                    await asyncio.shield(self._goodbye())
        except ConnectionClosed as e:
            raise ProbeError(f"websocket closed by server in phase {self.phase}: code={e.rcvd.code if e.rcvd else None} "
                             f"reason={e.rcvd.reason if e.rcvd else ''!r}", 5) from None
        except (OSError, asyncio.TimeoutError) as e:
            raise ProbeError(f"could not connect to {url}: {e!r}", 5) from None

    async def _goodbye(self) -> None:
        if self.ws is None:
            return
        try:
            if self.session is not None and self.session.state is not State.CLOSED:
                await self.send_panel([self.session.bye()], "bye")
            await self.ws.close(code=1001, reason="User disconnect")
            log.info("closed websocket (1001 User disconnect)")
        except Exception as e:  # noqa: BLE001
            log.debug("error while closing: %r", e)

    async def _loop(self, deadline: float) -> int:
        await self.send_cloud(CloudMsg.SUBSCRIPTION)
        while True:
            remaining = deadline - time.monotonic()
            if remaining <= 0:
                log.info("duration reached (%ss); closing", self.args.duration)
                return 0
            data_mode = self.session is not None and self.session.state is State.DATA_TRANSFER
            timeout = min(remaining, 300.0 if data_mode else self.args.step_timeout)
            try:
                msg = await self.recv(timeout)
            except ProbeError as e:
                if time.monotonic() >= deadline:
                    log.info("duration reached (%ss); closing", self.args.duration)
                    return 0
                raise e
            if await self._dispatch(msg):
                return 0

    async def _dispatch(self, msg) -> bool:
        """Handle one WebSocket message. Returns True to stop."""
        if isinstance(msg, bytes):
            raw = msg
            if self.session is None:
                text = msg.decode("utf-8", "replace").strip()
            else:
                log.warning("binary WS frame in panel phase: feeding raw bytes (the app would have used "
                            "okio ByteString.toString(), i.e. '[hex=...]', and could not parse it)")
                text = None
        else:
            text = msg if msg == " " else msg.strip()
            raw = text.encode("utf-8")
            if not text:
                return False

        # Heartbeat: any 1-char message (HomeControl.java:504)
        if text is not None and len(text) == 1:
            if self.session is not None and self.session.state is State.DATA_TRANSFER:
                log.info("cloud heartbeat -> sending encrypted 0x20")
                await self.send_panel([self.session.heartbeat()], "heartbeat")
            else:
                log.info("cloud heartbeat %r before data mode (app would ignore it)", text)
            return False

        reply = parse_cloud_reply(text) if text is not None else None
        if reply is not None:
            return await self._cloud_reply(reply, text)

        if self.session is None:
            log.warning("unexpected non-<e> message in phase %s: %r", self.phase, redact(text or ""))
            return False

        out, events = self.session.feed(raw)
        await self.send_panel(out)
        for ev in events:
            if await self._panel_event(ev):
                return True
        return False

    async def _cloud_reply(self, r: CloudReply, text: str) -> bool:
        if not r.positional:
            log.warning("'<e>' not at start of message; the app's positional parser would mis-read: %r", redact(text))
        log.info("<- cloud reply %r sep=%r data=%r", r.letter, r.sep, redact(r.data))
        if self.session is not None:
            # The app feeds *everything* to the panel decoder in STG_KEYS_PANEL /
            # STG_DATA_PANEL; we surface cloud replies instead.
            if r.letter in CLOUD_ERRORS:
                raise ProbeError(f"cloud error during panel session: {CLOUD_ERRORS[r.letter]}", 2)
            log.warning("cloud reply %r during panel session (the app would feed it to the panel decoder)", r.letter)
            return False
        L = r.letter
        if L in ("w", "m"):
            log.info("subscription info received (%r)", L)
            self.phase = "STG_REQ_PWD"
            await self.send_cloud(CloudMsg.REQ_PWD)
        elif L == "j":
            log.info("password/cloud data received")
            self.phase = "STG_REQ_CLOUD"
            await self.send_cloud(CloudMsg.CONN_TO_CLOUD)
        elif L == "u":
            log.info("panel connected through relay; starting handshake")
            await self.send_cloud(CloudMsg.CONN_TO_PANEL)
            self.phase = "STG_KEYS_PANEL"
            self.session = PanelSession(self.cfg.panel_id, self.cfg.panel_password, 1,
                                        b64_wrap_lines=not self.args.no_b64_wrap, nack=not self.args.no_nack)
            await self.send_panel([self.session.announce()], "A announce")
        elif L == "o":
            # The app re-sends MSG_REQ_PWD / MSG_CONN_TO_CLOUD here if it is in
            # STG_REQ_PWD / STG_REQ_CLOUD (post-switch, HomeControl.java:735-757).
            log.info("cloud OK 'o' (not re-sending, unlike the app)")
        elif L == "x":
            log.info("cloud 'x' (panel JSON accepted)")
        elif L in CLOUD_ERRORS:
            raise ProbeError(f"cloud error {L!r}: {CLOUD_ERRORS[L]}", 2)
        else:
            raise ProbeError(f"unknown cloud reply letter {L!r} (ERROR_UNKNOWN_DATA): {redact(text)!r}", 2)
        return False

    async def _panel_event(self, ev: Event) -> bool:
        d = ev.detail
        if ev.kind == "announce":
            log.info("panel announce: id=%s modem=%s flags=0x%02x p0=0x%02x extra=%s json_version=%s payload_len=%d",
                     d["panel_id"], d["modem"], d["flags"], d["p0"], d["extra"] or "-", d["json_version"], d["payload_len"])
            log.debug("announce payload: %s", d["payload"])
        elif ev.kind == "key":
            log.info("panel ECDH key received (b[0]=0x%02x, %s); session key derived. Sending login.",
                     d["b0"], d["key_format"])
            assert self.session is not None
            self.phase = "STG_DATA_PANEL"
            await self.send_panel([self.session.login(self.cfg.user_code)], "login D")
        elif ev.kind == "data":
            payload: bytes = d["payload"]
            log.debug("decrypted d seq=%s: %r", d["seq"], payload)
            print_panel_text(payload.decode("utf-8", "replace"), self.counters)
            if self.args.request_log and not self.log_requested and payload.startswith(b"{"):
                self.log_requested = True
                log.info("requesting %d log entries (0x35)", self.args.request_log)
                await self.send_panel([self.session.request_log(self.args.request_log)], "log request")
        elif ev.kind == "photo":
            log.info("g (photo) frame payload: %s", d["payload"].hex())
        elif ev.kind == "nack":
            log.warning("panel NACK received: %s", d["header"])
        elif ev.kind == "bye":
            if self.phase == "STG_DATA_PANEL" and not self.counters:
                raise ProbeError(f"panel ended the session with {d['type']!r} right after login: the app reports "
                                 "this as 'Please check your usecode'. Check PYRONIX_USER_CODE.", 3)
            log.info("panel said goodbye (%r)", d["type"])
            return True
        return False


# --------------------------------------------------------------------------
# Dry run
# --------------------------------------------------------------------------


def dry_run(cfg: Config, args: argparse.Namespace) -> int:
    wrap_lines = not args.no_b64_wrap
    print(f"# Dry run: nothing is sent. Endpoint would be {cfg.cloud_url}")
    print("# Secrets are redacted; messages containing <z> are shown re-encoded from the REDACTED text.\n")
    for mt in (CloudMsg.SUBSCRIPTION, CloudMsg.REQ_PWD, CloudMsg.CONN_TO_CLOUD, CloudMsg.CONN_TO_PANEL):
        plain = make_cloud_message(cfg, mt)
        b64 = CLOUD_MSG_BASE64[mt]
        shown_plain = redact(plain)
        wire = wrap_cloud(shown_plain, b64, wrap_lines)
        print(f"## {mt.value} ({'base64' if b64 else 'raw'})")
        print(f"plain: {shown_plain}")
        print(f"wire : {wire!r}\n")
    sess = PanelSession(cfg.panel_id, cfg.panel_password, 1, b64_wrap_lines=wrap_lines)
    a = sess.announce()
    print("## A announce frame (seq 1)")
    print(f"frame: {sess.last_tx.hex()}")
    print(f"wire : {a!r}\n")
    b = sess.ecdh()
    print("## B ECDH frame (seq 2, throwaway key)")
    print(f"frame: {sess.last_tx.hex()}")
    print(f"wire : {b!r}\n")
    print(f"fixedKey derivation path: {'SHA-256(pwd||0||id)' if len(cfg.panel_password) <= 31 else 'hex (radix-10 digits!)'}")
    return 0


# --------------------------------------------------------------------------
# CLI
# --------------------------------------------------------------------------


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    p = argparse.ArgumentParser(description="Read-only Pyronix Cloud protocol probe")
    p.add_argument("-v", "--verbose", action="store_true", help="debug logging of every wire message (secrets redacted)")
    p.add_argument("--dry-run", action="store_true", help="build and print outbound messages, do not connect")
    p.add_argument("--duration", type=float, default=60.0, help="seconds before sending E and closing (default 60)")
    p.add_argument("--step-timeout", type=float, default=30.0, help="timeout per handshake step (default 30)")
    p.add_argument("--request-log", type=int, default=0, metavar="N",
                   help="after the first JSON, send the read-only log request 0x35 for N entries (default off)")
    p.add_argument("--env-file", type=Path, default=None, help="path to .env (default: repo root .env)")
    p.add_argument("--save-app-pwd", action="store_true",
                   help="send SaveAppPwd=1 style hashed <z> instead of the plaintext password (the app never does)")
    p.add_argument("--no-b64-wrap", action="store_true",
                   help="send base64 without Android's 76-column newlines (the app always wraps)")
    p.add_argument("--no-nack", action="store_true", help="never send C (NACK) frames, just log")
    p.add_argument("--os-version", default="14")
    p.add_argument("--network-type", default="Wifi")
    p.add_argument("--app-lang", default="uk")
    p.add_argument("--user-agent", default="okhttp/4.12.0", help="WebSocket User-Agent header")
    return p.parse_args(argv)


def main(argv: list[str] | None = None) -> int:
    global _REDACTOR
    args = parse_args(argv)
    logging.basicConfig(level=logging.DEBUG if args.verbose else logging.INFO,
                        format="%(asctime)s %(levelname)-7s %(message)s", stream=sys.stderr)
    # websockets' own frame logging would show the base64 of MSG_CONN_TO_CLOUD
    # (which carries the password), so it stays quiet; our own logs cover the wire.
    logging.getLogger("websockets").setLevel(logging.WARNING)
    logging.getLogger("asyncio").setLevel(logging.WARNING)
    try:
        cfg = load_config(args.env_file, persist_device_id=not args.dry_run, args=args)
        _REDACTOR = RedactingFilter([cfg.panel_password, cfg.user_code])
        for h in logging.getLogger().handlers:
            h.addFilter(_REDACTOR)
        if args.dry_run:
            return dry_run(cfg, args)
        return asyncio.run(Probe(cfg, args).run())
    except ProbeError as e:
        log.error("%s", e)
        return e.exit_code
    except KeyboardInterrupt:
        log.info("interrupted")
        return 130
    except asyncio.CancelledError:
        log.info("terminated")
        return 143


if __name__ == "__main__":
    sys.exit(main())
