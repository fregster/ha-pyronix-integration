"""Offline tests for pyronix_probe (no network). Run: pytest probe/"""

from __future__ import annotations

import base64
import hashlib
import json
import struct

import pytest
from cryptography.hazmat.primitives import padding, serialization
from cryptography.hazmat.primitives.asymmetric import ec
from cryptography.hazmat.primitives.ciphers import Cipher, algorithms, modes

import pyronix_probe as pp

PANEL_ID = "12345678"
PWD = "secretpw"
KEY = bytes(range(16))


def cfg(**kw) -> pp.Config:
    base = dict(app_user="user@example.com", panel_id=PANEL_ID, panel_password=PWD, user_code="1234",
                system_name="Home", device_id="dev123", push_id="push", os_version="14",
                network_type="Wifi", app_lang="uk")
    base.update(kw)
    return pp.Config(**base)


# --------------------------------------------------------------------------
# Line-for-line ports of the Java, used as references
# --------------------------------------------------------------------------


def java_encrypt_ecb(key: bytes, data: bytes) -> bytes:
    """AES128.encryptECB: Cipher "AES/ECB/PKCS5Padding" doFinal (output grows by a block)."""
    p = padding.PKCS7(128).padder()
    padded = p.update(data) + p.finalize()
    e = Cipher(algorithms.AES(key), modes.ECB()).encryptor()
    return e.update(padded) + e.finalize()


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
    bArr = bytearray(tx)
    if len(bArr) < 21:
        raise AssertionError("short")
    length2 = len(bArr)
    b3 = bArr[0]
    if b3 not in (100, 101, 103, 68, 69, 71):
        return bytes(bArr)
    i4 = length2 - 5
    bArrEncryptECB = bytearray(java_encrypt_ecb(key, java_int_array_to_byte_array([b3, length2 - 21, appl_seq, 0])))
    i5 = 0
    i6 = 0
    while i4 > 0:
        if i5 == 0:
            bArrEncryptECB = bytearray(java_encrypt_ecb(key, bytes(bArrEncryptECB)))
        i7 = i6 + 5
        b4 = bArr[i7] ^ bArrEncryptECB[i5]
        bArr[i7] = b4
        bArrEncryptECB[i5] = b4
        i5 = (i5 + 1) & 15
        i4 -= 1
        i6 += 1
    return bytes(bArr)


def java_decrypt_message(key: bytes, rcv: bytes, seq: int) -> bytes:
    """PanelEncrypt.decryptMessage (PanelEncrypt.java:373-407), line for line."""
    bArr = bytearray(rcv)
    length = len(bArr)
    b3 = bArr[0]
    if b3 not in (100, 101, 103, 68, 69, 71):
        return bytes(bArr)
    i3 = length - 5
    e = bytearray(java_encrypt_ecb(key, java_int_array_to_byte_array([bArr[0], length - 21, seq, 0])))
    i4 = 0
    i5 = 0
    while i3 > 0:
        if i4 == 0:
            e = bytearray(java_encrypt_ecb(key, bytes(e)))
        i6 = i5 + 5
        b4 = bArr[i6]
        bArr[i6] = e[i4] ^ b4
        e[i4] = b4
        i4 = (i4 + 1) & 15
        i3 -= 1
        i5 += 1
    return bytes(bArr)


# --------------------------------------------------------------------------
# fixedKey
# --------------------------------------------------------------------------


def test_fixed_key_short_password():
    want = hashlib.sha256(b"secretpw\x00" + PANEL_ID.encode()).digest()[:16]
    assert pp.fixed_key(PWD, PANEL_ID) == want


def test_fixed_key_31_chars_still_hashed():
    pwd = "x" * 31
    assert pp.fixed_key(pwd, PANEL_ID) == hashlib.sha256(pwd.encode() + b"\x00" + PANEL_ID.encode()).digest()[:16]


def test_fixed_key_long_decimal_password():
    pwd = "0123456789" * 4  # 40 chars, first 32 digits used for 16 key bytes
    assert pp.fixed_key(pwd, PANEL_ID) == bytes.fromhex("01234567890123456789012345678901")


def test_fixed_key_long_hex_letters_count_as_zero():
    # Kotlin digitToIntOrNull() is radix 10: 'a'..'f' map to 0, unlike int(x, 16).
    pwd = "ab" * 16 + "ff"
    assert pp.fixed_key(pwd, PANEL_ID) == bytes(16)
    pwd2 = "1a" * 16
    assert pp.fixed_key(pwd2, PANEL_ID) == bytes([0x10]) * 16


def test_fixed_key_odd_length_and_only_first_32_chars():
    pwd = "9" * 32 + "1"  # 33 chars: char 33 does not affect the 16-byte key
    assert pp.fixed_key(pwd, PANEL_ID) == bytes([0x99]) * 16


def test_generate_password_save_app_pwd():
    assert pp.generate_password(PWD, PANEL_ID) == hashlib.sha256(b"secretpw\x00" + PANEL_ID.encode()).hexdigest()[:32]
    long = "z" * 32
    assert pp.generate_password(long, PANEL_ID) == long


# --------------------------------------------------------------------------
# Frames
# --------------------------------------------------------------------------


def test_build_frame_layout_and_check():
    f = pp.build_frame("A", 1, b"")
    assert len(f) == 21
    assert f[:5] == b"A\x01\x00\x00\x00"
    assert f[5:] == hashlib.sha256(f[:5]).digest()[:16]
    g = pp.build_frame("D", 0x1_0203, b"xyz")
    assert g[:5] == b"D\x03\x02\x03\x00"  # seq low 16 bits LE, len LE
    assert len(g) == 3 + 21


def test_b_frame_layout():
    s = pp.PanelSession(PANEL_ID, PWD)
    s.announce()
    s.ecdh()
    f = s.last_tx
    assert len(f) == 87 and f[0:1] == b"B" and f[3] | f[4] << 8 == 66
    assert f[5] == 1 and f[6] == 4  # WhichKey, uncompressed point
    assert f[1] == 2  # second frame


@pytest.mark.parametrize("n", [0, 1, 7, 15, 16, 17, 30, 200, 1000])
def test_encrypt_decrypt_round_trip(n):
    payload = bytes((i * 7) & 0xFF for i in range(n))
    f = pp.encrypt_frame(KEY, "D", 42, payload)
    assert f[:5] == pp.build_frame("D", 42, payload)[:5]
    assert f[5:] != pp.build_frame("D", 42, payload)[5:]
    assert pp.decrypt_frame(KEY, f, 42) == payload
    assert pp.decrypt_frame(KEY, f, 43) is None  # wrong nonce seq -> check fails
    tampered = bytearray(f)
    tampered[-1] ^= 1
    assert pp.decrypt_frame(KEY, bytes(tampered), 42) is None


def test_unencrypted_types_are_not_encrypted():
    for t in "ABCF":
        assert pp.encrypt_frame(KEY, t, 5, b"hi") == pp.build_frame(t, 5, b"hi")


# --------------------------------------------------------------------------
# CFB equivalence with the app's manual loop
# --------------------------------------------------------------------------


@pytest.mark.parametrize("n", [0, 1, 7, 15, 16, 17, 30, 31, 32, 33, 200])
@pytest.mark.parametrize("seq", [1, 2, 300, 0x12345678])
def test_cfb_matches_java_encrypt_loop(n, seq):
    payload = bytes((i * 31 + 3) & 0xFF for i in range(n))
    plain = pp.build_frame("D", seq, payload)
    java = java_encrypt_message(KEY, plain, seq)
    ours = pp.encrypt_frame(KEY, "D", seq, payload)
    assert ours == java


@pytest.mark.parametrize("n", [0, 5, 16, 100])
def test_cfb_matches_java_decrypt_loop(n):
    payload = bytes(range(n))
    for t in "deg":
        enc = pp.encrypt_frame(KEY, t, 9, payload)
        assert java_decrypt_message(KEY, enc, 9) == pp.build_frame(t, 9, payload)
        assert pp.decrypt_frame(KEY, enc, 9) == payload


def test_cfb_uses_decrepit_mode_when_available():
    try:
        from cryptography.hazmat.decrepit.ciphers.modes import CFB
    except ImportError:
        pytest.skip("old cryptography")
    assert pp._CFB is CFB


def test_nonce_layout():
    assert pp.make_nonce(ord("D"), 3, 0x01020304) == struct.pack("<IIII", 0x44, 3, 0x01020304, 0)


# --------------------------------------------------------------------------
# EC points
# --------------------------------------------------------------------------


def test_compressed_point_decompression_matches_cryptography():
    for _ in range(8):
        priv = ec.generate_private_key(ec.SECP256R1())
        pub = priv.public_key()
        unc = pub.public_bytes(serialization.Encoding.X962, serialization.PublicFormat.UncompressedPoint)
        comp = pub.public_bytes(serialization.Encoding.X962, serialization.PublicFormat.CompressedPoint)
        assert pp.decompress_point(comp[1:], comp[0] == 3) == unc
        loaded = pp.load_panel_public_key(comp)
        assert loaded.public_bytes(serialization.Encoding.X962, serialization.PublicFormat.UncompressedPoint) == unc
        assert pp.load_panel_public_key(unc).public_numbers() == pub.public_numbers()


def test_bad_key_length_rejected():
    with pytest.raises(pp.ProbeError):
        pp.load_panel_public_key(b"\x04" + bytes(10))


# --------------------------------------------------------------------------
# Cloud messages
# --------------------------------------------------------------------------

TAIL = "<i>dev123</i><l>6.03.001</l><t>16</t><m>Android</m><v>14</v><c>Wifi</c><lang>uk</lang></p>"
HEAD = "<p><n>user@example.com</n><u>push</u>"


def test_cloud_message_field_order():
    c = cfg()
    assert pp.make_cloud_message(c, pp.CloudMsg.SUBSCRIPTION) == HEAD + "<w>12345678</w>" + TAIL
    assert pp.make_cloud_message(c, pp.CloudMsg.REQ_PWD) == HEAD + "<j>12345678</j>" + TAIL
    conn = HEAD + "<a>12345678</a><b>Home</b><z>secretpw</z>" + TAIL
    assert pp.make_cloud_message(c, pp.CloudMsg.CONN_TO_CLOUD) == conn
    assert pp.make_cloud_message(c, pp.CloudMsg.CONN_TO_PANEL) == conn


def test_cloud_message_save_app_pwd_and_null():
    c = cfg(save_app_pwd=1)
    z = pp.generate_password(PWD, PANEL_ID)
    assert f"<z>{z}</z>" in pp.make_cloud_message(c, pp.CloudMsg.CONN_TO_CLOUD)
    for k in ("app_user", "push_id", "device_id"):
        assert pp.make_cloud_message(cfg(**{k: ""}), pp.CloudMsg.SUBSCRIPTION) == "null"
    assert "<t>1</t>" in pp.make_cloud_message(cfg(app_type=None), pp.CloudMsg.SUBSCRIPTION)


def test_wrap_cloud_android_base64():
    text = pp.make_cloud_message(cfg(), pp.CloudMsg.SUBSCRIPTION)
    w = pp.wrap_cloud(text, True)
    assert w.startswith("(") and w.endswith("\n)")
    body = w[1:-1]
    lines = body.split("\n")
    assert lines[-1] == ""  # trailing newline
    assert all(len(line) == 76 for line in lines[:-2]) and 0 < len(lines[-2]) <= 76
    assert base64.b64decode(body.replace("\n", "")) == text.encode()
    assert pp.wrap_cloud(text, True, b64_wrap_lines=False) == "(" + base64.b64encode(text.encode()).decode() + ")"
    # raw form: wrapped in parentheses once
    assert pp.wrap_cloud("<p>x</p>", False) == "(<p>x</p>)"
    assert pp.wrap_cloud("(abc)", False) == "(abc)"


def test_android_b64_exact_multiple_of_57():
    s = pp.android_b64encode(bytes(57))
    assert s.count("\n") == 1 and s.endswith("\n") and len(s) == 77
    assert pp.android_b64encode(b"") == ""


def test_parse_cloud_reply():
    r = pp.parse_cloud_reply("<e>w,somedata</e>")
    assert (r.letter, r.sep, r.data, r.positional) == ("w", ",", "somedata", True)
    r = pp.parse_cloud_reply("<e>o</e>")
    assert r.letter == "o" and r.data == ""
    assert pp.parse_cloud_reply("hello") is None
    r = pp.parse_cloud_reply("xx<e>n:</e>")
    assert r.letter == "n" and not r.positional
    # Java substring(5, len-4): exactly one char after the letter is skipped
    assert pp.parse_cloud_reply("<e>jABCDEF</e>").data == "BCDEF"


# --------------------------------------------------------------------------
# Receive path
# --------------------------------------------------------------------------


def test_receiver_base64_form_with_newlines_and_partial():
    f1 = pp.build_frame("a", 1, b"\x00\x83" + PANEL_ID.encode())
    f2 = pp.build_frame("d", 2, b"{}")
    wire = (pp.wrap_frame(f1) + pp.wrap_frame(f2)).encode()
    r = pp.FrameReceiver()
    r.feed(wire[:10])
    assert r.next_frame() is None
    r2 = pp.FrameReceiver()
    r2.feed(wire[:-3])
    assert r2.next_frame() == (f1, "b64")
    assert r2.next_frame() is None
    r2.feed(wire[-3:])
    assert r2.next_frame() == (f2, "b64")
    assert r2.next_frame() is None


def test_receiver_raw_binary_with_garbage_and_partial():
    f1 = pp.build_frame("a", 1, b"\x00\x01" + PANEL_ID.encode())
    f2 = pp.build_frame("d", 2, bytes(200))  # len low byte >= 0x80 (Java sign bug)
    r = pp.FrameReceiver()
    r.feed(b"\x00\xff\x10[]" + f1[:7])
    assert r.next_frame() is None
    assert bytes(r.buf[:1]) == b"a"  # garbage skipped
    r.feed(f1[7:] + f2[:50])
    assert r.next_frame() == (f1, "raw")
    assert r.next_frame() is None
    r.feed(f2[50:])
    assert r.next_frame() == (f2, "raw")


def test_receiver_needs_21_bytes():
    r = pp.FrameReceiver()
    r.feed(b"(QUJD)")
    assert r.next_frame() is None  # < 21 bytes buffered, Java waits too


def test_receiver_bad_base64_yields_empty_frame():
    r = pp.FrameReceiver()
    r.feed(b"(" + b"A" * 25 + b")")  # 25 chars: bad padding
    assert r.next_frame() == (b"", "b64")


# --------------------------------------------------------------------------
# Sequence handling
# --------------------------------------------------------------------------


def test_seq_first_frame():
    assert pp.accept_panel_seq(0, 1) == 1
    assert pp.accept_panel_seq(0, 127) == 127
    assert pp.accept_panel_seq(0, 128) is None
    assert pp.accept_panel_seq(0, 0) is None  # Java rejects seq 0


def test_seq_normal_window():
    assert pp.accept_panel_seq(5, 6) == 6
    assert pp.accept_panel_seq(5, 5) is None
    assert pp.accept_panel_seq(5, 4) is None
    assert pp.accept_panel_seq(5, 132) == 132
    assert pp.accept_panel_seq(5, 133) is None


def test_seq_wrap():
    assert pp.accept_panel_seq(65500, 3) == 0x10003
    assert pp.accept_panel_seq(65408, 3) is None  # must be > 65408
    assert pp.accept_panel_seq(65409, 3) == 0x10003
    assert pp.accept_panel_seq(65500, 128) is None
    # Java quirk: after the wrap, the next frame's "full" seq loses the high bits
    assert pp.accept_panel_seq(0x10003, 4) == 4
    # second wrap
    assert pp.accept_panel_seq(0x1FFF0, 2) == 0x20002


def test_app_seq_increments_before_build_and_wraps_16_bits():
    s = pp.PanelSession(PANEL_ID, PWD)
    s.announce()
    assert s.last_tx[1:3] == b"\x01\x00"
    s.app_seq = 0xFFFF
    s.state = pp.State.DATA_TRANSFER
    s.key = KEY
    s.heartbeat()
    assert s.app_seq == 0x10000 and s.last_tx[1:3] == b"\x00\x00"
    assert pp.decrypt_frame(KEY, s.last_tx, 0x10000) == b"\x20"  # nonce uses the full 32-bit seq


# --------------------------------------------------------------------------
# Read-only guard
# --------------------------------------------------------------------------


@pytest.mark.parametrize("op", [0x2C, 0x2D, 0x2F, 0x31, 0x34])
def test_write_opcodes_refused(op):
    s = pp.PanelSession(PANEL_ID, PWD)
    s.state = pp.State.DATA_TRANSFER
    s.key = KEY
    with pytest.raises(pp.ProbeError):
        s.data(bytes([op, 1, 1]))


# --------------------------------------------------------------------------
# Full handshake against an emulated panel
# --------------------------------------------------------------------------


class FakePanel:
    def __init__(self, panel_id: str, pwd: str, flags: int = 0x83, extra: bytes = b"", compressed: bool = True):
        self.panel_id, self.pwd, self.flags, self.extra, self.compressed = panel_id, pwd, flags, extra, compressed
        self.seq = 0
        self.app_seq = 0
        self.key = None
        self.rx = pp.FrameReceiver()

    def _seq(self):
        self.seq += 1
        return self.seq

    def handle(self, wire: str) -> list[bytes]:
        self.rx.feed(wire.encode())
        out = []
        while (got := self.rx.next_frame()) is not None:
            f, _ = got
            seq = f[1] | f[2] << 8
            t = chr(f[0])
            if t == "A":
                # payload: p0, flags, id(8), then filler so that extra lands at p[10:] and len-42 > 0
                p = b"\x07" + bytes([self.flags]) + self.panel_id.encode()
                if self.extra:
                    p += self.extra + bytes(21 - len(self.extra))  # plen = 10 + 21 -> extra_n = 10 (capped to 4)
                out.append(pp.build_frame("a", self._seq(), p))
            elif t == "B":
                app_pub = ec.EllipticCurvePublicKey.from_encoded_point(ec.SECP256R1(), f[6:6 + 65])
                priv = ec.generate_private_key(ec.SECP256R1())
                fmt = serialization.PublicFormat.CompressedPoint if self.compressed else serialization.PublicFormat.UncompressedPoint
                pub = priv.public_key().public_bytes(serialization.Encoding.X962, fmt)
                shared = priv.exchange(ec.ECDH(), app_pub)
                # app keeps p[10 : 10 + min(len(frame) - 42, 4)] -> 4 bytes here
                kx = (self.extra + bytes(21))[:4] if self.extra else b""
                self.key = pp.derive_session_key(pp.fixed_key(self.pwd, self.panel_id), shared, kx)
                out.append(pp.build_frame("b", self._seq(), b"\x01" + pub))
            elif t == "D":
                plain = pp.decrypt_frame(self.key, f, seq)
                assert plain is not None, "panel could not decrypt app frame"
                self.last_plain = plain
                if plain.startswith(b","):
                    for doc in ({"type": "panel", "Panel": self.panel_id}, {"type": "area", "Areas": 1}):
                        out.append(pp.encrypt_frame(self.key, "d", self._seq(), json.dumps(doc).encode()))
                elif plain == b"\x20":
                    out.append(pp.encrypt_frame(self.key, "d", self._seq(), b" "))
            elif t == "E":
                assert pp.decrypt_frame(self.key, f, seq) == b""
        return out


@pytest.mark.parametrize("compressed", [True, False])
@pytest.mark.parametrize("extra", [b"", b"\x01\x02"])
@pytest.mark.parametrize("pwd", ["secretpw", "1234567890" * 4])
def test_full_handshake_with_fake_panel(compressed, extra, pwd):
    panel = FakePanel(PANEL_ID, pwd, extra=extra, compressed=compressed)
    s = pp.PanelSession(PANEL_ID, pwd)
    to_panel = [s.announce()]
    events = []
    for _ in range(10):
        if not to_panel:
            break
        nxt = []
        for w in to_panel:
            for f in panel.handle(w):
                o, e = s.feed(pp.wrap_frame(f).encode())
                nxt += o
                events += e
                for ev in e:
                    if ev.kind == "key":
                        nxt.append(s.login("1234"))
        to_panel = nxt
    kinds = [e.kind for e in events]
    assert kinds[:2] == ["announce", "key"]
    assert events[0].detail["modem"] == "WAN_LAN"
    if extra:
        assert bytes.fromhex(events[0].detail["extra"]) == (extra + bytes(4))[:4]
    assert panel.last_plain == b",1234\n"
    docs = [json.loads(e.detail["payload"]) for e in events if e.kind == "data"]
    assert [d["type"] for d in docs] == ["panel", "area"]
    # heartbeat round trip
    for f in panel.handle(s.heartbeat()):
        _, e = s.feed(f)  # raw binary form this time
        assert e[0].detail["payload"] == b" "
    panel.handle(s.bye())
    assert s.last_tx[0:1] == b"E"


def test_wrong_password_gives_nacks_then_error():
    panel = FakePanel(PANEL_ID, "otherpw")
    s = pp.PanelSession(PANEL_ID, PWD)
    for f in panel.handle(s.announce()):
        out, _ = s.feed(f)
    for f in panel.handle(out[0]):
        s.feed(f)
    assert s.state is pp.State.DATA_TRANSFER  # app cannot tell yet
    bad = pp.encrypt_frame(panel.key, "d", 3, b"{}")
    with pytest.raises(pp.ProbeError):
        for _ in range(5):
            s.feed(bad)


def test_panel_id_mismatch_nacks():
    panel = FakePanel("87654321", PWD)
    s = pp.PanelSession(PANEL_ID, PWD)
    out, ev = s.feed(panel.handle(s.announce())[0])
    assert ev == [] and len(out) == 1
    nack = pp.android_b64decode(out[0][1:-1])
    assert nack[0:1] == b"C" and nack[5:10] == b"a\x01\x00" + bytes([10, 0])


def test_redaction():
    f = pp.RedactingFilter(["secretpw", "1234"])
    msg = pp.make_cloud_message(cfg(), pp.CloudMsg.CONN_TO_PANEL)
    out = f.redact(msg + " code ,1234\n")
    assert "secretpw" not in out and "1234" not in out and "<z>[REDACTED]</z>" in out


def test_redaction_of_secret_inside_logged_frames():
    # A panel app password equal to the System ID appears in the clear in the announce frame,
    # which is logged as hex and as base64 at any byte offset.
    import base64
    pw = "ABCDEFGH"
    f = pp.RedactingFilter([pw])
    for prefix in (b"", b"a", b"a\x01", b"a\x01\x00\n\x00Z"):
        frame = prefix + pw.encode() + b"\xd0\x74\x34"
        for text in (frame.hex(), frame.hex().upper(), base64.b64encode(frame).decode()):
            out = f.redact(text)
            assert "[REDACTED]" in out, (prefix, text)
            assert pw.encode().hex() not in out.lower()
