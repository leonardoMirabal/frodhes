#!/usr/bin/env python3
# SPDX-License-Identifier: GPL-3.0-only
"""Which MIDI port / USB audio device is the Frodhes FM-1, when other FM-1s may share the computer.

A "Frodhes" name wins: when one exists, only the Frodhes ports are candidates. The older names ("felucca", "fm-1")
are a fallback for when no Frodhes port exists (just after an install from the stock firmware or the rescue).
NEVER names are other firmware on another unit and are never matched, not even as a fallback.

  pick(names)        the candidate names, Frodhes ones only when any exist (keeps the order given)
  first(names)       pick(names)[0], or None
  devices(devs, ch)  the indices of sounddevice.query_devices() entries picked the same way, with >= ch inputs
  is_fm1(name)       the name is an FM-1 port by any key (a guard: "is this port the FM-1's own?")
  force(name, loc)   from now on pick()/devices() take only the names that contain `name` (a tool's --port, for
                     a second unit with its own USB name, tools/build.py --usb-name); nothing else is ever matched.
                     Refuses (SystemExit) when two USB devices answer to that name (the MIDI ports cannot tell
                     them apart), or when loc (--location) is given and the one unit of that name is not there.
  usb_devices()      [(USB product name, locationID)] of the connected USB devices (macOS ioreg; None elsewhere)
  usb_match(n, devs) the devices whose product name is the port name n (or one starts with the other)
  stale_match(n, loc, devs, ports, ids)
                     the device at loc for a port whose name matches no USB device (CoreMIDI keeps a port's old
                     name after the unit's USB name changed), only when every other port is accounted for: see there
  usb_ids()          {locationID: (idVendor, idProduct)} of the connected USB devices (macOS ioreg; None elsewhere)
"""
import os
import re
import subprocess
import sys

PREFER = "frodhes"
PORT_KEYS = ("frodhes", "felucca", "fm-1")   # Frodhes since the rename; Felucca before; fm-1 the stock name
NEVER = ("textura",)                         # another unit's firmware: never opened, never probed


FORCED = os.environ.get("FM1_PORT") or None  # force(); FM1_PORT=NAME does the same for every tool


def force(name, loc=None, devs=None):
    """only the ports / audio devices whose name contains `name` from now on (None: the usual choice); the unit
    checked on USB first (devs: usb_devices(), for the tests): exactly one device of that name, at loc if given"""
    global FORCED
    if name:
        devs = usb_devices() if devs is None else devs
        if devs is None:
            if loc is not None:
                sys.exit(f"--location 0x{loc:08x}: no USB device list on this computer (macOS ioreg)")
        else:
            m = usb_match(name, devs)
            if not m and loc is not None:     # CoreMIDI's name may be stale: the unit by exclusion (stale_match)
                m = stale_match(name, loc, devs, midi_inputs(), usb_ids())
                if m:
                    print(f"--port {name!r}: no USB device of that name; taken as {m[0][0]!r} at 0x{loc:08x} (every "
                          "other port is matched elsewhere: CoreMIDI keeps the old name)", file=sys.stderr)
            where = ", ".join(f"{n!r} at 0x{l:08x}" for n, l in m) or "none"
            if len(m) > 1:
                sys.exit(f"--port {name!r}: {len(m)} USB devices answer to that name ({where}); their MIDI ports "
                         "cannot be told apart: give one its own USB name (tools/build.py --usb-name)")
            if loc is not None and [l for _, l in m] != [loc]:
                sys.exit(f"--port {name!r} --location 0x{loc:08x}: that unit is not there (found: {where})")
    FORCED = name or None
    if FORCED:
        os.environ["FM1_PORT"] = FORCED       # (the tools' child processes too)
    else:
        os.environ.pop("FM1_PORT", None)


def midi_inputs():
    """the MIDI input port names (mido), [] when mido is missing"""
    try:
        import mido
        return list(mido.get_input_names())
    except Exception:
        return []


def parse_loc(text):
    """--location: 0x00100000 or 1048576 (ioreg's locationID)"""
    return int(text, 0)


def usb_devices():
    """[(USB product name, locationID)] (macOS: ioreg -p IOUSB -l), None when there is no such list"""
    if sys.platform != "darwin":
        return None
    try:
        txt = subprocess.run(["ioreg", "-p", "IOUSB", "-l", "-w0"], capture_output=True, text=True,
                             timeout=10).stdout
    except (OSError, subprocess.SubprocessError):
        return None
    return parse_ioreg(txt)


def usb_ids():
    """{locationID: (idVendor, idProduct)} (macOS ioreg), None when there is no such list"""
    if sys.platform != "darwin":
        return None
    try:
        txt = subprocess.run(["ioreg", "-p", "IOUSB", "-l", "-w0"], capture_output=True, text=True,
                             timeout=10).stdout
    except (OSError, subprocess.SubprocessError):
        return None
    return parse_ioreg_ids(txt)


def parse_ioreg_ids(txt):
    out = {}
    for node in re.split(r"\n[ |]*\+-o ", txt)[1:]:
        loc = re.search(r'"locationID" = (\d+)', node)
        vid = re.search(r'"idVendor" = (\d+)', node)
        pid = re.search(r'"idProduct" = (\d+)', node)
        if loc and vid and pid:
            out[int(loc.group(1))] = (int(vid.group(1)), int(pid.group(1)))
    return out


def parse_ioreg(txt):
    out = []
    for node in re.split(r"\n[ |]*\+-o ", txt)[1:]:
        name = re.search(r'"USB Product Name" = "([^"]*)"', node)
        loc = re.search(r'"locationID" = (\d+)', node)
        if name and loc:
            out.append((name.group(1), int(loc.group(1))))
    return out


def usb_match(name, devs):
    """the USB devices of the MIDI port `name`: the same product name, or one starting with the other ("Textura" /
    "Textura FM-1"), case aside"""
    n = name.lower().strip()
    return [(d, l) for d, l in devs
            if d.lower() == n or (len(n) >= 4 and (d.lower().startswith(n) or n.startswith(d.lower())))]


VIRTUAL = ("iac driver", "network", "session ", "bluetooth")   # ports with no USB device of their own


def stale_match(name, loc, devs, ports, ids=None):
    """--location given and the port `name` matches no USB device by name: CoreMIDI keeps a port's old name after the
    unit's USB name changed (a new image with another USB product name), so the name cannot pin the unit. The port is
    taken as the device at loc only by exclusion, and only when all of these hold (else []):
      - the port `name` matches no USB device at all;
      - exactly one USB device is at loc, and no port carries its name (else that port is the unit's own);
      - every other port matches a USB device by name, at another location, or is a virtual port (IAC, network,
        Bluetooth), or (with ids only) has no USB device by that name at all (a driver's port, a device of another
        kind): no other port is at loc;
      - no other device of the loc device's USB name is left without a port;
      - with ids (usb_ids()): every other device of the loc device's vendor / product id is one of those matched
        devices (no second unit without a port of its own that the stale port could belong to; an FM-1 has one
        port, so the stale port is the loc unit's or another kind of device's, and the installer's handshake then
        finds no FM-1 identity and writes nothing).
    The other units' ports, matched by their own names at their own locations, are never this one."""
    if loc is None or usb_match(name, devs):
        return []
    here = [(d, l) for d, l in devs if l == loc]
    if len(here) != 1:
        return []
    others = [p for p in dict.fromkeys(ports) if p != name]
    if any(usb_match(p, here) for p in others):
        return []
    matched = set()
    for p in others:
        m = usb_match(p, devs)
        if not m:
            if any(k in p.lower() for k in VIRTUAL) or ids is not None:
                continue                      # (with ids: every unit of the loc device's kind is matched below, so
                                              # a port without a device is not another such unit's)
            return []                         # another port without a device: the stale one could be either
        if any(l == loc for _, l in m):
            return []
        matched |= {l for _, l in m}
    if any(d.lower() == here[0][0].lower() and l != loc and l not in matched for d, l in devs):
        return []                             # another device of that USB name without a port: either could be it
    if ids is not None:
        kind = ids.get(loc)
        if kind is None:
            return []
        if any(k == kind and l != loc and l not in matched for l, k in ids.items()):
            return []
    return here


def _never(name):
    n = name.lower()
    return any(k in n for k in NEVER)


def is_fm1(name, keys=PORT_KEYS):
    n = name.lower()
    if FORCED:
        return FORCED.lower() in n
    return not _never(name) and any(k in n for k in keys)


def pick(names, keys=PORT_KEYS):
    if FORCED:
        return [n for n in names if FORCED.lower() in n.lower()]
    names = [n for n in names if not _never(n)]
    mine = [n for n in names if PREFER in n.lower()]
    return mine if mine else [n for n in names if any(k in n.lower() for k in keys)]


def first(names, keys=PORT_KEYS):
    p = pick(names, keys)
    return p[0] if p else None


def devices(devs, ch=2, keys=PORT_KEYS):
    devs = list(devs)
    ok = [i for i, d in enumerate(devs) if d["max_input_channels"] >= ch]
    names = pick([devs[i]["name"] for i in ok], keys)
    return [i for i in ok if devs[i]["name"] in names]
