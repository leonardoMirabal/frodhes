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
