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
"""

PREFER = "frodhes"
PORT_KEYS = ("frodhes", "felucca", "fm-1")   # Frodhes since the rename; Felucca before; fm-1 the stock name
NEVER = ("textura",)                         # another unit's firmware: never opened, never probed


def _never(name):
    n = name.lower()
    return any(k in n for k in NEVER)


def is_fm1(name, keys=PORT_KEYS):
    n = name.lower()
    return not _never(name) and any(k in n for k in keys)


def pick(names, keys=PORT_KEYS):
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
