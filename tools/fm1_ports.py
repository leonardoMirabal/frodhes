#!/usr/bin/env python3
# SPDX-License-Identifier: GPL-3.0-only
"""Which MIDI port / USB audio device is the Frodhes FM-1, when other FM-1s may share the computer.

A "Frodhes" name wins: when one exists, only the Frodhes ports are candidates. The older names ("felucca", "fm-1")
are a fallback for when no Frodhes port exists (just after an install from the stock firmware or the rescue).
NEVER names are other firmware on another unit and are never matched, not even as a fallback.

  pick(names)        the candidate names, Frodhes ones only when any exist (keeps the order given)
  first(names)       pick(names)[0], or None
  devices(devs, ch)  the indices of sounddevice.query_devices() entries picked the same way, with >= ch inputs; with
                     a location pinned (pin_audio(loc), FM1_LOCATION=0x...) only the one at that USB location
                     (CoreAudio UID); two FM-1 audio devices without one: refused
  coreaudio_devices() [(name, UID)] in CoreAudio's order (= sounddevice's); uid_loc(uid) the USB location in a UID
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
  midi_at(loc)       ([(index, name)] MIDI sources, [(index, name)] destinations) of the USB device at locationID loc
                     (CoreMIDI's USBLocationID on the endpoint's device; index = python-rtmidi's port index, the same
                     CoreMIDI order), None off macOS: two units of one name told apart by where they are plugged in
  midi_names_at(loc) (input names, output names) of the unit at loc as python-rtmidi / mido name them (midi_at's
                     indices into rtmidi's lists; None when an index is past them or there is no CoreMIDI)
  usb_name_at(loc, devs)  the USB product name at loc (ioreg), None when nothing is there
  fm1_usb_at(loc, devs)   that name, when it is an FM-1's (a Frodhes / FM-1 product, never NEVER), else None
"""
import os
import re
import subprocess
import sys

PREFER = "frodhes"
PORT_KEYS = ("frodhes", "felucca", "fm-1")   # Frodhes since the rename; Felucca before; fm-1 the stock name
NEVER = ("textura",)                         # another unit's firmware: never opened, never probed


FORCED = os.environ.get("FM1_PORT") or None  # force(); FM1_PORT=NAME does the same for every tool
AUDIO_LOC = int(os.environ["FM1_LOCATION"], 0) if os.environ.get("FM1_LOCATION") else None   # pin_audio()


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


def midi_at(loc):
    """([(i, name)] sources, [(i, name)] destinations) of the device at USB locationID loc (CoreMIDI, read only: the
    server's objects, nothing sent to a device); None when CoreMIDI is not there"""
    import ctypes
    import ctypes.util
    lib, cfl = ctypes.util.find_library("CoreMIDI"), ctypes.util.find_library("CoreFoundation")
    if not lib or not cfl:
        return None
    cm, cf = ctypes.cdll.LoadLibrary(lib), ctypes.cdll.LoadLibrary(cfl)
    u32, ul, vp = ctypes.c_uint32, ctypes.c_ulong, ctypes.c_void_p
    cf.CFStringCreateWithCString.restype, cf.CFStringCreateWithCString.argtypes = vp, [vp, ctypes.c_char_p, u32]
    cf.CFStringGetCString.argtypes = [vp, ctypes.c_char_p, ctypes.c_long, u32]
    cf.CFRelease.argtypes = [vp]
    for fn in ("MIDIGetSource", "MIDIGetDestination"):
        getattr(cm, fn).restype, getattr(cm, fn).argtypes = u32, [ul]
    cm.MIDIGetNumberOfSources.restype = cm.MIDIGetNumberOfDestinations.restype = ul
    cm.MIDIEndpointGetEntity.argtypes = cm.MIDIEntityGetDevice.argtypes = [u32, ctypes.POINTER(u32)]
    cm.MIDIObjectGetIntegerProperty.argtypes = [u32, vp, ctypes.POINTER(ctypes.c_int32)]
    cm.MIDIObjectGetStringProperty.argtypes = [u32, vp, ctypes.POINTER(vp)]
    utf8 = 0x08000100
    k_loc, k_name = (cf.CFStringCreateWithCString(None, k, utf8) for k in (b"USBLocationID", b"name"))

    def name(o):
        r = vp()
        if cm.MIDIObjectGetStringProperty(o, k_name, ctypes.byref(r)) or not r.value:
            return ""
        b = ctypes.create_string_buffer(512)
        cf.CFStringGetCString(r, b, 512, utf8)
        cf.CFRelease(r)
        return b.value.decode("utf-8", "replace")

    def at(n, get):
        out = []
        for i in range(n):
            e, ent, dev, v = get(i), u32(), u32(), ctypes.c_int32()
            if cm.MIDIEndpointGetEntity(e, ctypes.byref(ent)) or cm.MIDIEntityGetDevice(ent.value, ctypes.byref(dev)):
                continue
            if not cm.MIDIObjectGetIntegerProperty(dev.value, k_loc, ctypes.byref(v)) and (v.value & 0xFFFFFFFF) == loc:
                out.append((i, name(dev.value) or name(e)))
        return out
    return at(cm.MIDIGetNumberOfSources(), cm.MIDIGetSource), at(cm.MIDIGetNumberOfDestinations(), cm.MIDIGetDestination)


def midi_names_at(loc, ins=None, outs=None, at=None):
    """(input names, output names) of the unit at loc: midi_at's CoreMIDI indices looked up in python-rtmidi's lists
    (rtmidi's port i = CoreMIDI endpoint i; mido reorders its output list, so mido's lists are never indexed);
    None without CoreMIDI or when an index is past the list (a port came or went meanwhile)"""
    m = midi_at(loc) if at is None else at
    if m is None:
        return None
    if ins is None or outs is None:
        import rtmidi
        ins, outs = rtmidi.MidiIn().get_ports(), rtmidi.MidiOut().get_ports()
    if any(i >= len(ins) for i, _ in m[0]) or any(i >= len(outs) for i, _ in m[1]):
        return None
    return [ins[i] for i, _ in m[0]], [outs[i] for i, _ in m[1]]


def usb_name_at(loc, devs=None):
    devs = usb_devices() if devs is None else devs
    m = [n for n, l in devs or () if l == loc]
    return m[0] if len(m) == 1 else None


def fm1_usb_at(loc, devs=None):
    """the USB product name at loc when it is an FM-1 (by the USB product, not CoreMIDI's cached port name)"""
    n = usb_name_at(loc, devs)
    if n is None or _never(n) or not any(k in n.lower() for k in PORT_KEYS):
        return None
    return n


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


def devices(devs, ch=2, keys=PORT_KEYS, ca=None):
    """the FM-1's audio inputs among sounddevice's devs. Two units share the name "Frodhes FM-1": with a location
    pinned (pin_audio, a tool's --location, FM1_LOCATION=0x... for every tool) only the device at that USB location
    (CoreAudio's device UID); without one, two FM-1 audio devices are refused (SystemExit), never one taken at
    random. ca: coreaudio_devices() (the tests)"""
    devs = list(devs)
    ok = [i for i, d in enumerate(devs) if d["max_input_channels"] >= ch]
    names = pick([devs[i]["name"] for i in ok], keys)
    sel = [i for i in ok if devs[i]["name"] in names]
    if AUDIO_LOC is None and len(sel) < 2:
        return sel
    ca = coreaudio_devices() if ca is None else ca
    if ca is None or [n for n, _ in ca] != [d["name"] for d in devs]:
        if AUDIO_LOC is not None:
            sys.exit(f"--location 0x{AUDIO_LOC:08x}: the audio devices' USB locations cannot be read (CoreAudio's "
                     "list is not sounddevice's): no audio device taken")
        sys.exit(f"{len(sel)} FM-1 audio devices ({', '.join(devs[i]['name'] for i in sel)}) and their USB locations "
                 "cannot be read: give the unit's --location (or FM1_LOCATION=0x...)")
    if AUDIO_LOC is None:
        where = ", ".join(f"{devs[i]['name']!r} at " + (f"0x{uid_loc(ca[i][1]):08x}" if uid_loc(ca[i][1]) is not None
                                                        else "?") for i in sel)
        sys.exit(f"{len(sel)} FM-1 audio devices ({where}): give the unit's --location (or FM1_LOCATION=0x...)")
    return [i for i in sel if uid_loc(ca[i][1]) == AUDIO_LOC]


def pin_audio(loc):
    """devices() takes only the audio device at USB location loc from now on (None: off); child processes too"""
    global AUDIO_LOC
    AUDIO_LOC = loc
    if loc is None:
        os.environ.pop("FM1_LOCATION", None)
    else:
        os.environ["FM1_LOCATION"] = f"0x{loc:08x}"


def uid_loc(uid):
    """the USB locationID in a CoreAudio USB device UID ("AppleUSBAudioEngine:maker:product:20130000:2"; leading
    zeros dropped: "...:100000:2"); a device with a serial number has the serial there (None, or a number that
    matches no FM-1's location)"""
    if not uid or not uid.startswith("AppleUSBAudioEngine:"):
        return None
    p = uid.rsplit(":", 2)
    return int(p[1], 16) if len(p) == 3 and re.fullmatch(r"[0-9A-Fa-f]{1,8}", p[1]) else None


def coreaudio_devices():
    """[(name, UID)] of CoreAudio's devices in its own order (PortAudio's, so sounddevice's indices), read only;
    None off macOS"""
    import ctypes
    import ctypes.util
    lca, lcf = ctypes.util.find_library("CoreAudio"), ctypes.util.find_library("CoreFoundation")
    if not lca or not lcf:
        return None
    ca, cf = ctypes.cdll.LoadLibrary(lca), ctypes.cdll.LoadLibrary(lcf)
    u32, vp = ctypes.c_uint32, ctypes.c_void_p

    class Addr(ctypes.Structure):
        _fields_ = [("sel", u32), ("scope", u32), ("elem", u32)]

    def cc4(t):
        return int.from_bytes(t.encode(), "big")

    ca.AudioObjectGetPropertyDataSize.argtypes = [u32, ctypes.POINTER(Addr), u32, vp, ctypes.POINTER(u32)]
    ca.AudioObjectGetPropertyData.argtypes = [u32, ctypes.POINTER(Addr), u32, vp, ctypes.POINTER(u32), vp]
    cf.CFStringGetCString.argtypes = [vp, ctypes.c_char_p, ctypes.c_long, u32]
    cf.CFRelease.argtypes = [vp]
    a, n = Addr(cc4("dev#"), cc4("glob"), 0), u32()
    if ca.AudioObjectGetPropertyDataSize(1, ctypes.byref(a), 0, None, ctypes.byref(n)):
        return None
    ids = (u32 * (n.value // 4))()
    if ca.AudioObjectGetPropertyData(1, ctypes.byref(a), 0, None, ctypes.byref(n), ids):
        return None

    def prop(dev, sel):
        q, r, sz = Addr(cc4(sel), cc4("glob"), 0), vp(), u32(ctypes.sizeof(vp))
        if ca.AudioObjectGetPropertyData(dev, ctypes.byref(q), 0, None, ctypes.byref(sz), ctypes.byref(r)) or not r.value:
            return None
        b = ctypes.create_string_buffer(1024)
        cf.CFStringGetCString(r, b, 1024, 0x08000100)
        cf.CFRelease(r)
        return b.value.decode("utf-8", "replace")
    return [(prop(d, "lnam") or "", prop(d, "uid ")) for d in ids]
