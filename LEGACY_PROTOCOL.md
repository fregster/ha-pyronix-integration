# Pyronix Euro panels: legacy configuration link (overview)

This document gives an overview of the older, direct link between a Pyronix Euro-series panel
and installer PC software, which predates the cloud relay. It covers only what is needed to place this
link next to the cloud protocol in [PROTOCOL.md](PROTOCOL.md) and to judge whether a direct
connection is worth pursuing. It is not a wire-level specification.

The Home Assistant integration does **not** use this link. It talks to the panel through the
Pyronix Cloud relay described in PROTOCOL.md.

## Provenance

The information here comes from studying Pyronix's installer configuration software, InSite
Access 11.5b (UK), for the sole purpose of finding interface information that an independent
program needs to interoperate with a Euro panel (Copyright, Designs and Patents Act 1988,
s.50B and s.50BA). The text is our own description. No vendor code, data files, tables or
documentation are reproduced, and the vendor's per-firmware configuration data is
deliberately left out.

**Evidence labels**

- **Observed (vendor software)**: seen directly while studying the configuration software.
- **Observed (vendor documentation)**: stated in the Euro 46 V10 installation manual or
  communications guide.
- **Observed (live)**: confirmed against a real panel, with the date.
- **Inferred**: reasoned from observed facts but not confirmed.
- **Unknown**: not established.

## 1. Roles and terminology

The PC software is an installer tool. It reads a panel's programmed configuration into a local
per-site database, lets an engineer edit it, and writes it back. **Observed (vendor software).**

The vendor's terms run in the opposite direction to what many people expect:

| Vendor term | Direction | Meaning |
|---|---|---|
| Upload | panel → PC | Read the panel's configuration (and logs) into the PC |
| Download | PC → panel | Write configuration from the PC into the panel |

"Download" never means a firmware update in this software (see §5).

## 2. Physical links

The same configuration session can run over several carriers. **Observed (vendor software)**
unless marked otherwise.

| Link | Notes |
|---|---|
| Direct serial | RS-232, or USB through a virtual COM port driver. The host opens the port with no XON/XOFF and no CTS/DSR handshaking, and asserts DTR and RTS. The software lets the user set line speed, data bits, parity and stop bits, so the panel's expected settings are **Unknown** here. |
| PSTN modem | The PC dials the panel's on-board modem, or the panel calls the PC ("dial-in"). The host's modem init strings force ITU-T V.22 at 1200 bit/s (V.22bis at 2400 bit/s is also offered). Phone-line options include PABX prefix digits, pulse/tone dialling, rings before answer, timeout and retries. |
| TCP ("download over IP") | A TCP connection to a configured host and port, with a separate receiver service on the PC side. **Inferred:** it carries the same session as the serial/modem link. **Unknown:** whether it is plain TCP or adds its own framing, and which side connects. Do not assume the PC software can reach a plain serial-to-network bridge this way. To use such a bridge, present it to the PC software as a serial port through a virtual COM port driver, which works with any serial program. |
| IP communicators | Communicator modules (for example WebWay One, Chiron) appear as alternative "modem" types. **Inferred:** they tunnel the same session. |
| Pyronix Cloud | Offered as a link type for recent panels. **Inferred:** related to the cloud relay in PROTOCOL.md, but how the two relate is **Unknown**. |

### 2.1 Euro 46 V10 with a DIGI-LAN module

This section records which of the links above a Euro 46 V10 fitted with the DIGI-LAN Ethernet
module actually offers, and whether any of them reach the panel without the cloud.

- **Configuration-session link ("Download by"):** the panel offers only *RS-232* or *Cloud*.
  No local IP option is documented. **Observed (vendor documentation).**
- **The module's own network settings:** automatic or manual addressing, IP address, subnet
  mask, router and two DNS servers. The configuration software holds these as one 22-byte
  record. That size fits five IPv4 addresses plus flags, and leaves no room for a server
  address or port. **Observed (vendor software)**; the field breakdown is **Inferred**.
- **What the module connects to:** its diagnostics report "Polling Cloud" (app and installer
  software) and "Polling ARC" (alarm reporting to a monitoring centre). Both are outbound.
  **Observed (vendor documentation).**
- **Monitoring-centre reporting:** set up with telephone numbers only, although the formats
  (Fast Format, Contact ID, SIA) are listed with "IP variants". The configuration software has
  no IP or port fields for it. **Inferred:** IP reporting from this module travels through
  the Pyronix Cloud and is not addressable locally.
- **Call-back to the PC:** the panel stores four call-back entries of 20 bytes each. Each has
  flags for alarms, faults, set/unset and access-control events, so the panel can contact the
  PC when those events happen. The IP address and port fields exist only in the PC's own
  database, not in the panel's record. **Inferred:** on this panel a call-back is a telephone
  call, not a LAN connection.
- **Firewall ports:** the DIGI-LAN leaflet says that behind a restrictive firewall, TCP 25010
  ("Insite UDL"), 25000 ("panel") and 443 ("App") must be opened "to allow UDL and use of the
  HomeControl+ APP". **Observed (vendor documentation).** **Inferred:** these are the module's
  outbound connections to the Pyronix Cloud, one per service. Installer sessions over this
  module therefore go through the cloud.
- **Inbound connections:** none. A live TCP scan found no listening ports, including 25000 and
  25010. **Observed (live,
  2026-11-05)**; see PROTOCOL.md §1.

**Conclusion (Inferred).** On a Euro 46 V10 with DIGI-LAN, the only cloud-free link to the panel
is the RS-232 port. A network bridge (RS-232 to Wi-Fi or Ethernet) can carry it, because the
host treats the port as a plain byte pipe with no flow control (§6, L3). The DIGI-LAN module
uses a separate connector, so cloud and app access can stay in place. However, "Download by"
is a single setting, and switching it to RS-232 may stop installer access through the cloud
(see L5).

## 3. Session types

The software runs each operation as a sequence of steps. Every sequence starts by identifying
the panel's type and firmware version, because the configuration layout depends on both
(§4). **Observed (vendor software).** Operations include:

- read configuration (upload);
- write configuration (download), including first reading the panel's limits;
- read the event logs;
- live diagnostics (zone states, wireless devices, keypads, bells);
- remote set and unset of areas;
- a test call, and handling calls the panel makes to the PC.

## 4. Configuration model

**Observed (vendor software).** Panel configuration is held in the panel's non-volatile memory
as a set of **fixed-size records grouped by object type**. Examples are zones, zone and area
text, users and user codes, keypads, expanders, outputs, timers, responses, alarm-receiver
(ARC) reporting, modem options, autoset schedules, SMS and voice messaging, and on newer
firmware the app, LAN and Wi-Fi settings. The PC reads or writes each group as a unit. The
record size and record count for each group depend on the panel model and firmware version.

Consequences for an independent client:

- Identify the panel type and firmware version before interpreting any configuration data.
- Do not assume a layout carries over between firmware versions. Each version has its own.
- Capacities change between versions. For example, the PC software lists Euro 46 firmware
  V10.x with a higher maximum zone count (76) than earlier versions (46). **Observed (vendor
  software).**

Euro 46 firmware versions the software knows: V7.10, V8.00, V9.10, V9.53, V10.01 and V10.20,
plus an "App" variant. **Observed (vendor software).** The panel used for PROTOCOL.md's live
tests runs V10.26, which that list does not include.

## 5. Firmware

The configuration software contains no means of updating panel firmware and carries no firmware
images. **Observed (vendor software).** How Euro panel firmware is updated is **Unknown**
and out of scope for this project.

## 6. Unknowns

| # | Question | Status |
|---|---|---|
| L1 | Frame format: start/end markers, length, command codes, checksum, acknowledgement and retry. | **Partly answered.** The PC-side byte-transport pipeline is now mapped from the configuration software: a dedicated poll thread drives overlapped serial I/O through two 500-byte byte FIFOs (one per direction) with no flow control — a raw byte pipe. The **application frame** on top of it (markers, opcode set, length, checksum, ACK/retry) is assembled at run time by an unlabelled state machine and is **not** recoverable as static data; it was not reconstructed. |
| L2 | How the session authenticates (engineer code, UDL password or site identifier). | Unknown. |
| L3 | Serial line settings the panel expects on its direct port. | **Partly answered (PC side).** The host opens the port with no flow control and offers the full generic rate list; the Euro 46 *default* line rate is not fixed in the software (it is set in the panel or the USB-serial cable). |
| L4 | Whether the TCP and cloud links carry the session unchanged or re-encapsulate it. | Unknown. |
| L5 | Whether a direct link can run while a cloud session is open (compare PROTOCOL.md §11 and §13 Q11). | Unknown. |

**Cheapest way to finish L1.** The configuration software has a built-in diagnostic that logs
every byte it sends and receives at the port boundary, framing included. Enabling it during one
authorised session with a real panel would capture the exact frames directly — far cheaper and
safer than more static analysis. The address-level transport findings are recorded in the
gitignored `reference/uk_insite_v11.5b/ANALYSIS.md` §5.

Answer these only by observing your own panel, label them **Observed (live)** with the date,
and follow the safety rules in [AGENTS.md](AGENTS.md). A configuration write changes the
panel's programming and counts as a state-changing command.
