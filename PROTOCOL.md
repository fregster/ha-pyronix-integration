# Pyronix HomeControl2 — Panel Communication Protocol

Reverse-engineered from the Android app to enable a Home Assistant integration.

| | |
|---|---|
| App | HomeControl2.0 — `com.pyronixhc2.android.app` |
| Version analysed | 6.3.1 (versionCode 718), minSdk 24, targetSdk 36 |
| Source of APK | Google Play, via `apkeep` (genuine Google-delivered splits) |
| Embedded protocol library | `com.pyronix.kotlinlibhomecontrol`, `API_VERSION_STRING = "3.00.000"` |
| App identity sent to cloud | `AppVersion "6.03.001"`, `AppType 16`, `OSName "Android"` |
| Supported products (per library) | `"HomeControl+ Enforcer,Euro 46 App"` |
| Date of analysis | 2026-11-04 |
| Method | Static analysis (jadx 1.5.6), then read-only live sessions with `probe/` against a Euro 46 V10.26 (2026-11-04). Live findings are labelled **Observed (live)**. |
| Revised | 2026-11-04: corrected after a line-by-line port to Python (`probe/`) against the Java. Places where the app's code has bugs are marked **App bug**. |

All source paths below are relative to `decompiled/sources/` (gitignored; regenerate with
`jadx -d decompiled apk/com.pyronixhc2.android.app.apk`).

**Evidence labels used in this document**

- **Observed** — read directly in decompiled code.
- **Inferred** — a reasoned conclusion from code, not directly stated.
- **Unknown** — not determined; needs a traffic capture or further reading.
- **Reported (third-party)** — stated by an independent implementation (README or code) and
  **not** observed by us. Treat it as a strong hint, not as fact, until our own capture confirms
  it. Sources are listed in [§13.1](#131-third-party-sources).

---

## Contents

1. [Architecture overview](#1-architecture-overview)
2. [Inputs and credentials](#2-inputs-and-credentials)
3. [Layer 1 — Cloud WebSocket relay](#3-layer-1--cloud-websocket-relay)
4. [Layer 2 — Encrypted panel session](#4-layer-2--encrypted-panel-session)
5. [Layer 3 — Panel application commands](#5-layer-3--panel-application-commands)
6. [Layer 3 — Panel responses (JSON)](#6-layer-3--panel-responses-json)
7. [End-to-end connection sequence](#7-end-to-end-connection-sequence)
8. [Status, progress and error codes](#8-status-progress-and-error-codes)
9. [Ancillary REST API and account login](#9-ancillary-rest-api-and-account-login)
10. [Other SDKs in the app (not used for panel control)](#10-other-sdks-in-the-app-not-used-for-panel-control)
11. [Reference implementation notes (Python)](#11-reference-implementation-notes-python)
12. [Security observations](#12-security-observations)
13. [Open questions](#13-open-questions)
    - [13.1 Third-party sources](#131-third-party-sources)
14. [Emulator and acquisition notes](#14-emulator-and-acquisition-notes)
15. [Source index](#15-source-index)

---

## 1. Architecture overview

```
 ┌───────────────┐   WSS (TLS)    ┌──────────────────────────┐  panel's own   ┌───────────────┐
 │ HomeControl2  │ ─────────────▶ │ wss://app.pyronixcloud.  │  uplink (LAN/  │ Pyronix panel │
 │ app / HA      │ ◀───────────── │ com:443  (relay)         │ ◀────────────▶ │ (Enforcer /   │
 └───────────────┘                └──────────────────────────┘  GPRS/GSM…)    │  Euro 46)     │
        │                                                                       └───────────────┘
        └──────────── end-to-end encrypted session (ECDH P-256 + AES-128) ───────────┘
```

- **Observed:** the app never opens a socket to the panel. The only transport in the panel
  library is an OkHttp WebSocket to the Pyronix cloud (`HomeControl.java:108-110, 1160`).
- **Observed:** the protocol has three layers:
  1. **Cloud relay** — a small XML-like control language, used to find the panel and open a relay.
  2. **Encrypted panel session** — binary frames, ECDH key agreement, AES-128 stream encryption.
     The cloud forwards these frames but cannot decrypt them, because the key also depends on the
     panel's app password.
  3. **Application layer** — one-byte opcodes from the app; JSON documents from the panel.
- **Inferred:** a local-only (LAN) integration is not possible using this protocol, because the
  panel is reached through the cloud. A local integration would need the panel's own LAN or serial
  interface, which this app does not use.
- **Observed (live, 2026-11-05):** a full TCP connect scan of a Euro 46 V10.26 with a DIGI-LAN
  module found no listening ports. Every port refused the connection, so the module makes
  outbound connections only. Caveat: the scan crossed a router, which could have refused
  connections on the panel's behalf.
- **Observed (vendor documentation and software):** on the Euro 46 V10, the DIGI-LAN module's
  documented IP connections are the Pyronix Cloud (app and installer software) and alarm
  reporting to a monitoring centre. The DIGI-LAN leaflet lists the firewall ports these need:
  TCP 25000 ("panel"), 25010 ("Insite UDL") and 443 ("App"). **Inferred:** they are the panel's
  outbound connections to the cloud. This is the panel's side of the relay that §3 describes from
  the app's side. Which host and port carries the panel end of an app session is **Unknown**.
  Neither connection offers a local endpoint. The only direct, cloud-free
  link is the panel's RS-232 port, which carries the older installer protocol described in
  [LEGACY_PROTOCOL.md](LEGACY_PROTOCOL.md), not this one.

---

## 2. Inputs and credentials

The connect request is built in `SdkManagerImpl$connectToPanel$2.java:262-275`, plus `SdkManagerImpl.P()`
(`SdkManagerImpl.java:528`). It is handed to `HomeControl.setConnectionDetails()`
(`HomeControl.java:1113`).

| JSON key | Meaning | Source in app | Needed by HA? |
|---|---|---|---|
| `AppUser` | The app's **"Device name"** (Settings > "User Name"), a free-text name for the client and **not** an e-mail. It comes from `UserLocalDataStoreImpl.getUsername()` (prefs key `username2`) and is set by `UserNameActivity` ("Enter your name", at least 3 characters) or in Settings. It defaults to `Build.MODEL` (`SettingsActivity.java:1713`). If it is null, the login aborts (`OcpPanelLoginRepositoryImpl`). | Entered once at setup | Yes (any stable name) |
| `PanelId` | The **"System ID"** entered when adding the panel (`PanelIdScreenKt`), by typing it, QR code or short code. It is upper-cased, and `0`/`1` are rejected. The app accepts 8 characters (OCP panels, this protocol) or 32 (Enforcer X, routed by `PanelKt.a()`, probably the IoT/MQTT path). **Inferred** 8 ASCII chars here: the panel's announce is compared against bytes 2–9 (§4.4). | Entered once at setup | Yes |
| `PanelPwd` | The **"App password"** field on the login screen (`system_login_password`), set on the panel by the installer. Keys the encryption. Stored per panel as `{panelId}_p1` if saved. | Login screen | Yes |
| `UserCode` | The **"User code"** field on the login screen (`system_login_code`): the keypad code, sent after the encrypted session is up. Stored as `{panelId}_c1` if saved. | Login screen | Yes |
| `SystemName` | Friendly name for the panel. | User entry | Probably cosmetic |
| `SaveAppPwd` | Always `0` in this app → password sent **unhashed** to the cloud (§3.3, §12). | Hard-coded | Send `0` or `1` (see §12) |
| `PushId` | Push token (FCM). `makeCloudMessage` returns `"null"` and sends nothing if empty. | `GetPushId` use case | **Unknown** whether the cloud validates it |
| `DeviceId` key (`DeviceDataCollector.DEVICE_AGENT` constant) | Device identifier. Must be non-empty. | `GetDeviceId` use case | Yes (any stable value — **unknown** if validated) |
| `AppVersion` | `"6.03.001"` | `P()` | Copy |
| `AppType` | `16` | `P()` | Copy |
| `OSName` | `"Android"` | `P()` | Copy |
| `OSVersion` | `Build.VERSION.RELEASE`, e.g. `"14"` | `P()` | Copy |
| `NetworkType` | `"Wifi"`, `"Cellular"`, `"Ethernet"` or `""` (`U()`) | `P()` | Copy (`"Wifi"`) |
| `AppLang` | Effectively always `"uk"` (see below) | `P()` | Copy (`"uk"`) |

**Observed (Pyronix Cloud web portal, per-panel device list):** each client is listed with
*Device* (= `AppUser`), *Model* (`OSName`, e.g. `iOS`), *Version* (`OSVersion`), *App Version*
(`AppVersion`, iOS shows `6.03.000`), *Last Connection*, *Push Notification Status*
(`registered`, presumably from `PushId`) and *Access Allowed* (Yes/No). **Inferred:** the cloud
tracks clients by `AppUser` and possibly `DeviceId`. *Access Allowed = No* is the likely source of
the `a` (access denied) reply (§3.4). A third-party client should use its **own** `AppUser`.
Reusing a phone's name may overwrite that phone's push registration (unverified).

**Observed** (`SdkManagerImpl.java:528-619`): `AppLang` is meant to be a language code. However,
`Locale.getCountry()` returns upper case and is compared against a lower-case list, so the
comparison never matches and the fallback `"uk"` is always used.

> The exact string of the `DEVICE_AGENT` JSON key is a third-party constant that jadx left
> unresolved. It only matters for the app's internal JSON hand-off, not on the wire. On the wire
> the device ID travels in the `<i>` tag.

---

## 3. Layer 1 — Cloud WebSocket relay

### 3.1 Endpoint

| | |
|---|---|
| Live | `wss://app.pyronixcloud.com:443` |
| Test | `wss://apptestlb.pyronixcloud.com:443` |
| Client | OkHttp `newWebSocket` with a plain `Request` (no custom headers, no auth token) |
| Source | `HomeControl.java:108-110, 1160-1226` |

- **Observed:** no Authorization header, cookie or Cognito token is attached to the WebSocket
  request.
- **Unknown:** whether the server enforces any further check (for example, a registered push ID).

### 3.2 Framing on the WebSocket

**App → cloud** (`sendMessage2Cloud`, `HomeControl.java:972`)

- If `toBase64 = true`, the message is `"(" + Base64(UTF-8 bytes) + ")"`.
- If `toBase64 = false`, the text is sent inside parentheses as-is: `"(" + text + ")"`, unless it
  is already wrapped.
- Base64 is Android `Base64.encodeToString(x, 0)`, i.e. `DEFAULT`. That is standard alphabet
  with `=` padding, a `\n` every 76 characters, and a trailing `\n`. This is byte-identical to
  Python's `base64.encodebytes`. Received data is decoded with Android `Base64.decode`.
  `PanelEncryptKt.convFrom64`/`convTo64` are never called.

**Cloud → app** (`onMessage`, `HomeControl.java:1194-1224`)

- Text frames: a single `" "` is passed through untouched (heartbeat). Other frames are trimmed,
  then handled by `didReceiveMessage`.
- Binary frames: passed on as `ByteString.toString()`, which gives the text `"[hex=…]"`, not the
  bytes (`HomeControl.java:1223`). **Inferred:** the cloud never sends binary WebSocket frames,
  because the app could not process them.
- **Observed:** cloud control replies are **plain text** containing `<e>`. They are not
  base64-decoded before parsing.
- **Observed:** panel frames relayed by the cloud arrive as `(base64)` text, or as raw bytes
  beginning with `a`–`f` (see §4.2).

### 3.3 App → cloud messages (`makeCloudMessage`, `HomeControl.java:814-906`)

Every message has the same envelope:

```
<p><n>{AppUser}</n><u>{PushId}</u>{TYPE-SPECIFIC}<i>{DeviceId}</i><l>{AppVersion}</l><t>{AppType}</t><m>{OSName}</m><v>{OSVersion}</v><c>{NetworkType}</c><lang>{AppLang}</lang></p>
```

- No XML escaping is applied. Values are inserted as-is.
- If `AppUser`, `PushId` or `DeviceId` is empty, the function returns the string `"null"`.

| CloudMsgType | Type-specific tags | Sent when | Base64? |
|---|---|---|---|
| `MSG_SUBSCRIPTION` | `<w>{PanelId}</w>` | WebSocket opened (state `STG_OPENING`) | yes |
| `MSG_REQ_PWD` | `<j>{PanelId}</j>` | After a subscription reply | yes |
| `MSG_CONN_TO_CLOUD` | `<a>{PanelId}</a><b>{SystemName}</b><z>{PWD}</z>` | After a password reply | yes |
| `MSG_CONN_TO_PANEL` | `<a>{PanelId}</a><b>{SystemName}</b><z>{PWD}</z>` (identical) | After a `u` reply | **no** (raw XML in parentheses) |
| `MSG_ADD_PANEL` | `<s>{PanelIdToAdd}</s><b>{SystemNameToAdd}</b>` | Add-panel flow | yes |
| `MSG_DELETE_PANEL` | `<d>{PanelIdToDelete}</d><b>{SystemNameToDelete}</b>` | Delete-panel flow | yes |
| `MSG_PANEL_JSON` | `<s>{PanelId}</s><x>{inputJSON}</x><g>{userJSON}</g>` | On close, uploads input/user names (§6.9) | see §6.9 |

**`{PWD}` in `<z>`:**

- If `SaveAppPwd == 1`: `generatePassword(pwd, panelId)`. If `len(pwd) < 32`, this is the first
  32 hex characters of `SHA-256(pwd ‖ 0x00 ‖ panelId)`; otherwise `pwd` is sent unchanged
  (`HomeControl.java:770`).
- Otherwise (this app always sends `SaveAppPwd = 0`): the **plaintext panel app password**.

### 3.4 Cloud → app replies (`didReceiveMessage`, `HomeControl.java:490-762`)

- A reply is recognised by containing `<e>`.
- Command letter = `message.substring(3, 4)` (the character after `<e>`).
- Data = `message.substring(5, len - 4)`. One character after the command letter is skipped, and
  the trailing `</e>` is stripped.
- So the shape is `<e>{C}{sep}{DATA}</e>`.
- **Observed (live, 2026-11-04):**
  - The separator is a space. The cloud answers `MSG_SUBSCRIPTION` with `<e>j </e>` (letter `j`
    directly, not `w`), sometimes with data after the space. Treat `j` data as secret: in one run
    the probe's redactor matched it against a configured secret.
  - `u` arrives as `<e>u</e>`: 8 characters, no separator, no data. The app's
    `substring(5, len-4)` would throw on it, so parse from the `<e>` position and tolerate short
    forms.
- **Observed:** parsing is purely positional. `getDataFromXml` is `substring(5, len-4)`
  (`HomeControl.java:791`), which throws for replies shorter than 9 characters. **Inferred:**
  `w`/`m`/`j` replies therefore always carry at least one data character.
- **Observed:** `<e>` replies are only recognised in the cloud stages. In `STG_KEYS_PANEL` and
  `STG_DATA_PANEL`, every message other than a 1-character heartbeat goes straight to the panel
  decoder, with no `<e>` check (`HomeControl.java:512-601`). A client should still detect `<e>`
  in those states and report it.

| Letter | Meaning | App action / next state |
|---|---|---|
| `w` | Subscription info | Save data. State → `STG_REQ_PWD`; send `MSG_REQ_PWD` |
| `m` | Subscription info (alternate) | Same as `w` |
| `j` | Password / cloud data | Save data. State → `STG_REQ_CLOUD`; send `MSG_CONN_TO_CLOUD` |
| `u` | Panel connected through the relay | Send `MSG_CONN_TO_PANEL` (raw). Reset crypto. State → `STG_KEYS_PANEL`; start the panel handshake (§4) |
| `o` | OK / acknowledgement | No state change in the switch. However, see the re-send note below the table |
| `x` | Panel JSON accepted | `PANEL_JSON_ADDED_SUCCESS`; close |
| `a` | Access denied | `ERROR_ACCESS_DENIED` → fail |
| `b` | Panel busy | `ERROR_PANEL_BUSY` → fail |
| `i` | Subscription expired | `ERROR_PANEL_SUBSCRIPTION_EXPIRED` → fail |
| `n` | Panel not polling (offline) | `ERROR_PANEL_NOT_POLLING` → fail |
| other | — | `ERROR_UNKNOWN_DATA` → fail |

- **Reported (third-party, recorded 2026-11-05):** right after a goodbye and disconnect, the cloud
  has sometimes answered a new connection with `n` (not polling), then accepted one about 35 s
  later (HA-Pyronix README, troubleshooting). So `n` straight after our own session may mean "slot
  not yet released", not "panel offline". Do not retry in a tight loop; back off for ≥35 s. See
  also §13 Q11.

- **Re-send (observed, `HomeControl.java:735-757`):** after the switch, if the state is still
  `STG_REQ_PWD` or `STG_REQ_CLOUD`, the app re-sends `MSG_REQ_PWD` or `MSG_CONN_TO_CLOUD`. This
  applies to any message that leaves the state unchanged, `o` included.
- **Error letters do not close the socket** (`HomeControl.java:740`). The state goes
  `STG_PANEL_FAIL` → `STG_BEGIN`, and the progress code is reported to the UI.

**Heartbeat:** any 1-character message from the cloud (normally `" "`) makes the app send an
encrypted panel heartbeat, opcode `0x20` (`HomeControl.java:508-512`).

- **Observed (live):** the cloud heartbeat is exactly `" "`, about every 51 s; the panel answers
  the `0x20` with a `d` frame holding `" "`.
- Because frames can be split across WebSocket messages (§4.2), a 1-character message such as a
  split-off `)` is frame data. Only a single whitespace character is a heartbeat. The app's
  length test is a latent bug.

**No-reply timer:** `cloudNoReplyTimer = 300` seconds (`HomeControl.java:255`). It is armed on
connect and cancelled on every received message. On expiry it calls `noReplyFromCloud()`
(`NO_REPLY_FROM_CLOUD`).

---

## 4. Layer 2 — Encrypted panel session

Source: `com/pyronix/kotlinlibhomecontrol/PanelEncrypt.java`, `PanelEncryptKt.java`, `AES128.java`.

### 4.1 Frame format

```
offset  size  field
0       1     type   (ASCII letter, see table)
1       2     seq    (little-endian, low 16 bits of sender's sequence counter)
3       2     len    (little-endian, payload length)
5       len   payload
5+len   16    check  = SHA-256(bytes[0 .. 5+len))[0:16]   — computed over PLAINTEXT header+payload
```

Total length = `len + 21` (`makeUnencryptMsg`, `PanelEncrypt.java:960`).

| App → panel | Panel → app | Meaning | Encrypted? |
|---|---|---|---|
| `A` | `a` | Announce | no |
| `B` | `b` | ECDH public key | no |
| `C` | `c` | NACK (payload = 5-byte header of the rejected frame) | no |
| `D` | `d` | Data | **yes** |
| `E` | `e` | Encrypted goodbye | **yes** |
| `F` | `f` | Plain goodbye | no |
| `G` | `g` | Take-photo (PIR camera) | **yes** |

For encrypted types (`D d E e G g`), everything from offset 5 to the end is encrypted, **including
the 16-byte check**. Bytes 0–4 (the header) are always in the clear.

### 4.2 Transport encoding of frames

- **App → panel:** `"(" + Base64(frame) + ")"` (`encodeSendData`, `PanelEncrypt.java:465`). The
  `mOutBase64` flag defaults to `true` and is forced to `true` by `dataToPanel`. The string then
  goes out through `sendMessage2Cloud(str, false)`, which does not wrap it a second time.
  - **Exception:** `HomeControl.java:561` calls `setOutputEncoding(false)` before every receive.
    Frames sent from the receive path (NACKs) therefore go out **raw**, after a lossy UTF-8
    string conversion in `writeSendToPanel` (`HomeControl.java:1342`). New clients should always
    base64-wrap.
- **Panel → app** (`receiveMessage`, `PanelEncrypt.java:1104`), as intended:
  - Leading bytes are skipped until one is `(` or `a`–`f`.
  - If it starts with `(`: take the text up to the next `)` and base64-decode it. The header `len`
    field is not used here; the frame length comes from the decoded data.
  - Otherwise it is raw binary: frame length = `buf[3] | buf[4] << 8` + 21.
  - Nothing is parsed until at least 21 bytes are buffered.
- **App bug: the raw path does not work as written.**
  - The results of `ArraysKt.drop(...)` are thrown away (`PanelEncrypt.java:1112, 1141`). The
    leading-byte skip therefore never advances, and a raw frame is never removed from the buffer.
  - The raw length is read from signed bytes (`:1137`).
  - **Inferred:** in practice the cloud delivers panel frames as `(base64)` text. Clients should
    implement both forms correctly and log which one arrives.
- **Observed (live, 2026-11-04):** every panel frame arrived as `(base64)` text. The cloud does
  not align frames with WebSocket messages:
  - one message can carry several frames: `(…)(…)`;
  - one frame can be split across two messages: `…tL)(ZAQ…`;
  - a message can start with a space: ` (YQE…)`.

  A receiver must buffer across messages. Each `d` frame carried exactly one complete JSON
  document.

### 4.3 Sequence numbers

- App counter `mApplMsgSeq` starts at 0 and is **incremented before** each frame is built. The
  first frame therefore carries seq 1. The header holds the low 16 bits.
- Panel seq acceptance (`checkDecryptMessage`, `PanelEncrypt.java:251-280`):
  - First panel frame (`mPanelMsgSeq == 0`): seq must be **1–127**. Seq 0 falls into the general
    branch and is rejected.
  - After that: seq must be greater than the last seq and less than 128 ahead (`RX_SEQ_WRAP_GAP = 128`).
  - A 16-bit wrap is accepted when the last seq is > 65408 and the new one is < 128. Only on
    **that** frame is the full seq extended with `(last & 0xFFFF0000) + 0x10000`.
  - In the normal branch the full seq is just the 16-bit value. After a wrap, the stored seq and
    the nonce seq drop back to 16 bits on the next frame. Copy this exactly, because the nonce
    (§4.7) depends on it.
- **App bug:** the panel seq is built from **signed** bytes (`PanelEncrypt.java:260`; Kotlin
  `Byte.toUInt()` sign-extends). Any panel seq whose low byte is ≥ 0x80 becomes a huge value and
  is rejected. **Inferred:** the app fails at panel frame 128, or the panel never gets that far in
  one session. Read the seq unsigned.
- After a NACK is sent, the expected panel seq is decremented by 1.
- Max 3 consecutive NACKs in either direction (`MAX_CONSECUTIVE_NACK = 3`). After that the error
  is `MANY_NACKS` (3).
- **App bug: `sendNACK` never sends a real `C` frame** (`PanelEncrypt.java:1170-1178`;
  `makeUnencryptMsg` at `:972`).
  - The NACK payload (the rejected frame's 5-byte header) is copied into a local 26-byte array.
  - `makeUnencryptMsg` writes the check into `mTxBuffer`, and `mTxBuffer` is what gets sent.
  - The result is a re-send of the previous outbound frame with bytes 10–25 overwritten. After
    the 21-byte `A` frame, this throws an out-of-bounds exception.
  - New clients should send the intended frame: `C`, payload = the 5-byte header, plain check.

### 4.4 Handshake (`initialKeyMessages`, `PanelEncrypt.java:727-893`)

States: `BEGIN → GET_ANNOUNCE → SEND_ECDH → RECV_ECDH → DATA_TRANSFER` (`CLOSED` on failure).

1. **Before the handshake:** `setPanelInfo(PanelId, PanelPwd, WhichKey = 1)` (`HomeControl.java:741`).
   This derives `fixedKey` (§4.5).
2. **App → `A`** (announce): empty payload, 21 bytes in total.
3. **Panel → `a`.** With `p = payload` (bytes after the 5-byte header):
   - `p[1]` = modem / uplink flags (§4.6).
   - `p[2..9]` must equal the ASCII `PanelId` bytes (8 bytes). On mismatch the app sends a NACK.
   - If `p[1] & 0x80`: extra bytes `p[10 .. 10 + min(totalLen − 42, 4))` are saved as
     `announceExtra` and later mixed into the key. `announceExtra[0]` is also the **JSON version**
     (`getJsonVersion`, `PanelEncrypt.java:635-640`; 0 if there are no extra bytes).
   - `p[0]` is unused by the app.
   - The frame's check field is verified.
4. **App → `B`:** 87 bytes in total, payload length 66:
   - `payload[0] = WhichKey` (= `0x01`).
   - `payload[1..65]` = the app's new P-256 public key, **uncompressed**: `0x04 ‖ X(32) ‖ Y(32)`.
5. **Panel → `b`:** payload = 1 byte (presumably a key index; skipped) + panel public key.
   - The key is 65 bytes uncompressed (`04‖X‖Y`) or 33 bytes compressed (`02/03‖X`).
   - The app decompresses with `y = (x³ + ax + b)^((p+1)/4) mod p` (`PanelEncrypt.java:339`).
   - On `c` (NACK) the app re-sends `B` (up to 3 times). On `f` the session is closed (error 4).
6. **Session key:**
   ```
   shared  = ECDH(app_priv, panel_pub)          # 32-byte X coordinate (Java KeyAgreement "ECDH")
   digest  = SHA-256(fixedKey(16) ‖ shared(32) ‖ announceExtra(0..4))
   aes_key = digest[0:16]
   ```
   State → `DATA_TRANSFER`, and the private key is discarded.
7. **Login** (application layer, the first `D` frame; `HomeControl.java:528-548`): plaintext
   payload = `"," + UserCode + "\n"` (UTF-8). App state → `STG_DATA_PANEL`.
   - **Inferred:** a wrong user code makes the panel reply with an encrypted goodbye `e`
     (→ −2, shown as "Please check your usecode"), or with NACKs (→ −3).

### 4.5 `fixedKey` derivation (`setPanelInfo` / `calculatePasswordHash`, `PanelEncrypt.java:186, 1232`)

```
if len(PanelPwd) <= 31:
    fixedKey = SHA-256( utf8(PanelPwd) ‖ 0x00 ‖ utf8(PanelId) )[0:16]
else:                                   # "hex" password — but see note
    nibbles  = [int(c) if c in "0123456789" else 0 for c in PanelPwd[0:64]]
    fixedKey = bytes(nibbles[2i] << 4 | nibbles[2i+1] for i in 0..15)
```

**Observed (`convertHexPasswordToKey`, `PanelEncrypt.java:202-215`):** each character is converted
with `CharsKt.digitToIntOrNull(c)` with **no radix**, which means radix 10. As a result:

- `a`–`f` and `A`–`F` count as **0**, not 10–15. Only `0`–`9` contribute.
- Only the first 32 characters affect the 16-byte key.

Copy this exactly. A real hex decode gives a different key.

Further Java details that a port must copy (found while porting, 2026-11-04):

- `length()` and `charAt` count UTF-16 code units, so a password with characters outside the
  BMP (e.g. emoji) reaches the 32+ branch sooner than a character count suggests.
- `digitToIntOrNull()` accepts any Unicode decimal digit (e.g. `٣` counts as 3), not only
  `0`–`9`.
- In the short branch, `getBytes(UTF_8)` turns a lone surrogate into `?`.

**Divergence (recorded 2026-11-05):** the third-party port aiopyronix 0.1.1 (§13.1) does a true
hex decode in the long branch (`bytes.fromhex(password[:32])`, rejecting non-hex characters). That
contradicts the radix-10 behaviour above. This document follows the decompiled source. Neither
reading has been tested against a panel with a 32+ character app password.

### 4.6 Uplink / modem type from the announce (`p[1]`)

| If `p[1] & 0x80` | value | Type | | Else (bit flags) | Type |
|---|---|---|---|---|---|
| | 0x81 | USB | | `& 0x60` | WAN/LAN |
| | 0x82 | RS232 | | `& 0x02` | GPRS |
| | 0x83 | WAN/LAN | | `& 0x01` | GSM |
| | 0x84 | GSM | | `& 0x04` | PSTN |
| | 0x85 | GPRS | | | |
| | 0x86 | PSTN | | | |

> **Observed (app bug):** `switch (b3)` compares a signed byte with `case 129` … `134`
> (`PanelEncrypt.java:765`), so it never matches. In the app the modem type stays `UNKNOWN` for
> 0x81–0x86. The intended values are clearly 0x81–0x86. Ported code should compare with
> `p[1] & 0xFF`.

### 4.7 Encryption algorithm (`encryptMessage` / `decryptMessage`, `PanelEncrypt.java:373, 484`)

The scheme is **AES-128-CFB-128** (full-block cipher feedback), with an IV derived from a nonce:

```
nonce = LE32(type) ‖ LE32(len) ‖ LE32(seq_full) ‖ LE32(0)      # 16 bytes
IV    = AES-ECB_k(nonce)                                         # first 16 bytes of output
ciphertext[5:] = AES-CFB128_k(IV).encrypt(plaintext[5:])         # covers payload AND check
```

- `type` is the frame's type byte (`D`, `d`, …). `len` is the payload length (frame length − 21).
- `seq_full` is the full 32-bit sequence number of that frame. For panel frames, use the
  wrap-extended value from §4.3.
- Detail: the code calls `AES/ECB/PKCS5Padding` and keeps only the first 16 bytes of each output.
  The keystream for block *i* is `AES(feedback)`, where the feedback is the IV for the first block
  and the previous **ciphertext** block afterwards. That is exactly standard CFB-128. The extra
  padding block is ignored.
- **Decrypt then verify:** decrypt bytes 5..end, then check
  `SHA-256(frame[0 : len+5])[0:16] == frame[len+5 : len+21]`.

### 4.8 Session end

- `closeCloud()` (`HomeControl.java:342`):
  - If panel JSON is pending, it is uploaded first (§6.9).
  - Otherwise the app sends `E` (encrypted goodbye, 0-length payload) if in data mode, or `F` if
    not, then closes the WebSocket with code `1001 "User disconnect"`.
- Panel → `e`: decrypt returns −2. Panel → `f` during the key exchange: error 4 (`RECEIVE_CLOSE`).

---

## 5. Layer 3 — Panel application commands

These are sent as the plaintext payload of `D` frames (`HomeControl.java:954-1112`; opcodes in
`HomeControlKt.java`). They are only accepted in state `STG_DATA_PANEL`; otherwise the error is
`NOT_DATA_MODE`.

| Command | Opcode | Payload bytes | App usage |
|---|---|---|---|
| Login | — | `"," + UserCode + "\n"` (ASCII) | Sent automatically after the handshake |
| Heartbeat | `0x20` (32) `HEARTBEAT_MSG` | `[0x20]` | In reply to a cloud heartbeat |
| Validate code | `0x2C` (44) `SPI_VALIDATE_CODE` | **Unknown** (not called by the library) | — |
| Set (arm) areas | `0x2D` (45) `SPI_SET_AREAS` | `[0x2D, (n & 0x7F) \| (forceOmits ? 0x80 : 0), area₁ … areaₙ]` | App always uses `forceOmits = true` (`SdkManagerImpl$armAreas$2:58-62`) |
| Unset (disarm) areas | `0x2F` (47) `SPI_UNSET_AREAS` | `[0x2F, n, area₁ … areaₙ]` | |
| Operate user output | `0x31` (49) `SPI_OPERATE_USER_OUTPUT` | `[0x31, outputNo, on ? 1 : 0]` | "Automated controls" enable/disable |
| Omit (bypass) input | `0x34` (52) `SPI_OMIT_INPUT` | `[0x34, inputNo, omit ? 1 : 0]` | omit = 1, "respect" (un-omit) = 0 |
| Request log | `0x35` (53) `SPI_REQUEST_LOG_EVENTS` | `[0x35, count (1-255)]` | App requests 10 |
| Take photo (PIR cam) | — (`G` frame) | `[0x00, zoneLo, zoneHi]` | `SdkManagerImpl.java:963-972` |

- **Reported (third-party, recorded 2026-11-05):** the **non-forced** single-area forms work on
  hardware: set `[0x2D, 0x01, area]` (count 1, no `0x80` force flag) and unset
  `[0x2F, 0x01, area]` (aiopyronix `control_frame`; HA-Pyronix README, §13.1). That client also
  refuses to send a set while the area reports `V` 4 or 5. Not yet tried by us.
- Area numbers are `OcpArea.id`, the area's `R` value from panel JSON (§6.3), as one byte.
- Input and output numbers are one byte (`(byte)`), so input IDs above 255 would be truncated.
  **Unknown** whether the panel supports more than 255.

**PIR-camera photo response** (a `g` frame, decrypt result 7). The payload is reported as:

```json
{"operation":"<p[0]>", "zoneNumber":"<p[1] | p[2]<<8>", "pirResponsedCode":"<p[3]>"}
```

`pirResponsedCode` values: 0 = success, 1 = invalid zone, 255 = unspecified (`PIRCAM_PhotoResponsed`).

---

## 6. Layer 3 — Panel responses (JSON)

Decrypted `d` payloads are UTF-8 text (`HomeControl.java:559-583`):

- `" "` (one space) is a **panel heartbeat**. The library counts these and passes the count on as
  a decimal string.
- Text beginning with `{` is a JSON document. It is dispatched on its `"type"` field
  (`PanelDataObserver$parseData$1.java:92-117`).
- Anything else is logged as "Unknown format".

Field-name mapping comes from Moshi `@Json(name=…)` annotations in the model classes. Several
one-letter names showed up in jadx as unrelated library constants; they resolve as follows:
`ExifInterface.X4 = "V"`, `W4 = "A"`, `R4 = "S"`, `S4 = "E"`, `d5 = "T"`, and
`Stripe3ds2AuthResult.Ares.VALUE_CHALLENGE = "C"`.

**Value adapters:**

- **Dates** (`DateAdapter`): string format `dd/MM/yy HH:mm:ss`, in the phone's local time zone.
  If parsing fails, the current time is used.
- **Area status** (`AreaStatusAdapter`): an integer index into `OcpAreaStatus`.
- **Input state** (`InputStateAdapter`): an integer index into `OcpInput.State`.

### 6.1 Dispatch table

| `type` | Parser | Model |
|---|---|---|
| `panel` | `parsePanelInfo` | `PanelInfoModel` |
| `area` | `parseAreas` | `OcpAreaModel` |
| `input` | `parseInputs` | `OcpInputModel` |
| `log` | `parseHistory` | `OcpPanelHistoryData` |
| `output` | `parseAutomatedControls` | `AutomatedControlsData` |
| `user` | `parseUser` | `OcpUserModel` |
| `fault` | `parseFaults` | `OcpFaultsModel` |
| `applevel` | not handled by the app | — |

**Observed (live, 2026-11-04):**

- The login dump arrives about 1 s after login, in this order: `panel`, `applevel`, `area` ×3
  (two areas per page), `fault`, `input` pages, `output`, `user`. `user` was always last, so a
  client can treat it as "dump complete".
- `applevel` looks like `{"type":"applevel","level":1,"lang":"GB","panel":"Rev V10.26c …"}`.
  Its meaning is **unknown**; clients should ignore unknown types.
- String fields are space-padded: `SiteName` and `L` are often `" "`, and names can carry
  trailing spaces. Trim them.
- `00/00/00 00:00:00` means "never". The app's `DateAdapter` would turn it into "now".

### 6.2 `panel` — panel information (`ocp_about/data/models/PanelInfoModel.java`)

```json
{"type":"panel","Panel":"ABCDEFGH","SiteName":" ","Areas":6,"Inputs":76,"Outputs":30,"VersionHi":10,"VersionLo":26,"Options":1}
```

(Live shape, identifiers replaced.) `Areas`, `Inputs` and `Outputs` are the panel's
**capacity**: this panel reported 76 inputs but sent 10–15 configured ones. `Options` is
**unknown**.

| Key | Type | Meaning |
|---|---|---|
| `SiteName` | string | Site name |
| `VersionHi` / `VersionLo` | int | Firmware version, major / minor |
| `Panel` | string | Panel ID |
| `Areas` | int | Number of areas |
| `Inputs` | int | Number of inputs (zones) |
| `Outputs` | int | Number of outputs |

### 6.3 `area` — area status (`ocp_areas/data/models/OcpAreaModel.java`, `AreaDetailModel.java`)

```json
{"type":"area","isnew":true,"Areas":2,"SetType":…,
 "Detail":[{"R":1,"C":"A","N":"House","V":0,
            "LastSet":{"U":"Paul","D":"04/11/26 08:00:00"},
            "LastUnSet":{"U":"Paul","D":"04/11/26 17:30:00"},
            "Faults":{"Q":0,"List":[{"I":"text"}]}}]}
```

| Key | Type | Meaning |
|---|---|---|
| `isnew` | int | **Observed:** `1` in the login dump, `0` in pushed updates (on the wire it is an int, not a bool) |
| `Areas` | int | **Observed:** the number of entries in *this* document, not the panel total. The dump is paged two areas per document (A+B, C+D, 0+1). An update carries one area. Use `panel.Areas` for the total. |
| `SetType` | enum `SetType` | `Level` or `Area` — how the panel sets (by levels or by areas). Persisted by the app. **Observed:** sent as the string `"Level"` on a panel with "Use Level Set" enabled |
| `Detail[].S` | string | **Observed:** display text for the state, e.g. `"Unset"`, `"Set Delayed"`, `"Cannot Set"`. Not authoritative. Use `V`. |
| `Detail[].R` | int | Area ID; this is the value used in set/unset commands. **Observed:** starts at **0** (A = 0, B = 1, …) |
| `Detail[].C` | char | Area letter (A, B, C …) |
| `Detail[].N` | string | Area name |
| `Detail[].V` | int → `OcpAreaStatus` | 0 Unset, 1 Set, 2 Alarmed, 3 Setting, 4 CannotSet, 5 CanOverride, 6 SetCancelled |
| `Detail[].LastSet` / `LastUnSet` | `{U: user, D: date}` | Last set / unset, with user and time |
| `Detail[].Faults` | `{Q: count, List: [{I: text}]}` | Faults that block setting this area. **Observed:** often `{"Q":0}` with no `List`, or absent |

**Observed (Euro 46 V10.26, 2026-11-04), with nobody setting the panel:** when an input in area A
opened, the panel pushed area A with `V:5` (CanOverride), `S:"Set Delayed"` and
`Faults.List:[{"I":"Landing DT Open"}]`. When it closed, the panel pushed `V:0` (Unset). So `V:5`
means "not ready: setting would need an override", not "arming". Each change is re-sent about once
a second for 2–3 copies, so deduplicate. Unused areas report `V:4` (CannotSet). A real
set/exit-delay/set sequence has **not** been captured by us yet.

**Reported (third-party, recorded 2026-11-05):** after a set command, the area is pushed over the
open session as `V:3` (Setting) for the exit delay, about 30–40 s on their panel, then `V:1`
(Set) (HA-Pyronix README, §13.1). Their client treats `V` 1 or 3 as acceptance of a set, `V` 0 as
acceptance of an unset, and `V` 4, 5 or 6 after a set as failure (aiopyronix
`operation_confirmed`, `_settle_pending`). This fits the `OcpAreaStatus` order above. The exit
mode on their panel is not stated, so Final Door behaviour is still open (§13 Q12).

**Suggested Home Assistant `alarm_control_panel` mapping (design choice, not observed):**

| `OcpAreaStatus` | HA state |
|---|---|
| Unset | `disarmed` |
| Set | `armed_away` / `armed_night` / `armed_custom_bypass` per level (see below) |
| Setting | `arming` |
| Alarmed | `triggered` |
| CannotSet, CanOverride, SetCancelled | `disarmed`, plus a "not ready" attribute listing `Faults` |

With `SetType == "Level"` only one level can be set at a time, so model **one** alarm panel whose
armed mode is chosen by the level (e.g. A → away, B → night, C → custom), not one entity per area.

### 6.4 `input` — zones (`OcpInputModel.java`, `InputDetailModel.java`)

```json
{"type":"input","isnew":1,"Inputs":32,
 "Detail":[{"R":1,"C":0,"N":"Front Door","L":"Hall","A":{"Q":1,"R":[1]},
            "V":0,"O":true,"B":false,"S":"…"}]}
```

| Key | Type | Meaning |
|---|---|---|
| `isnew` | bool/int | Full list vs. update. The library also sees it as int `1` (`HomeControl.java:999`) |
| `Inputs` | int | Input count |
| `Detail[].R` | int | Input number; used for omit commands. **Observed:** starts at 0 |
| `Detail[].T` | string | **Observed:** input type text, e.g. `"Intruder"`, `"Final Exit"`, `"Fire"`, `"ER (Area FX)"` |
| `Detail[].C` | int? | `isPIRCam` — set if the input is a PIR camera, which supports take-photo |
| `Detail[].N` | string | Input name |
| `Detail[].L` | string | Location |
| `Detail[].A` | `{Q: count, R: [areaIds]}` | Areas this input belongs to |
| `Detail[].V` | int → `OcpInput.State` | 0 Closed, 1 Open, 2 Problem |
| `Detail[].O` | bool | Can be omitted (bypassed). **Observed:** sent as int `0`/`1` |
| `Detail[].B` | bool | Currently omitted (bypassed). **Observed:** sent as int `0`/`1` |
| `Detail[].S` | string | Free-text status |

**Inferred:** large input lists may arrive in several messages. The library collects `Detail`
entries into `completeInputArray` across messages when `isnew == 1`
(`HomeControl.java:995-1040`, `OcpInputDataProcessor`).

### 6.5 `output` — automated controls (`automatedcontrols/data/models/AutomatedControlsData.java`, `AutomatedControlModel.java`)

```json
{"type":"output","isnew":1,"Outputs":2,
 "Detail":[{"R":1,"N":"Garage Door","T":"…","P":0,"S":"…","E":1}]}
```

| Key | Type | Meaning |
|---|---|---|
| `Detail[].R` | int | Output number; used in opcode `0x31` |
| `Detail[].N` | string | Name |
| `Detail[].T` | string | Output type |
| `Detail[].P` | int | Pulse (**inferred:** momentary/pulse output vs. latched) |
| `Detail[].S` | string | Status text |
| `Detail[].E` | int | Enabled (on) state |

### 6.6 `log` — event history (`ocp_history/data/models/OcpPanelHistoryData.java`)

```json
{"type":"log","Entries":10,"Detail":[{"D":"04/11/26 08:00:00","I":"Area A set by Paul"}]}
```

### 6.7 `user` — current user's permissions (`OcpUserModel.java`)

```json
{"type":"user","U":"Paul","SetAreas":{"Q":2,"R":[1,2]},"UnsetAreas":{"Q":2,"R":[1,2]}}
```

This shows which areas the logged-in user code may set or unset. **Observed (live):** it also
carries `"Flexiset":1` (meaning **unknown**), and its area numbers are the 0-based `R` values.
**Reported (third-party, 2026-11-05):** aiopyronix uses `SetAreas.R` / `UnsetAreas.R` the same
way, to limit which areas may be set or unset.

### 6.8 `fault` — panel faults (`OcpFaultsModel.java`, `FaultDetailModel.java`)

```json
{"type":"fault","Faults":1,"Detail":[{"I":"Mains fail","A":{"Q":1,"R":[1]}}]}
```

Here `Faults` is a count (int). Inside an area entry, `Faults` is an object (§6.3): the same
key has two shapes.

### 6.9 Upload of input/user JSON to the cloud (`sendPanelJSONToCloud`, `HomeControl.java:989-1085`)

- Every non-heartbeat JSON from the panel is also passed to `sendPanelJSONToCloud`.
- The library keeps the `input` list (merged across messages) and the `user` document.
- On close it sends `MSG_PANEL_JSON` (`<s>panelId</s><x>inputJSON</x><g>userJSON</g>`).
  The cloud acknowledges with `x`.
- **Inferred:** this is how the cloud learns input and user names for push-notification text.
- A Home Assistant client does not need to do this. Whether the cloud expects it is **unknown**.

---

## 7. End-to-end connection sequence

```
HA / app                                   Cloud (wss://app.pyronixcloud.com)        Panel
   │ WebSocket open ───────────────────────────▶│
   │ (b64) <p>…<w>PanelId</w>…</p> ────────────▶│  MSG_SUBSCRIPTION
   │◀──────────────────────────── <e>w…data</e> │  (or 'm'; errors a/b/i/n)
   │ (b64) <p>…<j>PanelId</j>…</p> ────────────▶│  MSG_REQ_PWD
   │◀──────────────────────────── <e>j…data</e> │
   │ (b64) <p>…<a>Id</a><b>Name</b><z>Pwd</z>…</p) ▶ MSG_CONN_TO_CLOUD
   │◀──────────────────────────────── <e>u…</e> │  panel reachable
   │ (raw) (<p>…<a>Id</a><b>Name</b><z>Pwd</z>…</p>) ▶ MSG_CONN_TO_PANEL
   │ ( b64 'A' announce ) ─────────────────────▶│──────────────────────────────────▶│
   │◀───────────────────────────────────────────│◀──── ( 'a' announce: id, modem ) ─│
   │ ( b64 'B' 01‖04‖X‖Y ) ────────────────────▶│──────────────────────────────────▶│
   │◀───────────────────────────────────────────│◀──── ( 'b' ?‖panel pubkey ) ──────│
   │   derive aes_key = SHA256(fixedKey‖ECDH‖extra)[0:16]
   │ ( 'D' enc ",UserCode\n" ) ────────────────▶│──────────────────────────────────▶│
   │◀───────────────────────────────────────────│◀──── ( 'd' enc JSON panel/area/   │
   │                                            │        input/output/user/fault )   │
   │◀──────────────── " " (cloud heartbeat) ────│                                    │
   │ ( 'D' enc [0x20] ) ───────────────────────▶│──────────────────────────────────▶│
   │ ( 'D' enc [0x2D,n|0x80,areas…] ) set ─────▶│──────────────────────────────────▶│
   │ ( 'E' enc bye ) ──────────────────────────▶│ close 1001                         │
```

**Inferred:** the panel pushes the initial JSON set by itself after login. The library never sends
an explicit "get status" request, apart from the log request.

---

## 8. Status, progress and error codes

**`ProgressStatusTypes`** (reported to the UI through `homeControlProgress`):

| Code | Name |
|---|---|
| 0 | DISCONNECTED |
| 1 | SENT_DETAILS |
| 2 | CLOUD_CONNECTED |
| 3 | SENT_PANEL_DETAILS |
| 4 | RECEIVED_CLOUD_PWD_DATA |
| 5 | RECEIVED_CLOUD_SUBSCRIPTION_DATA |
| 6 | PANEL_HANDSHAKE |
| 7 | PANEL_DATA |
| 8 | ERROR_ACCESS_DENIED |
| 9 | ERROR_PANEL_UNKNOWN |
| 10 | ERROR_PANEL_BUSY |
| 11 | ERROR_PANEL_NOT_POLLING |
| 12 | ERROR_PANEL_COMMS |
| 13 | ERROR_PANEL_SUBSCRIPTION_EXPIRED |
| 14 | ERROR_UNKNOWN_DATA |
| 15 | ERROR_CHECK_SSL_CERT |
| 16 | SENT_OPEN_CONNECTION_REQUEST |
| 17 | NO_REPLY_FROM_CLOUD |
| 18 | PANEL_DELETED_SUCCESS |
| 19 | PANEL_ADDED_SUCCESS |
| 21 | PANEL_JSON_ADDED_SUCCESS |

**Reported (third-party, 2026-11-05):** `ERROR_PANEL_NOT_POLLING` (cloud `n`) can be transient
straight after a goodbye; a new connection was accepted about 35 s later (§3.4).

**`Errors`** (library return values):

| Code | Name |
|---|---|
| 0 | NONE |
| 1 | SEND_FAIL |
| 2 | RECEIVE_FAIL |
| 3 | MANY_NACKS |
| 4 | RECEIVE_CLOSE |
| 5 | NO_DATA (need more bytes) |
| 6 | NOT_DATA_MODE |
| 7 | PIRCAM_PHOTO_RESPONSED |

Internal `decryptReceivedMessage` values: −2 = closed or panel sent `e`; −3 = NACK limit / data
incorrect.

**`PanelMsgTypes`** (library state machine): `STG_BEGIN`, `STG_OPENING`, `STG_REQ_SUB`,
`STG_CONN_IDLE`, `STG_REQ_PWD`, `STG_REQ_PANEL`, `STG_REQ_CLOUD`, `STG_KEYS_PANEL`,
`STG_DATA_PANEL`, `STG_CLOSING`, `STG_PANEL_FAIL`, `STG_PANEL_DELETE`, `STG_REQ_DELETE`,
`STG_OPENING_DELETE`, `STG_PANEL_ADD`, `STG_REQ_ADD`, `STG_OPENING_ADD`, `STG_JSON_SENT`.

**`ConnectState`** (crypto state machine): `BEGIN`, `GET_ANNOUNCE`, `SEND_ECDH`, `RECV_ECDH`,
`DATA_TRANSFER`, `CLOSED`.

**`ModemType`:** `UNKNOWN`, `GSM`, `GPRS`, `WAN_LAN`, `USB`, `PSTN`, `RS232`.

**`JsonVersion`:** `JSON_ORIG`, `JSON_NG_V1`. `getJsonVersion()` returns `announceExtra[0]` when
there is at least one extra announce byte, and 0 otherwise (`PanelEncrypt.java:635-640`; §4.4).
**Unknown:** how the value maps onto the enum, and what changes between versions.

---

## 9. Ancillary REST API and account login

These are **not needed for panel control**. Documented for completeness.

- **Account login:**
  - AWS Amplify / Cognito user pools (`com/pyronix/cloud_auth/…/PyronixCloudUserAmplifyDataSource.java`).
  - Supports email/password, social web-UI sign-in, password reset and "sign out of all devices".
  - **Observed:** the Cognito identity is not used on the panel WebSocket.
- **REST API** (`com/hikvision/app/common/data/api/PyronixCloud.java`, Retrofit + Moshi):
  - The base URL comes from build config `AppBuild.f().d()` and was not resolved (**unknown**;
    probably a `pyronixcloud.com` host).

| Method | Path | Parameters | Purpose (inferred from name) |
|---|---|---|---|
| GET | `/v1/paneldetails` | `panelId`, `registrationId`, `deviceId` | Panel details |
| GET | `v1/requestpush` | `lastid`, `apns`, `phoneid`, + 1 int | Fetch push notification history |
| GET | `v2/image` (`Accept: image/png`) | `panelid` | Panel / installer image |
| GET | `v2/installer` | `panelid` | Installer details |
| GET | `v2/country` | `email` | User's country |
| PUT | `pircam/v1/user/{user_id}/panel/{panel_id}/device/{device_id}/images` | body | PIR-cam notification images |
| PUT | `/pircam/v1/user/{user_id}/panel/{panel_id}/device/{device_id}/delete/images` | body | Delete PIR-cam images |
| GET | `/v2/tuya/email/{email}` | — | Tuya account lookup |
| GET | `v2/tuya/user/{user_id}` | — | Tuya user data |
| POST | `v2/tuya/user/{user_id}/registration` | body | Register Tuya user |

---

## 10. Other SDKs in the app (not used for panel control)

The Pyronix app is a Hikvision-built app (`com.hikvision.app.*`, launch activity
`com.hikvision.app.features.splash.presentation.activity.SplashActivity`). Bundled native
libraries (arm64 split):

| Libraries | Vendor / purpose |
|---|---|
| `libhcnetsdk`, `libHCAlarm`, `libHCCore*`, `libHCPreview`, `libHCPlayBack`, `libHCVoiceTalk`, `libsadp`, `libPlayCtrl` | Hikvision device SDK (Hik cameras, `com.pyronix.hik_cameras`). `sadp` = Hikvision LAN discovery |
| `libezstreamclient`, `libezwatch`, `libNPClient`, `libstunClient`, `libsrt` | EZVIZ / Hik-Connect cloud streaming and P2P |
| `libThing*`, `libthing_security*` | Tuya ("Thing") smart-device SDK (`com.pyronix.smartdevices`) |
| `libCHIPController` | Matter |
| `libMNN`, `libbarhopper_v3` | On-device ML; barcode / QR scanning |

- **Observed:** the panel protocol is pure Kotlin/Java (`com.pyronix.kotlinlibhomecontrol`).
  None of the native libraries is involved.
- The app also includes `com.pairip` (Google Play anti-tamper / licence check).

---

## 11. Reference implementation notes (Python)

The sketch below follows §4. It is untested against a real panel. It also packs the type with
`<IIII`, which fails for type bytes ≥ 0x80 where Java sign-extends; harmless for the ASCII
types used. **Superseded by
`probe/pyronix_probe.py`**, which is tested offline against a port of the Java. Use the probe as
the reference.

**Verified offline (2026-11-04):** `cfb()` below gives byte-identical output to a line-for-line
Python port of the app's `encryptMessage` loop, for payload lengths 0, 1, 7, 16, 30 and 200. That
confirms §4.7 describes standard AES-CFB-128. `cryptography` warns that `modes.CFB` is moving to
`cryptography.hazmat.decrepit.ciphers.modes.CFB` (removal planned in version 49), so import it from
there in new code.

```python
import base64, hashlib, struct
from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric import ec
from cryptography.hazmat.primitives.ciphers import Cipher, algorithms, modes

def check16(b: bytes) -> bytes:
    return hashlib.sha256(b).digest()[:16]

def fixed_key(pwd: str, panel_id: str) -> bytes:
    if len(pwd) <= 31:
        return hashlib.sha256(pwd.encode() + b"\x00" + panel_id.encode()).digest()[:16]
    # App uses radix-10 digitToIntOrNull: only 0-9 count, a-f/A-F/other -> 0 (see §4.5)
    n = [int(c) if c.isascii() and c.isdigit() else 0 for c in pwd[:64]]
    return bytes((n[i] << 4) | n[i + 1] for i in range(0, 32, 2))

def build_frame(ftype: str, seq: int, payload: bytes) -> bytes:
    hdr = bytes([ord(ftype)]) + struct.pack("<HH", seq & 0xFFFF, len(payload))
    return hdr + payload + check16(hdr + payload)

def cfb(key: bytes, ftype: int, plen: int, seq: int, data: bytes, encrypt: bool) -> bytes:
    nonce = struct.pack("<IIII", ftype, plen, seq & 0xFFFFFFFF, 0)
    iv = Cipher(algorithms.AES(key), modes.ECB()).encryptor().update(nonce)
    c = Cipher(algorithms.AES(key), modes.CFB(iv))
    op = c.encryptor() if encrypt else c.decryptor()
    return op.update(data) + op.finalize()

def encrypt_frame(key, ftype, seq, payload) -> bytes:
    f = build_frame(ftype, seq, payload)
    return f[:5] + cfb(key, f[0], len(payload), seq, f[5:], True)

def decrypt_frame(key, frame: bytes, seq_full: int) -> bytes:
    plen = frame[3] | frame[4] << 8
    plain = frame[:5] + cfb(key, frame[0], plen, seq_full, frame[5:], False)
    assert check16(plain[:5+plen]) == plain[5+plen:5+plen+16]
    return plain[5:5+plen]

def wrap(frame: bytes) -> str:          # app → panel over the WebSocket
    return "(" + base64.b64encode(frame).decode() + ")"

# Handshake key
priv = ec.generate_private_key(ec.SECP256R1())
pub = priv.public_key().public_bytes(  # 0x04‖X‖Y
    serialization.Encoding.X962, serialization.PublicFormat.UncompressedPoint)
b_payload = b"\x01" + pub
# panel_pub = ec.EllipticCurvePublicKey.from_encoded_point(ec.SECP256R1(), b_payload_from_panel[1:])
# shared = priv.exchange(ec.ECDH(), panel_pub)
# aes_key = hashlib.sha256(fixed_key(pwd, pid) + shared + announce_extra).digest()[:16]
```

**Suggested Home Assistant design (design choices, not observed behaviour):**

- **Config flow inputs:** device name (`AppUser`), System ID (`PanelId`), App password, user code, plus a generated stable
  `DeviceId`. The `PushId` value needs testing (see §13).
- **Connection:** the panel allows **one remote session at a time** (§13 Q11), so a permanent
  connection locks out the HomeControl2 app. Two modes:
  - **On-demand (default):** connect, log in, read the state dump (~2 s), send goodbye `E`,
    disconnect. Do this on a poll interval (1–2 min) and for each arm/disarm. The app is blocked
    only for those few seconds. No live input updates.
  - **Persistent (opt-in):** keep the session open for live pushes (§6.3, §6.4). Re-handshake on
    drop, with back-off. Answer the cloud's `" "` heartbeat with opcode `0x20`. Locks out the app
    while connected (exact behaviour on contention: §13 Q11).
  Always end with the `E` goodbye frame; the slot frees as soon as the WebSocket closes.
  **Reported (third-party):** the cloud may still answer `n` for up to ~35 s after a goodbye
  (§3.4), so an on-demand poll or command shortly after the previous session can fail.
- **Entities:**
  - `alarm_control_panel`: one per panel on level-set systems, one per area on area systems (§6.3).
  - `binary_sensor` per input (`V == Open`), with an `omitted` attribute.
  - `switch` per output (opcode `0x31`).
  - A sensor for faults; the history log on demand.
- **Arming** uses `SPI_SET_AREAS` with force-omit (the app default). Make force-omit an option.
  The non-forced form is reported to work (§5).

---

## 12. Security observations

1. **Panel app password is sent to the cloud.** The app always sends `SaveAppPwd = 0`, so `<z>`
   carries the **plaintext** panel app password (inside TLS + base64). The cloud can therefore
   derive `fixedKey` and could in principle decrypt the session. **Inferred:** the end-to-end
   encryption protects against network observers, not against the cloud operator. With
   `SaveAppPwd = 1` only a truncated SHA-256 would be sent, but that hash is a different
   construction from `fixedKey`. **Unknown:** whether the cloud accepts this mode.
2. **No account token on the control channel.** Panel access through the relay is gated by panel
   ID, app password and user code, not by the Cognito login.
3. **Only a 128-bit truncated SHA-256 protects integrity** (no HMAC). It is inside the encryption,
   so it acts as an encrypt-then-hash MAC substitute. The check field is not keyed.
4. **The app logs secrets to stdout** — private key DER, user code, plaintext buffers
   (`System.out.println` in `PanelEncrypt`/`HomeControl`). These appear in `logcat` on debug-capable
   devices.

---

## 13. Open questions

| # | Question | How to resolve |
|---|---|---|
| 1 | ~~Does the cloud validate `PushId` / `DeviceId` / `AppUser`?~~ **Answered (2026-11-04):** a dummy `PushId`, a random `DeviceId` and a new `AppUser` were all accepted at once | — |
| 2 | ~~Separator character in `<e>{C}?{data}</e>`~~ **Answered (2026-11-04):** a space (`<e>j </e>`); `u` has none (`<e>u</e>`). Still open: meaning of the `j` data | Capture |
| 3 | ~~Is `AppUser` the account e-mail or a panel-side user name?~~ **Resolved:** it is the app's free-text "Device name" (§2). It is still **unknown** whether the cloud or panel checks it against anything. | Capture |
| 4 | ~~Wire encoding of `SetType`~~ **Answered:** the string `"Level"` | — |
| 5 | ~~Does the panel push changes spontaneously?~~ **Answered:** yes. Input and area changes arrive as `isnew:0` documents within ~1 s | — |
| 6 | Use of `p[0]` and the `b` frame's first payload byte | Capture |
| 7 | Payload format of `SPI_VALIDATE_CODE` (0x2C) | Not used by the library |
| 8 | ~~What selects `JSON_ORIG` vs `JSON_NG_V1`~~ **Partly answered:** the first announce extra byte (§4.4). Still open: what differs between the versions | Probe prints it; capture |
| 9 | REST API base URL | Resolve `AppBuild.f().d()` |
| 10 | Is a separate cloud "subscription" (`i` = expired) needed for app access? | Pyronix account terms |
| 11 | **Session exclusivity.** Observed: while the probe was connected (as a separate panel user), the phone app could not log in; it got in at once after the probe disconnected. A probe started while the app was logged in connected fine. **Unknown:** whether that dropped the app (newest wins) or the app had released the slot. **Reported (third-party, 2026-11-05):** straight after a goodbye the cloud sometimes answers `n` and accepts a new connection ~35 s later (§3.4), so the slot may not free at once | Watch the app during a probe run |
| 12 | What a real set looks like: `V` sequence through exit delay to Set, and with Final Door exit mode. **Partly answered (third-party, 2026-11-05):** `V:3` (Setting) for ~30–40 s, then `V:1` (Set), pushed over the open session (§6.3). Still needs our own capture, including Final Door exit mode | Capture while setting from the app |
| 13 | What the panel does when the login `D` frame was encrypted with a wrong app password (key mismatch): `c` NACKs, `e`, or silence? The library maps both "no `d` frame ever verifies" and "our login is NACKed to the limit" to an authentication error. **Reported (third-party, 2026-11-05):** aiopyronix treats an integrity failure on a data frame, or any `e`/`f`/`c` frame, before the first area data as an authentication failure (`_handle_panel_data`). That corroborates our inferred mapping, but it is a heuristic, not a captured trace | Capture with a deliberately wrong app password (costs a failed login; ask first) |

The cheapest way to answer most of these is to run the real app on a physical Android phone with a
debug build of `kotlinlibhomecontrol` logging. Alternatively, read `logcat`: the library prints
every WebSocket message and plaintext buffer with `System.out.println`.

### 13.1 Third-party sources

Evidence labelled **Reported (third-party)** comes from an independent implementation. We read its
code and README (2026-11-05) but did not run it, and none of it was observed by us.

| Source | Licence | Notes |
|---|---|---|
| aiopyronix 0.1.1 — <https://github.com/Herbertmt978/aiopyronix> | MIT | Async client library. Relevant code: `src/aiopyronix/client.py`, `protocol.py` |
| HA-Pyronix 0.4.1 — <https://github.com/Herbertmt978/HA-Pyronix> | MIT | Home Assistant integration on top of aiopyronix |

The HA-Pyronix README states that connect, live area states, set, unset, reconnect and disconnect
were verified on a real panel with HomeControl 2.0 app 6.3.0 and panel firmware 2.11 (from
`panel.VersionHi`/`VersionLo`; the panel model is not stated), last checked on 2 November 2026.
That firmware differs from our Euro 46 V10.26.

---

## 14. Emulator and acquisition notes

- **Play Store:** shows "This app won't work on your device" on every emulator tried:
  - API 30 / 4 KB pages / Play image, with Bluetooth, camera and microphone features present.
  - API 36 / 16 KB pages.
  - API 37.

  **Inferred:** the developer excludes emulators through the Play Console device catalogue.
- **APK acquisition:** `apkeep -a com.pyronixhc2.android.app -d google-play -o split_apk=true -e <email> -t <aas_token> <dir>`.
  This gives `base`, `config.arm64_v8a`, `config.en` and `config.xxhdpi`. The base manifest
  declares `requiredSplitTypes="base__abi,base__density"`, so all splits are needed.
- **Sideload:** `adb install-multiple apk/*.apk` succeeds on the API 30 arm64 emulator.
- **Launch:** fails with *"Activity class … SplashActivity does not exist"*, even though the package
  manager lists the activity. **Inferred:** related to `com.pairip` (Play licence / integrity
  protection) on a sideloaded install. Not investigated further.
- **Required hardware features** (manifest): `bluetooth`, `bluetooth_le`, `camera`,
  `camera.autofocus`, `faketouch`, `location`, `microphone`, `screen.landscape`,
  `screen.portrait`, `wifi`.

---

## 15. Source index

| Topic | File (under `decompiled/sources/`) |
|---|---|
| WebSocket, cloud messages, state machine, commands | `com/pyronix/kotlinlibhomecontrol/HomeControl.java` |
| Opcodes, API version | `com/pyronix/kotlinlibhomecontrol/HomeControlKt.java` |
| Framing, handshake, crypto | `com/pyronix/kotlinlibhomecontrol/PanelEncrypt.java` |
| Frame header letters, base64 helpers | `com/pyronix/kotlinlibhomecontrol/PanelEncryptKt.java` |
| AES wrapper | `com/pyronix/kotlinlibhomecontrol/AES128.java` |
| Enums | `…/kotlinlibhomecontrol/{CloudMsgTypes,PanelMsgTypes,ConnectState,ModemType,JsonVersion,Errors,ProgressStatusTypes,PIRCAM_PhotoResponsed}.java` |
| App ↔ library glue, connect JSON, command callers | `com/hikvision/app/features/ocp/common/data/datasources/SdkManagerImpl*.java` |
| JSON dispatch and parsers | `com/hikvision/app/features/ocp/common/data/datasources/PanelDataObserver*.java` |
| JSON models | `com/hikvision/app/features/ocp/{ocp_about,ocp_areas,ocp_history,automatedcontrols}/data/models/*.java` |
| Enum / date adapters | `com/hikvision/app/features/ocp/ocp_areas/data/adapters/*.java`, `…/common/data/json_adapters/DateAdapter.java` |
| Domain enums (area status, input state, set type) | `com/hikvision/core/features/paneloverview/domain/entities/{OcpArea,OcpInput,SetType}.java` |
| REST API | `com/hikvision/app/common/data/api/PyronixCloud.java` |
| Account login (Cognito) | `com/pyronix/cloud_auth/**` |
